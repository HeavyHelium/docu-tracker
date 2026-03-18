import json
import os
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import quote, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from docu_tracker.analyzer import analyze_document
from docu_tracker.config import load_config
from docu_tracker.db import Database
from docu_tracker.extractor import extract_text
from docu_tracker.scanner import compute_file_hash, scan_directory

ASSET_DIR = Path(__file__).resolve().parent / "webui"
MAX_WORKERS = 4
VALID_STATUSES = ["unread", "reading", "read", "needs_review"]


class HTTPError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


@contextmanager
def database_for_path(db_path):
    db = Database(db_path)
    db.initialize()
    try:
        yield db
    finally:
        db.close()


def parse_since(value):
    import re

    match = re.fullmatch(r"(\d+)([hdwm])", value.strip().lower())
    if not match:
        raise HTTPError(400, f"Invalid duration '{value}'. Use 7d, 2w, 24h, or 1m.")
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    elif unit == "w":
        delta = timedelta(weeks=amount)
    else:
        delta = timedelta(days=amount * 30)
    return datetime.now(timezone.utc) - delta


def _read_asset(name):
    path = ASSET_DIR / name
    return path.read_bytes()


def _json_response(start_response, status_code, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(
        f"{status_code} {HTTPStatus(status_code).phrase}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def _text_response(start_response, status_code, body, content_type):
    encoded = body if isinstance(body, bytes) else body.encode("utf-8")
    start_response(
        f"{status_code} {HTTPStatus(status_code).phrase}",
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(encoded))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [encoded]


def _document_source(paths):
    if not paths:
        return ""
    return os.path.basename(os.path.dirname(paths[0]))


def _serialize_document(doc):
    return {
        "id": doc["id"],
        "file_hash": doc["file_hash"],
        "title": doc["title"] or "",
        "authors": doc["authors"] or "",
        "summary": doc["summary"] or "",
        "status": doc["status"],
        "scanned_at": doc["scanned_at"],
        "file_modified_at": doc["file_modified_at"],
        "paths": doc["paths"],
        "topics": doc["topics"],
        "source": _document_source(doc["paths"]),
    }


def _content_disposition(file_path):
    filename = os.path.basename(file_path)
    suffix = Path(file_path).suffix.lower()
    disposition = "inline" if suffix == ".pdf" else "attachment"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "document"
    escaped_ascii_name = ascii_name.replace('"', "")
    quoted_name = quote(filename)
    return f"{disposition}; filename=\"{escaped_ascii_name}\"; filename*=UTF-8''{quoted_name}"


class DocuTrackerWebApp:
    def __init__(self, config_dir=None, cwd=None):
        self.config_dir = config_dir or os.environ.get(
            "DOCU_TRACKER_DIR", os.path.expanduser("~/.docu-tracker")
        )
        self.cwd = cwd or os.getcwd()
        self.db_path = os.path.join(self.config_dir, "tracker.db")

    def __call__(self, environ, start_response):
        try:
            method = environ.get("REQUEST_METHOD", "GET").upper()
            path = environ.get("PATH_INFO", "/")
            if path == "/":
                return _text_response(
                    start_response,
                    200,
                    _read_asset("index.html"),
                    "text/html; charset=utf-8",
                )
            if path == "/app.css":
                return _text_response(
                    start_response,
                    200,
                    _read_asset("app.css"),
                    "text/css; charset=utf-8",
                )
            if path == "/app.js":
                return _text_response(
                    start_response,
                    200,
                    _read_asset("app.js"),
                    "application/javascript; charset=utf-8",
                )
            if path == "/api/state" and method == "GET":
                return _json_response(start_response, 200, self.build_state())
            if path == "/api/scan" and method == "POST":
                payload = self._parse_json(environ)
                result = self.scan_documents(
                    path=payload.get("path"),
                    since=payload.get("since"),
                )
                return _json_response(start_response, 200, result)
            if path == "/api/rescan" and method == "POST":
                payload = self._parse_json(environ)
                result = self.reclassify_documents(
                    topic=payload.get("topic"),
                    doc_id=payload.get("doc_id"),
                    since=payload.get("since"),
                )
                return _json_response(start_response, 200, result)
            if path == "/api/topics" and method == "POST":
                payload = self._parse_json(environ)
                result = self.create_topic(payload)
                return _json_response(start_response, 201, result)

            if path.startswith("/api/documents/"):
                return self._handle_document_route(path, method, environ, start_response)
            if path.startswith("/api/topics/"):
                return self._handle_topic_route(path, method, environ, start_response)

            raise HTTPError(404, "Not found")
        except HTTPError as exc:
            return _json_response(
                start_response,
                exc.status_code,
                {"error": exc.message},
            )
        except Exception as exc:
            return _json_response(
                start_response,
                500,
                {"error": f"Internal server error: {exc}"},
            )

    def _handle_document_route(self, path, method, environ, start_response):
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) < 3:
            raise HTTPError(404, "Not found")
        try:
            doc_id = int(parts[2])
        except ValueError as exc:
            raise HTTPError(400, "Document id must be an integer") from exc

        if len(parts) == 3 and method == "PATCH":
            payload = self._parse_json(environ)
            result = self.update_document(doc_id, payload)
            return _json_response(start_response, 200, result)
        if len(parts) == 4 and parts[3] == "open" and method == "GET":
            return self.stream_document(doc_id, start_response)
        if len(parts) == 4 and parts[3] == "rescan" and method == "POST":
            result = self.reclassify_documents(doc_id=doc_id)
            return _json_response(start_response, 200, result)

        raise HTTPError(404, "Not found")

    def _handle_topic_route(self, path, method, environ, start_response):
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3:
            raise HTTPError(404, "Not found")
        topic_name = parts[2]

        if method == "PATCH":
            payload = self._parse_json(environ)
            result = self.update_topic(topic_name, payload)
            return _json_response(start_response, 200, result)
        if method == "DELETE":
            result = self.delete_topic(topic_name)
            return _json_response(start_response, 200, result)

        raise HTTPError(404, "Not found")

    def _parse_json(self, environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError as exc:
            raise HTTPError(400, "Invalid Content-Length header") from exc
        raw_body = environ["wsgi.input"].read(length) if length else b""
        if not raw_body:
            return {}
        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPError(400, "Invalid JSON body") from exc

    def _load_config(self):
        return load_config(
            config_dir=self.config_dir,
            dotenv_path=os.path.join(self.cwd, ".env"),
        )

    def build_state(self):
        config = self._load_config()
        with database_for_path(self.db_path) as db:
            docs = [_serialize_document(doc) for doc in db.list_documents()]
            topics = [
                {"name": name, "description": description}
                for name, description in db.list_topics_with_descriptions()
            ]
        return {
            "documents": docs,
            "topics": topics,
            "scan_paths": config.get("scan_paths", []),
            "model": config.get("model"),
            "has_api_key": bool(config.get("anthropic_api_key")),
            "statuses": VALID_STATUSES,
        }

    def update_document(self, doc_id, payload):
        with database_for_path(self.db_path) as db:
            doc = db.get_document(doc_id)
            if not doc:
                raise HTTPError(404, f"Document {doc_id} not found")

            status = payload.get("status")
            if status is not None:
                if status not in VALID_STATUSES:
                    raise HTTPError(400, f"Invalid status '{status}'")
                db.update_status(doc_id, status)

            title = payload.get("title")
            authors = payload.get("authors")
            summary = payload.get("summary")
            for value, field_name in (
                (title, "title"),
                (authors, "authors"),
                (summary, "summary"),
            ):
                if value is not None and not isinstance(value, str):
                    raise HTTPError(400, f"Document field '{field_name}' must be a string")
            if any(value is not None for value in (title, authors, summary)):
                db.update_document(
                    doc_id,
                    title=title,
                    authors=authors,
                    summary=summary,
                )

            topics = payload.get("topics")
            if topics is not None:
                if not isinstance(topics, list):
                    raise HTTPError(400, "Document topics must be a list")
                valid_topics = set(db.list_topics())
                cleaned_topics = []
                for topic in topics:
                    if not isinstance(topic, str):
                        raise HTTPError(400, "Each topic must be a string")
                    if topic not in valid_topics:
                        raise HTTPError(400, f"Unknown topic '{topic}'")
                    if topic not in cleaned_topics:
                        cleaned_topics.append(topic)
                db.set_topics(doc_id, cleaned_topics)

            return {"document": _serialize_document(db.get_document(doc_id))}

    def create_topic(self, payload):
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPError(400, "Topic name is required")
        description = (payload.get("description") or "").strip()
        with database_for_path(self.db_path) as db:
            if name in db.list_topics():
                raise HTTPError(400, f"Topic '{name}' already exists")
            db.add_topic(name, description)
        return {"ok": True}

    def update_topic(self, topic_name, payload):
        new_name = (payload.get("name") or topic_name).strip()
        description = payload.get("description")
        with database_for_path(self.db_path) as db:
            if topic_name not in db.list_topics():
                raise HTTPError(404, f"Topic '{topic_name}' not found")
            try:
                if new_name != topic_name:
                    db.rename_topic(topic_name, new_name)
                if description is not None:
                    db.update_topic_description(new_name, description.strip())
            except ValueError as exc:
                raise HTTPError(400, str(exc)) from exc
        return {"ok": True}

    def delete_topic(self, topic_name):
        with database_for_path(self.db_path) as db:
            if topic_name not in db.list_topics():
                raise HTTPError(404, f"Topic '{topic_name}' not found")
            try:
                db.remove_topic(topic_name)
            except ValueError as exc:
                raise HTTPError(400, str(exc)) from exc
        return {"ok": True}

    def _existing_document_path(self, doc_id):
        with database_for_path(self.db_path) as db:
            doc = db.get_document(doc_id)
            if not doc:
                raise HTTPError(404, f"Document {doc_id} not found")

            file_path = next(
                (path for path in doc.get("paths", []) if os.path.exists(path)),
                None,
            )
            if not file_path:
                raise HTTPError(404, "No existing file path found for this document")
            return doc, file_path

    def stream_document(self, doc_id, start_response):
        _, file_path = self._existing_document_path(doc_id)
        suffix = Path(file_path).suffix.lower()
        content_type = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(suffix, "application/octet-stream")
        with open(file_path, "rb") as handle:
            body = handle.read()
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Content-Disposition", _content_disposition(file_path)),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]

    def scan_documents(self, path=None, since=None):
        config = self._load_config()
        api_key = config.get("anthropic_api_key")
        if not api_key:
            raise HTTPError(
                400,
                "No API key found. Set ANTHROPIC_API_KEY or add it to .env.",
            )

        if path:
            scan_paths = [os.path.abspath(os.path.expanduser(path))]
        else:
            scan_paths = [
                os.path.abspath(os.path.expanduser(item))
                for item in config.get("scan_paths", [config["downloads_path"]])
            ]

        files = []
        for scan_path in scan_paths:
            files.extend(scan_directory(scan_path))

        if since:
            cutoff_ts = parse_since(since).timestamp()
            files = [file_path for file_path in files if os.path.getmtime(file_path) >= cutoff_ts]

        summary = {
            "mode": "scan",
            "paths": scan_paths,
            "files_seen": len(files),
            "new_count": 0,
            "duplicate_count": 0,
            "failed_count": 0,
            "items": [],
        }
        if not files:
            return summary

        with database_for_path(self.db_path) as db:
            topic_names = db.list_topics()
            topics_with_desc = db.list_topics_with_descriptions()
            to_analyze = []

            for file_path in files:
                file_hash = compute_file_hash(file_path)
                existing = db.get_document_by_hash(file_hash)
                if existing:
                    added_path = False
                    if file_path not in existing["paths"]:
                        db.add_duplicate_path(file_hash, file_path)
                        added_path = True
                    summary["duplicate_count"] += 1
                    summary["items"].append(
                        {
                            "kind": "duplicate",
                            "title": existing["title"],
                            "path": file_path,
                            "detail": "New location recorded" if added_path else "Already tracked",
                        }
                    )
                    continue

                text = extract_text(file_path)
                if not text.strip():
                    file_mtime = os.path.getmtime(file_path)
                    mtime_iso = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()
                    doc_id = db.add_document(
                        file_hash=file_hash,
                        file_path=file_path,
                        title=f"Unknown - {os.path.basename(file_path)}",
                        authors="",
                        summary="",
                        topics=["Other"],
                        file_modified_at=mtime_iso,
                    )
                    db.update_status(doc_id, "needs_review")
                    summary["failed_count"] += 1
                    summary["items"].append(
                        {
                            "kind": "needs_review",
                            "title": os.path.basename(file_path),
                            "path": file_path,
                            "detail": "No text extracted",
                        }
                    )
                    continue

                to_analyze.append((file_path, file_hash, text))

            if to_analyze:
                def _analyze_one(item):
                    file_path, file_hash, text = item
                    result = analyze_document(
                        text,
                        topic_names,
                        api_key,
                        topics_with_descriptions=topics_with_desc,
                        model=config.get("model"),
                    )
                    return file_path, file_hash, result

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    futures = {
                        pool.submit(_analyze_one, item): item for item in to_analyze
                    }
                    for future in as_completed(futures):
                        file_path, file_hash, result = future.result()
                        if result is None:
                            summary["failed_count"] += 1
                            summary["items"].append(
                                {
                                    "kind": "failed",
                                    "title": os.path.basename(file_path),
                                    "path": file_path,
                                    "detail": "LLM analysis failed",
                                }
                            )
                            continue

                        file_mtime = os.path.getmtime(file_path)
                        mtime_iso = datetime.fromtimestamp(
                            file_mtime, tz=timezone.utc
                        ).isoformat()
                        authors_str = ", ".join(result["authors"]) if result["authors"] else ""
                        db.add_document(
                            file_hash=file_hash,
                            file_path=file_path,
                            title=result["title"],
                            authors=authors_str,
                            summary=result["summary"],
                            topics=result["topics"],
                            file_modified_at=mtime_iso,
                        )
                        summary["new_count"] += 1
                        summary["items"].append(
                            {
                                "kind": "new",
                                "title": result["title"],
                                "path": file_path,
                                "detail": ", ".join(result["topics"]),
                            }
                        )

        return summary

    def reclassify_documents(self, topic=None, doc_id=None, since=None):
        config = self._load_config()
        api_key = config.get("anthropic_api_key")
        if not api_key:
            raise HTTPError(
                400,
                "No API key found. Set ANTHROPIC_API_KEY or add it to .env.",
            )

        summary = {
            "mode": "rescan",
            "updated_count": 0,
            "failed_count": 0,
            "items": [],
        }

        with database_for_path(self.db_path) as db:
            if doc_id is not None:
                doc = db.get_document(doc_id)
                if not doc:
                    raise HTTPError(404, f"Document {doc_id} not found")
                docs = [doc]
            else:
                docs = db.list_documents(topic=topic)
                if since:
                    cutoff_iso = parse_since(since).isoformat()
                    docs = [
                        doc for doc in docs
                        if (doc.get("file_modified_at") or "") >= cutoff_iso
                    ]

            if not docs:
                return summary

            topic_names = db.list_topics()
            topics_with_desc = db.list_topics_with_descriptions()
            to_analyze = []

            for doc in docs:
                file_path = doc["paths"][0] if doc["paths"] else None
                if not file_path or not os.path.exists(file_path):
                    summary["failed_count"] += 1
                    summary["items"].append(
                        {
                            "kind": "missing",
                            "title": doc["title"],
                            "detail": "Primary file path no longer exists",
                        }
                    )
                    continue

                text = extract_text(file_path)
                if not text.strip():
                    summary["failed_count"] += 1
                    summary["items"].append(
                        {
                            "kind": "needs_review",
                            "title": doc["title"],
                            "detail": "No text extracted during rescan",
                        }
                    )
                    continue

                to_analyze.append((doc, text))

            def _analyze_one(item):
                doc, text = item
                result = analyze_document(
                    text,
                    topic_names,
                    api_key,
                    topics_with_descriptions=topics_with_desc,
                    model=config.get("model"),
                )
                return doc, result

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {pool.submit(_analyze_one, item): item for item in to_analyze}
                for future in as_completed(futures):
                    doc, result = future.result()
                    if result is None:
                        summary["failed_count"] += 1
                        summary["items"].append(
                            {
                                "kind": "failed",
                                "title": doc["title"],
                                "detail": "LLM analysis failed",
                            }
                        )
                        continue

                    authors_str = ", ".join(result["authors"]) if result["authors"] else ""
                    db.set_topics(doc["id"], result["topics"])
                    db.update_document(
                        doc["id"],
                        title=result["title"],
                        authors=authors_str,
                        summary=result["summary"],
                    )
                    summary["updated_count"] += 1
                    summary["items"].append(
                        {
                            "kind": "updated",
                            "title": result["title"],
                            "detail": ", ".join(result["topics"]),
                        }
                    )

        return summary


class QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        print(format % args, file=sys.stdout)


def serve_web_app(host="127.0.0.1", port=8421, config_dir=None, cwd=None):
    app = DocuTrackerWebApp(config_dir=config_dir, cwd=cwd)
    with make_server(
        host,
        port,
        app,
        server_class=ThreadingWSGIServer,
        handler_class=QuietWSGIRequestHandler,
    ) as httpd:
        print(
            f"Docu Tracker web UI running at http://{host}:{httpd.server_port}",
            file=sys.stdout,
        )
        httpd.serve_forever()


def open_web_ui(host="127.0.0.1", port=8421):
    url = f"http://{host}:{port}"
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()


def build_test_environ(method="GET", path="/", payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": BytesIO(body),
    }
