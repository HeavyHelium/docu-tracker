import os
import time
import pytest
from click.testing import CliRunner
from unittest.mock import patch
import fitz
from docu_tracker.cli import cli


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """CLI runner with isolated config and DB."""
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir)
    monkeypatch.setenv("DOCU_TRACKER_DIR", config_dir)
    return CliRunner()


@pytest.fixture
def downloads_with_pdf(tmp_path):
    """Create a downloads dir with a real PDF."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Title: Test Paper\nAuthors: Alice\nAbstract: A test.")
    doc.save(str(downloads / "test.pdf"))
    doc.close()
    return str(downloads)


def test_topics_list(runner):
    """Should list default topics."""
    result = runner.invoke(cli, ["topics"])
    assert result.exit_code == 0
    assert "Work" in result.output
    assert "Other" in result.output


def test_topics_add(runner):
    """Should add a new topic."""
    result = runner.invoke(cli, ["topics", "add", "Alignment"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["topics"])
    assert "Alignment" in result.output


def test_topics_remove(runner):
    """Should remove a topic."""
    result = runner.invoke(cli, ["topics", "remove", "Finance"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["topics"])
    assert "Finance" not in result.output


def test_topics_remove_other_blocked(runner):
    """Should not allow removing Other."""
    result = runner.invoke(cli, ["topics", "remove", "Other"])
    assert result.exit_code != 0 or "Cannot remove" in result.output


def test_scan_finds_new_documents(runner, tmp_path, monkeypatch, downloads_with_pdf):
    """Scan should find and process new documents."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    with patch("docu_tracker.cli.analyze_document") as mock_analyze:
        mock_analyze.return_value = {
            "title": "Test Paper",
            "authors": ["Alice"],
            "summary": "A test paper.",
            "topics": ["Academic"],
        }
        result = runner.invoke(cli, ["scan", "--path", downloads_with_pdf])
    assert result.exit_code == 0
    assert "Test Paper" in result.output


def test_scan_skips_duplicates(runner, tmp_path, monkeypatch, downloads_with_pdf):
    """Second scan should detect duplicates."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    with patch("docu_tracker.cli.analyze_document") as mock_analyze:
        mock_analyze.return_value = {
            "title": "Test Paper",
            "authors": ["Alice"],
            "summary": "A test paper.",
            "topics": ["Academic"],
        }
        runner.invoke(cli, ["scan", "--path", downloads_with_pdf])
        result = runner.invoke(cli, ["scan", "--path", downloads_with_pdf])
    assert "Already tracked" in result.output


def test_scan_no_api_key(runner, tmp_path, monkeypatch, downloads_with_pdf):
    """Scan without API key should show error."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # avoid picking up .env from project root
    result = runner.invoke(cli, ["scan", "--path", downloads_with_pdf])
    assert result.exit_code != 0 or "API key" in result.output


def test_scan_since_filters_old_files(runner, tmp_path, monkeypatch):
    """Scan --since should skip files older than the cutoff."""
    downloads = tmp_path / "downloads_since"
    downloads.mkdir()

    # Create a PDF and backdate it to 30 days ago
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Old paper content")
    old_pdf = str(downloads / "old.pdf")
    doc.save(old_pdf)
    doc.close()
    old_time = time.time() - 30 * 86400
    os.utime(old_pdf, (old_time, old_time))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(cli, ["scan", "--path", str(downloads), "--since", "7d"])
    assert result.exit_code == 0
    assert "No PDF/DOCX files found" in result.output


def test_list_since_filters_old_docs(runner, tmp_path, monkeypatch):
    """List --since should exclude documents older than the cutoff."""
    _seed_documents(runner, tmp_path, monkeypatch)
    # Seeded docs have current mtime, so --since 1d should include them
    result = runner.invoke(cli, ["list", "--since", "1d"])
    assert "Paper A" in result.output
    # --since 0d (zero days = right now) should exclude them
    result = runner.invoke(cli, ["list", "--since", "0d"])
    assert "No documents found" in result.output or "Paper A" not in result.output


def _seed_documents(runner, tmp_path, monkeypatch):
    """Helper to seed the DB with test documents via scan."""
    downloads = tmp_path / "dl"
    downloads.mkdir(exist_ok=True)
    for name, content in [("a.pdf", b"pdf1"), ("b.pdf", b"pdf2")]:
        (downloads / name).write_bytes(content)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    responses = iter([
        {"title": "Paper A", "authors": ["Alice"], "summary": "Summary A.", "topics": ["Work"]},
        {"title": "Paper B", "authors": ["Bob"], "summary": "Summary B.", "topics": ["Finance", "Personal"]},
    ])
    with patch("docu_tracker.cli.analyze_document") as mock:
        mock.side_effect = lambda *a, **kw: next(responses)
        with patch("docu_tracker.cli.extract_text", return_value="some text"):
            runner.invoke(cli, ["scan", "--path", str(downloads)])


