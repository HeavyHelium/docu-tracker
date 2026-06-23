import base64
import errno
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

from click.testing import CliRunner

from docu_tracker.cli import cli
from docu_tracker.db import Database
from docu_tracker.web import DocuTrackerWebApp, build_test_environ


def call_app(app, method="GET", path="/", payload=None, body=None, content_type="application/json"):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(
        app(
            build_test_environ(
                method=method,
                path=path,
                payload=payload,
                body=body,
                content_type=content_type,
            ),
            start_response,
        )
    )
    captured["body"] = body
    header_map = {name.lower(): value for name, value in captured["headers"]}
    if body and header_map.get("content-type", "").startswith("application/json"):
        captured["json"] = json.loads(body.decode("utf-8"))
    else:
        captured["json"] = None
    return captured


def seed_document(app, file_path, title="Seed Paper", topics=None):
    db = Database(app.db_path)
    db.initialize()
    doc_id = db.add_document(
        file_hash=f"hash-{title}",
        file_path=str(file_path),
        title=title,
        authors="Alice",
        summary="Seed summary.",
        topics=topics or ["Other"],
        file_modified_at=datetime.now(timezone.utc).isoformat(),
    )
    db.close()
    return doc_id


def test_state_and_document_update(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf-bytes")
    seed_document(app, file_path)

    state_response = call_app(app, "GET", "/api/state")
    assert state_response["status"].startswith("200")
    assert len(state_response["json"]["documents"]) == 1

    update_response = call_app(
        app,
        "PATCH",
        "/api/documents/1",
        {
            "title": "Updated Paper",
            "authors": "Alice, Bob",
            "summary": "Updated summary.",
            "status": "reading",
            "topics": ["Work"],
        },
    )
    assert update_response["status"].startswith("200")
    document = update_response["json"]["document"]
    assert document["title"] == "Updated Paper"
    assert document["status"] == "reading"
    assert document["topics"] == ["Work"]


def test_duplicate_clear_routes(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    primary_path = tmp_path / "paper.pdf"
    duplicate_path = tmp_path / "paper-copy.pdf"
    primary_path.write_bytes(b"pdf-bytes")
    duplicate_path.write_bytes(b"pdf-copy-bytes")
    doc_id = seed_document(app, primary_path)

    db = Database(app.db_path)
    db.initialize()
    db.add_duplicate_path("hash-Seed Paper", str(duplicate_path))
    db.close()

    remove_response = call_app(
        app,
        "DELETE",
        f"/api/documents/{doc_id}/paths",
        {"path": str(duplicate_path)},
    )

    assert remove_response["status"].startswith("200")
    assert remove_response["json"]["removed_count"] == 1
    assert remove_response["json"]["document"]["paths"] == [str(primary_path)]

    db = Database(app.db_path)
    db.initialize()
    db.add_duplicate_path("hash-Seed Paper", str(duplicate_path))
    db.close()

    doc_clear_response = call_app(app, "POST", f"/api/documents/{doc_id}/duplicates/clear")

    assert doc_clear_response["status"].startswith("200")
    assert doc_clear_response["json"]["removed_count"] == 1
    assert doc_clear_response["json"]["document"]["paths"] == [str(primary_path)]

    second_path = tmp_path / "notes.pdf"
    second_duplicate_path = tmp_path / "notes-copy.pdf"
    second_path.write_bytes(b"notes")
    second_duplicate_path.write_bytes(b"notes-copy")
    second_doc_id = seed_document(app, second_path, title="Second Paper")

    db = Database(app.db_path)
    db.initialize()
    db.add_duplicate_path("hash-Seed Paper", str(duplicate_path))
    db.add_duplicate_path("hash-Second Paper", str(second_duplicate_path))
    db.close()

    all_clear_response = call_app(app, "POST", "/api/duplicates/clear")

    assert all_clear_response["status"].startswith("200")
    assert all_clear_response["json"]["document_count"] == 2
    assert all_clear_response["json"]["removed_count"] == 2

    state_response = call_app(app, "GET", "/api/state")
    docs_by_id = {doc["id"]: doc for doc in state_response["json"]["documents"]}
    assert docs_by_id[doc_id]["paths"] == [str(primary_path)]
    assert docs_by_id[second_doc_id]["paths"] == [str(second_path)]


def test_hard_delete_duplicate_path_route_deletes_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    primary_path = tmp_path / "paper.pdf"
    duplicate_path = tmp_path / "paper-copy.pdf"
    primary_path.write_bytes(b"pdf-bytes")
    duplicate_path.write_bytes(b"pdf-bytes")
    doc_id = seed_document(app, primary_path)

    db = Database(app.db_path)
    db.initialize()
    db.add_duplicate_path("hash-Seed Paper", str(duplicate_path))
    db.close()

    response = call_app(
        app,
        "DELETE",
        f"/api/documents/{doc_id}/paths",
        {"path": str(duplicate_path), "hard_delete": True},
    )

    assert response["status"].startswith("200")
    assert response["json"]["deleted_count"] == 1
    assert primary_path.exists()
    assert not duplicate_path.exists()
    assert response["json"]["document"]["paths"] == [str(primary_path)]

    primary_delete_response = call_app(
        app,
        "DELETE",
        f"/api/documents/{doc_id}/paths",
        {"path": str(primary_path), "hard_delete": True},
    )
    assert primary_delete_response["status"].startswith("400")
    assert primary_path.exists()


def test_duplicate_scan_route_tracks_untracked_duplicate_group(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    scan_dir = tmp_path / "downloads"
    scan_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        f"scan_paths:\n  - {scan_dir}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    first_path = scan_dir / "a.pdf"
    second_path = scan_dir / "b.pdf"
    first_path.write_bytes(b"same-pdf")
    second_path.write_bytes(b"same-pdf")

    response = call_app(app, "POST", "/api/duplicates/scan")

    assert response["status"].startswith("200")
    assert response["json"]["recorded_count"] == 1
    assert response["json"]["new_group_count"] == 1

    state_response = call_app(app, "GET", "/api/state")
    document = state_response["json"]["documents"][0]
    assert document["status"] == "needs_review"
    assert document["paths"] == [str(first_path), str(second_path)]


def test_prune_missing_files_route(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    existing_path = tmp_path / "paper.pdf"
    missing_path = tmp_path / "paper-copy.pdf"
    existing_path.write_bytes(b"pdf-bytes")
    doc_id = seed_document(app, existing_path)

    db = Database(app.db_path)
    db.initialize()
    db.add_duplicate_path("hash-Seed Paper", str(missing_path))
    db.close()

    response = call_app(app, "POST", "/api/missing-files/prune")

    assert response["status"].startswith("200")
    assert response["json"]["removed_path_count"] == 1
    assert response["json"]["removed_document_count"] == 0
    state_response = call_app(app, "GET", "/api/state")
    docs_by_id = {doc["id"]: doc for doc in state_response["json"]["documents"]}
    assert docs_by_id[doc_id]["paths"] == [str(existing_path)]


def test_topic_crud_routes(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    create_response = call_app(
        app,
        "POST",
        "/api/topics",
        {"name": "Research", "description": "Research papers and notes"},
    )
    assert create_response["status"].startswith("201")

    update_response = call_app(
        app,
        "PATCH",
        "/api/topics/Research",
        {"name": "Research Notes", "description": "Renamed topic"},
    )
    assert update_response["status"].startswith("200")

    delete_response = call_app(app, "DELETE", "/api/topics/Research%20Notes")
    assert delete_response["status"].startswith("200")

    state_response = call_app(app, "GET", "/api/state")
    topic_names = [topic["name"] for topic in state_response["json"]["topics"]]
    assert "Research Notes" not in topic_names



def test_notebook_routes_persist_notes_and_references(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf-bytes")
    doc_id = seed_document(app, file_path)

    create_response = call_app(
        app,
        "POST",
        "/api/notebook",
        {
            "title": "Deception map",
            "body": "# Claim\n- Evidence",
            "document_ids": [doc_id],
        },
    )
    assert create_response["status"].startswith("201")
    note = create_response["json"]["note"]
    assert note["title"] == "Deception map"
    assert note["document_ids"] == [doc_id]

    update_response = call_app(
        app,
        "PATCH",
        f"/api/notebook/{note["id"]}",
        {
            "title": "Updated map",
            "body": "Updated synthesis",
            "document_ids": [],
        },
    )
    assert update_response["status"].startswith("200")
    assert update_response["json"]["note"]["title"] == "Updated map"
    assert update_response["json"]["note"]["document_ids"] == []

    state_response = call_app(app, "GET", "/api/state")
    assert state_response["json"]["notebook_notes"][0]["body"] == "Updated synthesis"

    invalid_response = call_app(
        app,
        "POST",
        "/api/notebook",
        {"title": "Invalid", "document_ids": [999]},
    )
    assert invalid_response["status"].startswith("400")

    delete_response = call_app(app, "DELETE", f"/api/notebook/{note["id"]}")
    assert delete_response["status"].startswith("200")
    assert call_app(app, "GET", "/api/notebook")["json"]["notebook_notes"] == []


def test_notebook_attachment_routes_store_pasted_images(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    create_response = call_app(
        app,
        "POST",
        "/api/notebook/attachments",
        {"data_url": "data:image/png;base64," + encoded},
    )

    assert create_response["status"].startswith("201")
    assert create_response["json"]["content_type"] == "image/png"
    assert create_response["json"]["url"].startswith("/api/notebook/attachments/")

    fetch_response = call_app(app, "GET", create_response["json"]["url"])
    headers = {name.lower(): value for name, value in fetch_response["headers"]}
    assert fetch_response["status"].startswith("200")
    assert headers["content-type"] == "image/png"
    assert fetch_response["body"] == b"png-bytes"

    binary_response = call_app(
        app,
        "POST",
        "/api/notebook/attachments?name=pasted.png",
        body=b"raw-png-bytes",
        content_type="image/png",
    )
    assert binary_response["status"].startswith("201")
    binary_fetch = call_app(app, "GET", binary_response["json"]["url"])
    assert binary_fetch["body"] == b"raw-png-bytes"

    invalid_response = call_app(
        app,
        "POST",
        "/api/notebook/attachments",
        {"data_url": "data:text/plain;base64," + encoded},
    )
    assert invalid_response["status"].startswith("400")


def test_waiting_to_scan_route_counts_supported_files(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    scan_dir = tmp_path / "downloads"
    scan_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        f"scan_paths:\n  - {scan_dir}\n  - {scan_dir}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    tracked_file = scan_dir / "tracked.pdf"
    tracked_file.write_bytes(b"tracked-pdf")
    waiting_file = scan_dir / "waiting.docx"
    waiting_file.write_bytes(b"waiting-docx")
    newer_waiting_file = scan_dir / "newer-waiting.pdf"
    newer_waiting_file.write_bytes(b"newer-waiting-pdf")
    old_untracked_file = scan_dir / "old-untracked.pdf"
    old_untracked_file.write_bytes(b"old-untracked-pdf")
    ignored_file = scan_dir / "notes.txt"
    ignored_file.write_text("not supported", encoding="utf-8")

    last_scan_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    new_ts = datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp()
    newer_ts = datetime(2026, 1, 3, tzinfo=timezone.utc).timestamp()
    for file_path in (tracked_file, old_untracked_file):
        os.utime(file_path, (old_ts, old_ts))
    os.utime(waiting_file, (new_ts, new_ts))
    os.utime(newer_waiting_file, (newer_ts, newer_ts))

    db = Database(app.db_path)
    db.initialize()
    db.add_document(
        file_hash="tracked-hash",
        file_path=str(tracked_file),
        title="Tracked",
        authors="",
        summary="",
        topics=["Other"],
        file_modified_at=datetime.now(timezone.utc).isoformat(),
    )
    db.set_scan_path_last_scanned_at(str(scan_dir), last_scan_at.isoformat())
    db.close()

    state_response = call_app(app, "GET", "/api/stats/waiting-to-scan")

    assert state_response["status"].startswith("200")
    assert state_response["json"]["waiting_to_scan"] == 2
    assert state_response["json"]["oldest_waiting_modified_at"].startswith("2026-01-02T00:00:00")


def test_scan_route_adds_document(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    scan_dir = tmp_path / "downloads"
    scan_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        f"scan_paths:\n  - {scan_dir}\n",
        encoding="utf-8",
    )
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))
    file_path = scan_dir / "fresh.pdf"
    file_path.write_bytes(b"pdf-bytes")

    with patch("docu_tracker.web.extract_text", return_value="some extracted text"):
        with patch("docu_tracker.web.analyze_document") as mock_analyze:
            mock_analyze.return_value = {
                "title": "Fresh Paper",
                "authors": ["Alice"],
                "summary": "Fresh summary.",
                "topics": ["Academic"],
            }
            response = call_app(
                app,
                "POST",
                "/api/scan",
                {"path": str(scan_dir)},
            )

    assert response["status"].startswith("200")
    assert response["json"]["new_count"] == 1

    state_response = call_app(app, "GET", "/api/state")
    assert state_response["json"]["documents"][0]["title"] == "Fresh Paper"

    stat_response = call_app(app, "GET", "/api/stats/waiting-to-scan")
    assert stat_response["json"]["waiting_to_scan"] == 0


def test_rescan_route_updates_document(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf-bytes")
    seed_document(app, file_path, title="Before", topics=["Other"])

    with patch("docu_tracker.web.extract_text", return_value="rescanned text"):
        with patch("docu_tracker.web.analyze_document") as mock_analyze:
            mock_analyze.return_value = {
                "title": "After",
                "authors": ["Alice", "Bob"],
                "summary": "Rescanned summary.",
                "topics": ["Finance"],
            }
            response = call_app(app, "POST", "/api/documents/1/rescan")

    assert response["status"].startswith("200")
    assert response["json"]["updated_count"] == 1

    state_response = call_app(app, "GET", "/api/state")
    document = state_response["json"]["documents"][0]
    assert document["title"] == "After"
    assert document["topics"] == ["Finance"]


def test_rescan_route_respects_since_filter(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    old_file = tmp_path / "old.pdf"
    old_file.write_bytes(b"old-pdf")
    new_file = tmp_path / "new.pdf"
    new_file.write_bytes(b"new-pdf")

    db = Database(app.db_path)
    db.initialize()
    db.add_document(
        file_hash="old-hash",
        file_path=str(old_file),
        title="Old Doc",
        authors="Alice",
        summary="Old summary.",
        topics=["Other"],
        file_modified_at="2026-01-01T00:00:00+00:00",
    )
    db.add_document(
        file_hash="new-hash",
        file_path=str(new_file),
        title="New Doc",
        authors="Bob",
        summary="New summary.",
        topics=["Other"],
        file_modified_at=datetime.now(timezone.utc).isoformat(),
    )
    db.close()

    with patch("docu_tracker.web.extract_text", return_value="rescanned text"):
        with patch("docu_tracker.web.analyze_document") as mock_analyze:
            mock_analyze.return_value = {
                "title": "Updated Doc",
                "authors": ["Alice"],
                "summary": "Updated summary.",
                "topics": ["Work"],
            }
            response = call_app(
                app,
                "POST",
                "/api/rescan",
                {"since": "7d"},
            )

    assert response["status"].startswith("200")
    assert response["json"]["updated_count"] == 1
    assert mock_analyze.call_count == 1

    state_response = call_app(app, "GET", "/api/state")
    docs_by_title = {doc["title"]: doc for doc in state_response["json"]["documents"]}
    assert "Old Doc" in docs_by_title
    assert docs_by_title["Updated Doc"]["topics"] == ["Work"]


def test_open_route_streams_primary_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"pdf-bytes")
    seed_document(app, file_path)

    response = call_app(app, "GET", "/api/documents/1/open")

    assert response["status"].startswith("200")
    assert response["body"] == b"pdf-bytes"
    header_map = {name.lower(): value for name, value in response["headers"]}
    assert "x-document-title" not in header_map
    assert "filename*" in header_map["content-disposition"]


def test_last_session_close_triggers_shutdown(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    shutdown_calls = []

    class ImmediateTimer:
        def __init__(self, interval, callback):
            self.callback = callback
            self.daemon = False

        def start(self):
            self.callback()

        def cancel(self):
            pass

    app.shutdown_callback = lambda: shutdown_calls.append(True)

    with patch("docu_tracker.web.threading.Timer", ImmediateTimer):
        open_response = call_app(
            app,
            "POST",
            "/api/session/open",
            {"session_id": "tab-1"},
        )
        close_response = call_app(
            app,
            "POST",
            "/api/session/close",
            {"session_id": "tab-1"},
        )

    assert open_response["status"].startswith("200")
    assert close_response["status"].startswith("200")
    assert shutdown_calls == [True]



def test_serve_web_app_falls_back_when_port_is_busy(tmp_path):
    calls = []

    class FakeServer:
        server_port = 9123

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def shutdown(self):
            pass

        def serve_forever(self):
            pass

    def fake_make_server(host, port, app, server_class, handler_class):
        calls.append((host, port))
        if len(calls) == 1:
            raise OSError(errno.EADDRINUSE, "Address already in use")
        return FakeServer()

    with patch("docu_tracker.web.make_server", fake_make_server):
        DocuTrackerWebApp(config_dir=str(tmp_path / "config"), cwd=str(tmp_path))
        from docu_tracker.web import serve_web_app

        serve_web_app(host="127.0.0.1", port=8421, config_dir=str(tmp_path / "config"), cwd=str(tmp_path))

    assert calls == [("127.0.0.1", 8421), ("127.0.0.1", 0)]


def test_notebook_topics_round_trip(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    created = call_app(
        app, "POST", "/api/notebook",
        {"title": "Topic note", "topics": ["Work"]},
    )
    assert created["status"].startswith("201")
    assert created["json"]["note"]["topics"] == ["Work"]

    note_id = created["json"]["note"]["id"]
    updated = call_app(
        app, "PATCH", f"/api/notebook/{note_id}",
        {"topics": ["Academic"]},
    )
    assert updated["json"]["note"]["topics"] == ["Academic"]

    bad = call_app(
        app, "POST", "/api/notebook",
        {"title": "Bad", "topics": "Work"},
    )
    assert bad["status"].startswith("400")


def test_web_command_invokes_server(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("DOCU_TRACKER_DIR", str(config_dir))
    runner = CliRunner()

    with patch("docu_tracker.web.serve_web_app") as mock_server:
        result = runner.invoke(cli, ["web", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    mock_server.assert_called_once()
