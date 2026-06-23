import base64
import errno
import json
import os
import sys
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from docu_tracker.analyzer import analyze_document
from docu_tracker.config import load_config
from docu_tracker.db import Database
from docu_tracker.extractor import extract_text
from docu_tracker.scanner import compute_file_hash, scan_directory

ASSET_DIR = Path(__file__).resolve().parent / "webui"
MAX_WORKERS = 4
VALID_STATUSES = ["unread", "reading", "read", "needs_review"]
SESSION_SHUTDOWN_GRACE_SECONDS = 2.0

NOTEBOOK_ATTACHMENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_NOTEBOOK_ATTACHMENT_BYTES = 10 * 1024 * 1024



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



def _bytes_response(start_response, status_code, body, content_type):
    start_response(
        f"{status_code} {HTTPStatus(status_code).phrase}",
        [
            ("Content-Type", content_type),
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



def _serialize_notebook_note(note):
    return {
        "id": note["id"],
        "title": note["title"] or "",
        "body": note["body"] or "",
        "created_at": note["created_at"],
        "updated_at": note["updated_at"],
        "document_ids": note["document_ids"],
        "topics": note["topics"],
    }

def _configured_scan_paths(config):
    return [
        os.path.abspath(os.path.expanduser(item))
        for item in config.get("scan_paths", [config["downloads_path"]])
    ]


def _timestamp_from_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value).timestamp()


def _waiting_since_last_scan(scan_paths, last_scan_by_path):
    waiting_count = 0
    oldest_waiting_ts = None
    seen_paths = set()
    for scan_path in scan_paths:
        last_scan_ts = _timestamp_from_iso(last_scan_by_path.get(scan_path))
        for file_path in scan_directory(scan_path):
            absolute_path = os.path.abspath(file_path)
            if absolute_path in seen_paths:
                continue
            seen_paths.add(absolute_path)
            try:
                file_mtime = os.path.getmtime(absolute_path)
            except OSError:
                continue
            if last_scan_ts is None or file_mtime > last_scan_ts:
                waiting_count += 1
                oldest_waiting_ts = min(oldest_waiting_ts or file_mtime, file_mtime)
    oldest_waiting_modified_at = None
    if oldest_waiting_ts is not None:
        oldest_waiting_modified_at = datetime.fromtimestamp(
            oldest_waiting_ts, tz=timezone.utc
        ).isoformat()
    return {
        "waiting_to_scan": waiting_count,
        "oldest_waiting_modified_at": oldest_waiting_modified_at,
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
        self.shutdown_callback = None
        self._session_lock = threading.Lock()
        self._session_ids = set()
        self._shutdown_timer = None

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
            if path == "/favicon.svg":
                return _text_response(
                    start_response,
                    200,
                    _read_asset("favicon.svg"),
                    "image/svg+xml",
                )
            if path == "/api/state" and method == "GET":
                return _json_response(start_response, 200, self.build_state())
            if path == "/api/stats/waiting-to-scan" and method == "GET":
                return _json_response(start_response, 200, self.waiting_to_scan_state())
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
            if path == "/api/duplicates/clear" and method == "POST":
                payload = self._parse_json(environ)
                result = self.clear_all_duplicate_paths(
                    hard_delete=bool(payload.get("hard_delete"))
                )
                return _json_response(start_response, 200, result)
            if path == "/api/duplicates/scan" and method == "POST":
                payload = self._parse_json(environ)
                result = self.scan_duplicate_files(
                    path=payload.get("path"),
                    since=payload.get("since"),
                )
                return _json_response(start_response, 200, result)
            if path == "/api/missing-files/prune" and method == "POST":
                result = self.prune_missing_file_records()
                return _json_response(start_response, 200, result)
            if path == "/api/notebook" and method == "GET":
                return _json_response(start_response, 200, self.notebook_state())
            if path == "/api/notebook" and method == "POST":
                payload = self._parse_json(environ)
                result = self.create_notebook_note(payload)
                return _json_response(start_response, 201, result)
            if path == "/api/notebook/attachments" and method == "POST":
                content_type = (environ.get("CONTENT_TYPE") or "").split(";", 1)[0].lower()
                if content_type in NOTEBOOK_ATTACHMENT_TYPES:
                    query = parse_qs(environ.get("QUERY_STRING") or "")
                    result = self.create_notebook_attachment_from_bytes(
                        self._read_request_body(environ),
                        content_type,
                        (query.get("name") or [""])[0],
                    )
                else:
                    payload = self._parse_json(environ)
                    result = self.create_notebook_attachment(payload)
                return _json_response(start_response, 201, result)
            if path.startswith("/api/notebook/attachments/") and method == "GET":
                return self.stream_notebook_attachment(path, start_response)
            if path == "/api/session/open" and method == "POST":
                payload = self._parse_json(environ)
                return _json_response(start_response, 200, self.open_session(payload))
            if path == "/api/session/close" and method == "POST":
                payload = self._parse_json(environ)
                return _json_response(start_response, 200, self.close_session(payload))

            if path.startswith("/api/notebook/"):
                return self._handle_notebook_route(path, method, environ, start_response)
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


    def _handle_notebook_route(self, path, method, environ, start_response):
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3:
            raise HTTPError(404, "Not found")
        try:
            note_id = int(parts[2])
        except ValueError as exc:
            raise HTTPError(400, "Notebook note id must be an integer") from exc

        if method == "PATCH":
            payload = self._parse_json(environ)
            result = self.update_notebook_note(note_id, payload)
            return _json_response(start_response, 200, result)
        if method == "DELETE":
            result = self.delete_notebook_note(note_id)
            return _json_response(start_response, 200, result)

        raise HTTPError(404, "Not found")

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
        if len(parts) == 4 and parts[3] == "paths" and method == "DELETE":
            payload = self._parse_json(environ)
            result = self.remove_document_path(doc_id, payload)
            return _json_response(start_response, 200, result)
        if len(parts) == 5 and parts[3] == "duplicates" and parts[4] == "clear" and method == "POST":
            payload = self._parse_json(environ)
            result = self.clear_document_duplicate_paths(
                doc_id,
                hard_delete=bool(payload.get("hard_delete")),
            )
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

    def _read_request_body(self, environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError as exc:
            raise HTTPError(400, "Invalid Content-Length header") from exc
        return environ["wsgi.input"].read(length) if length else b""

    def _parse_json(self, environ):
        raw_body = self._read_request_body(environ)
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

    def _cancel_shutdown_timer(self):
        if self._shutdown_timer is not None:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None

    def _schedule_shutdown(self):
        def _maybe_shutdown():
            with self._session_lock:
                self._shutdown_timer = None
                if self._session_ids:
                    return
            if self.shutdown_callback:
                self.shutdown_callback()

        self._cancel_shutdown_timer()
        self._shutdown_timer = threading.Timer(
            SESSION_SHUTDOWN_GRACE_SECONDS,
            _maybe_shutdown,
        )
        self._shutdown_timer.daemon = True
        self._shutdown_timer.start()

    def open_session(self, payload):
        session_id = (payload.get("session_id") or "").strip()
        if not session_id:
            raise HTTPError(400, "session_id is required")
        with self._session_lock:
            self._session_ids.add(session_id)
            self._cancel_shutdown_timer()
        return {"ok": True}

    def close_session(self, payload):
        session_id = (payload.get("session_id") or "").strip()
        if not session_id:
            raise HTTPError(400, "session_id is required")
        should_schedule_shutdown = False
        with self._session_lock:
            self._session_ids.discard(session_id)
            if not self._session_ids:
                should_schedule_shutdown = True
        if should_schedule_shutdown:
            self._schedule_shutdown()
        return {"ok": True}

    def build_state(self):
        config = self._load_config()
        with database_for_path(self.db_path) as db:
            docs = [_serialize_document(doc) for doc in db.list_documents()]
            topics = [
                {"name": name, "description": description}
                for name, description in db.list_topics_with_descriptions()
            ]
            notebook_notes = [
                _serialize_notebook_note(note)
                for note in db.list_notebook_notes()
            ]
        return {
            "documents": docs,
            "topics": topics,
            "scan_paths": config.get("scan_paths", []),
            "notebook_notes": notebook_notes,
            "model": config.get("model"),
            "has_api_key": bool(config.get("anthropic_api_key")),
            "statuses": VALID_STATUSES,
        }

    def waiting_to_scan_state(self):
        config = self._load_config()
        scan_paths = _configured_scan_paths(config)
        with database_for_path(self.db_path) as db:
            last_scan_by_path = {
                scan_path: db.get_scan_path_last_scanned_at(scan_path)
                for scan_path in scan_paths
            }
        return _waiting_since_last_scan(scan_paths, last_scan_by_path)


    def notebook_state(self):
        with database_for_path(self.db_path) as db:
            notes = [
                _serialize_notebook_note(note)
                for note in db.list_notebook_notes()
            ]
        return {"notebook_notes": notes}

    def _clean_notebook_document_ids(self, db, document_ids):
        if document_ids is None:
            return None
        if not isinstance(document_ids, list):
            raise HTTPError(400, "Notebook document_ids must be a list")
        cleaned_ids = []
        for raw_doc_id in document_ids:
            if not isinstance(raw_doc_id, int):
                raise HTTPError(400, "Notebook document IDs must be integers")
            if raw_doc_id in cleaned_ids:
                continue
            if not db.get_document(raw_doc_id):
                raise HTTPError(400, f"Unknown document id {raw_doc_id}")
            cleaned_ids.append(raw_doc_id)
        return cleaned_ids

    def _clean_notebook_topics(self, topics):
        if topics is None:
            return None
        if not isinstance(topics, list):
            raise HTTPError(400, "Notebook topics must be a list")
        cleaned = []
        for raw in topics:
            if not isinstance(raw, str):
                raise HTTPError(400, "Notebook topics must be strings")
            name = raw.strip()
            # Silently drop blank/whitespace-only names and duplicates; unknown
            # topic names are tolerated here and ignored later by the DB layer.
            if name and name not in cleaned:
                cleaned.append(name)
        return cleaned

    def create_notebook_note(self, payload):
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPError(400, "Notebook title is required")
        body = payload.get("body") or ""
        if not isinstance(body, str):
            raise HTTPError(400, "Notebook body must be a string")
        with database_for_path(self.db_path) as db:
            document_ids = self._clean_notebook_document_ids(
                db,
                payload.get("document_ids", []),
            )
            topics = self._clean_notebook_topics(payload.get("topics", []))
            note_id = db.add_notebook_note(title, body, document_ids, topics=topics)
            return {"note": _serialize_notebook_note(db.get_notebook_note(note_id))}

    def update_notebook_note(self, note_id, payload):
        with database_for_path(self.db_path) as db:
            note = db.get_notebook_note(note_id)
            if not note:
                raise HTTPError(404, f"Notebook note {note_id} not found")

            title = payload.get("title") if "title" in payload else None
            if title is not None:
                if not isinstance(title, str):
                    raise HTTPError(400, "Notebook title must be a string")
                title = title.strip()
                if not title:
                    raise HTTPError(400, "Notebook title is required")

            body = payload.get("body") if "body" in payload else None
            if body is not None and not isinstance(body, str):
                raise HTTPError(400, "Notebook body must be a string")

            document_ids = self._clean_notebook_document_ids(
                db,
                payload.get("document_ids") if "document_ids" in payload else None,
            )
            topics = self._clean_notebook_topics(
                payload.get("topics") if "topics" in payload else None
            )
            db.update_notebook_note(
                note_id,
                title=title,
                body=body,
                document_ids=document_ids,
                topics=topics,
            )
            return {"note": _serialize_notebook_note(db.get_notebook_note(note_id))}

    def delete_notebook_note(self, note_id):
        with database_for_path(self.db_path) as db:
            if not db.get_notebook_note(note_id):
                raise HTTPError(404, f"Notebook note {note_id} not found")
            db.delete_notebook_note(note_id)
        return {"ok": True}


    def _notebook_attachment_dir(self):
        return Path(self.config_dir) / "notebook_attachments"

    def _write_notebook_attachment(self, body, content_type):
        suffix = NOTEBOOK_ATTACHMENT_TYPES.get(content_type)
        if not suffix:
            raise HTTPError(400, "Only PNG, JPEG, GIF, and WebP images can be pasted")
        if not body:
            raise HTTPError(400, "Attachment is empty")
        if len(body) > MAX_NOTEBOOK_ATTACHMENT_BYTES:
            raise HTTPError(400, "Attachment is larger than 10 MB")

        attachment_dir = self._notebook_attachment_dir()
        attachment_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex}{suffix}"
        path = attachment_dir / filename
        path.write_bytes(body)
        return {
            "url": f"/api/notebook/attachments/{quote(filename)}",
            "filename": filename,
            "content_type": content_type,
        }

    def create_notebook_attachment_from_bytes(self, body, content_type, name=""):
        return self._write_notebook_attachment(body, content_type)

    def create_notebook_attachment(self, payload):
        data_url = payload.get("data_url")
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            raise HTTPError(400, "Attachment data_url is required")
        try:
            header, encoded = data_url.split(",", 1)
        except ValueError as exc:
            raise HTTPError(400, "Invalid attachment data URL") from exc
        if ";base64" not in header:
            raise HTTPError(400, "Attachment data URL must be base64 encoded")
        content_type = header[5:].split(";", 1)[0].lower()
        try:
            body = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise HTTPError(400, "Invalid base64 attachment data") from exc
        return self._write_notebook_attachment(body, content_type)

    def stream_notebook_attachment(self, path, start_response):
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 4:
            raise HTTPError(404, "Not found")
        filename = parts[3]
        if filename != os.path.basename(filename):
            raise HTTPError(400, "Invalid attachment name")
        suffix = Path(filename).suffix.lower()
        content_type = next(
            (kind for kind, ext in NOTEBOOK_ATTACHMENT_TYPES.items() if ext == suffix),
            "application/octet-stream",
        )
        attachment_path = self._notebook_attachment_dir() / filename
        if not attachment_path.exists() or not attachment_path.is_file():
            raise HTTPError(404, "Attachment not found")
        return _bytes_response(start_response, 200, attachment_path.read_bytes(), content_type)

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

    def prune_missing_file_records(self):
        with database_for_path(self.db_path) as db:
            result = db.prune_missing_file_records()
        return {"ok": True, **result}

    def _delete_duplicate_file(self, file_path):
        if not os.path.exists(file_path):
            return False
        if not os.path.isfile(file_path):
            raise HTTPError(400, f"Duplicate path is not a file: {file_path}")
        try:
            os.remove(file_path)
        except OSError as exc:
            raise HTTPError(400, f"Could not delete duplicate file: {exc}") from exc
        return True

    def remove_document_path(self, doc_id, payload):
        file_path = payload.get("path")
        hard_delete = bool(payload.get("hard_delete"))
        if not isinstance(file_path, str) or not file_path:
            raise HTTPError(400, "Document path is required")

        deleted_count = 0
        with database_for_path(self.db_path) as db:
            doc = db.get_document(doc_id)
            if not doc:
                raise HTTPError(404, f"Document {doc_id} not found")
            if file_path not in doc["paths"]:
                raise HTTPError(404, "Document path not found")
            if hard_delete and file_path == doc["paths"][0]:
                raise HTTPError(400, "Cannot hard-delete the primary tracked file")
            if hard_delete and self._delete_duplicate_file(file_path):
                deleted_count = 1
            try:
                removed_count = db.remove_document_path(doc_id, file_path)
            except ValueError as exc:
                raise HTTPError(400, str(exc)) from exc
            return {
                "ok": True,
                "removed_count": removed_count,
                "deleted_count": deleted_count,
                "document": _serialize_document(db.get_document(doc_id)),
            }

    def clear_document_duplicate_paths(self, doc_id, hard_delete=False):
        with database_for_path(self.db_path) as db:
            doc = db.get_document(doc_id)
            if not doc:
                raise HTTPError(404, f"Document {doc_id} not found")
            duplicate_paths = doc["paths"][1:]
            if not hard_delete:
                removed_count = db.clear_document_duplicate_paths(doc_id)
                return {
                    "ok": True,
                    "removed_count": removed_count,
                    "deleted_count": 0,
                    "document": _serialize_document(db.get_document(doc_id)),
                }

            removed_count = 0
            deleted_count = 0
            for file_path in duplicate_paths:
                if self._delete_duplicate_file(file_path):
                    deleted_count += 1
                removed_count += db.remove_document_path(doc_id, file_path)
            return {
                "ok": True,
                "removed_count": removed_count,
                "deleted_count": deleted_count,
                "document": _serialize_document(db.get_document(doc_id)),
            }

    def clear_all_duplicate_paths(self, hard_delete=False):
        with database_for_path(self.db_path) as db:
            if not hard_delete:
                result = db.clear_all_duplicate_paths()
                return {"ok": True, "deleted_count": 0, **result}

            duplicate_docs = [doc for doc in db.list_documents() if len(doc.get("paths", [])) > 1]
            removed_count = 0
            deleted_count = 0
            for doc in duplicate_docs:
                for file_path in doc["paths"][1:]:
                    if self._delete_duplicate_file(file_path):
                        deleted_count += 1
                    removed_count += db.remove_document_path(doc["id"], file_path)
            return {
                "ok": True,
                "document_count": len(duplicate_docs),
                "removed_count": removed_count,
                "deleted_count": deleted_count,
            }

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

    def scan_duplicate_files(self, path=None, since=None):
        config = self._load_config()
        if path:
            scan_paths = [os.path.abspath(os.path.expanduser(path))]
        else:
            scan_paths = _configured_scan_paths(config)

        files = []
        for scan_path in scan_paths:
            files.extend(scan_directory(scan_path))

        if since:
            cutoff_ts = parse_since(since).timestamp()
            files = [file_path for file_path in files if os.path.getmtime(file_path) >= cutoff_ts]

        summary = {
            "mode": "duplicate_scan",
            "paths": scan_paths,
            "files_seen": len(files),
            "recorded_count": 0,
            "new_group_count": 0,
            "already_tracked_count": 0,
            "items": [],
        }

        seen_untracked = {}
        with database_for_path(self.db_path) as db:
            for file_path in files:
                file_hash = compute_file_hash(file_path)
                existing = db.get_document_by_hash(file_hash)
                if existing:
                    if file_path in existing["paths"]:
                        summary["already_tracked_count"] += 1
                        continue
                    db.add_duplicate_path(file_hash, file_path)
                    summary["recorded_count"] += 1
                    summary["items"].append({
                        "kind": "duplicate",
                        "title": existing["title"],
                        "path": file_path,
                        "detail": "Duplicate location recorded",
                    })
                    continue

                primary_path = seen_untracked.get(file_hash)
                if not primary_path:
                    seen_untracked[file_hash] = file_path
                    continue

                file_mtime = os.path.getmtime(primary_path)
                mtime_iso = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()
                doc_id = db.add_document(
                    file_hash=file_hash,
                    file_path=primary_path,
                    title=f"Unknown - {os.path.basename(primary_path)}",
                    authors="",
                    summary="",
                    topics=["Other"],
                    file_modified_at=mtime_iso,
                )
                db.update_status(doc_id, "needs_review")
                db.add_duplicate_path(file_hash, file_path)
                summary["new_group_count"] += 1
                summary["recorded_count"] += 1
                summary["items"].append({
                    "kind": "duplicate_group",
                    "title": os.path.basename(primary_path),
                    "path": file_path,
                    "detail": "New duplicate group tracked for review",
                })

        return summary

    def scan_documents(self, path=None, since=None):
        scan_started_at = datetime.now(timezone.utc).isoformat()
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
            scan_paths = _configured_scan_paths(config)

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
            with database_for_path(self.db_path) as db:
                for scan_path in scan_paths:
                    db.set_scan_path_last_scanned_at(scan_path, scan_started_at)
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

        with database_for_path(self.db_path) as db:
            for scan_path in scan_paths:
                db.set_scan_path_last_scanned_at(scan_path, scan_started_at)

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


def serve_web_app(host="127.0.0.1", port=8421, config_dir=None, cwd=None, open_browser=False):
    app = DocuTrackerWebApp(config_dir=config_dir, cwd=cwd)
    try:
        httpd = make_server(
            host,
            port,
            app,
            server_class=ThreadingWSGIServer,
            handler_class=QuietWSGIRequestHandler,
        )
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        print(
            f"Port {port} is already in use; choosing an available port instead.",
            file=sys.stdout,
        )
        httpd = make_server(
            host,
            0,
            app,
            server_class=ThreadingWSGIServer,
            handler_class=QuietWSGIRequestHandler,
        )

    with httpd:
        app.shutdown_callback = httpd.shutdown
        actual_port = httpd.server_port
        print(
            f"Docu Tracker web UI running at http://{host}:{actual_port}",
            file=sys.stdout,
        )
        if open_browser:
            open_web_ui(host=host, port=actual_port)
        httpd.serve_forever()


def open_web_ui(host="127.0.0.1", port=8421):
    url = f"http://{host}:{port}"
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()


def build_test_environ(method="GET", path="/", payload=None, body=None, content_type="application/json"):
    if body is None:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    path_info, _, query_string = path.partition("?")
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path_info,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": content_type,
        "wsgi.input": BytesIO(body),
    }