def test_list_shows_all_documents(runner, tmp_path, monkeypatch):
    """List should show all tracked documents."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "Paper A" in result.output
    assert "Paper B" in result.output


def test_list_filter_by_topic(runner, tmp_path, monkeypatch):
    """List --topic should filter."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["list", "--topic", "Work"])
    assert "Paper A" in result.output
    assert "Paper B" not in result.output


def test_list_filter_by_status(runner, tmp_path, monkeypatch):
    """List --status should filter."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["list", "--status", "unread"])
    assert "Paper A" in result.output


def test_list_week_grouping(runner, tmp_path, monkeypatch):
    """List --week should include week headers."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["list", "--week"])
    assert result.exit_code == 0
    assert "Week of" in result.output


def test_show_document(runner, tmp_path, monkeypatch):
    """Show should display full document details."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["show", "1"])
    assert result.exit_code == 0
    assert "Paper A" in result.output
    assert "Alice" in result.output
    assert "Summary A" in result.output


def test_show_nonexistent(runner):
    """Show for nonexistent ID should error."""
    result = runner.invoke(cli, ["show", "999"])
    assert "not found" in result.output.lower() or result.exit_code != 0


def test_mark_read(runner, tmp_path, monkeypatch):
    """mark-read should change status."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["mark-read", "1"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["show", "1"])
    assert "Status:   read" in result.output


def test_mark_reading(runner, tmp_path, monkeypatch):
    """mark-reading should change status to reading."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["mark-reading", "1"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["show", "1"])
    assert "Status:   reading" in result.output


def test_mark_unread(runner, tmp_path, monkeypatch):
    """mark-unread should change status back."""
    _seed_documents(runner, tmp_path, monkeypatch)
    runner.invoke(cli, ["mark-read", "1"])
    result = runner.invoke(cli, ["mark-unread", "1"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["show", "1"])
    assert "Status:   unread" in result.output


def test_tag_document(runner, tmp_path, monkeypatch):
    """tag should add a topic to a document."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["tag", "1", "Finance"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["show", "1"])
    assert "Finance" in result.output


def test_untag_document(runner, tmp_path, monkeypatch):
    """untag should remove a topic from a document."""
    _seed_documents(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["untag", "1", "Work"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["show", "1"])
    assert "Work" not in result.output


def test_reclassify_single_doc(runner, tmp_path, monkeypatch):
    """Reclassify --id should update topics on a single document."""
    _seed_documents(runner, tmp_path, monkeypatch)
    # Add a new topic
    runner.invoke(cli, ["topics", "add", "Alignment"])

    with patch("docu_tracker.cli.analyze_document") as mock:
        mock.return_value = {
            "title": "Paper A",
            "authors": ["Alice"],
            "summary": "Summary A.",
            "topics": ["Alignment"],
        }
        with patch("docu_tracker.cli.extract_text", return_value="some text"):
            result = runner.invoke(cli, ["reclassify", "--id", "1"])
    assert result.exit_code == 0
    assert "Alignment" in result.output
    # Verify the topic actually changed
    result = runner.invoke(cli, ["show", "1"])
    assert "Alignment" in result.output


def test_reclassify_all(runner, tmp_path, monkeypatch):
    """Reclassify without filters should update all documents."""
    _seed_documents(runner, tmp_path, monkeypatch)

    with patch("docu_tracker.cli.analyze_document") as mock:
        mock.return_value = {
            "title": "X",
            "authors": [],
            "summary": "X",
            "topics": ["Academic"],
        }
        with patch("docu_tracker.cli.extract_text", return_value="some text"):
            result = runner.invoke(cli, ["reclassify"])
    assert result.exit_code == 0
    assert "2 updated" in result.output


def test_web_opens_browser_by_default(runner):
    """web should open the browser and start the server."""
    with patch("docu_tracker.web.open_web_ui") as mock_open:
        with patch("docu_tracker.web.serve_web_app") as mock_serve:
            result = runner.invoke(cli, ["web"])
    assert result.exit_code == 0
    mock_open.assert_called_once_with(host="127.0.0.1", port=8421)
    mock_serve.assert_called_once()


def test_web_can_skip_browser(runner):
    """web --no-browser should only start the server."""
    with patch("docu_tracker.web.open_web_ui") as mock_open:
        with patch("docu_tracker.web.serve_web_app") as mock_serve:
            result = runner.invoke(cli, ["web", "--no-browser"])
    assert result.exit_code == 0
    mock_open.assert_not_called()
    mock_serve.assert_called_once()
