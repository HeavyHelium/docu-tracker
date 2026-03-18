import json
from datetime import datetime, timezone
from unittest.mock import patch

from click.testing import CliRunner

from docu_tracker.cli import cli
from docu_tracker.db import Database
from docu_tracker.web import DocuTrackerWebApp, build_test_environ


def call_app(app, method="GET", path="/", payload=None):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(
        app(
            build_test_environ(method=method, path=path, payload=payload),
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


def test_scan_route_adds_document(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    app = DocuTrackerWebApp(config_dir=str(config_dir), cwd=str(tmp_path))

    scan_dir = tmp_path / "downloads"
    scan_dir.mkdir()
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
        file_modified_at="2026-03-18T00:00:00+00:00",
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


def test_web_command_invokes_server(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("DOCU_TRACKER_DIR", str(config_dir))
    runner = CliRunner()

    with patch("docu_tracker.web.serve_web_app") as mock_server:
        result = runner.invoke(cli, ["web", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    mock_server.assert_called_once()
