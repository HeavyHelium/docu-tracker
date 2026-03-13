import hashlib
import os
import pytest
from docu_tracker.scanner import scan_directory, compute_file_hash


@pytest.fixture
def downloads_dir(tmp_path):
    """Create a fake downloads directory with test files."""
    (tmp_path / "paper1.pdf").write_bytes(b"fake pdf content 1")
    (tmp_path / "paper2.docx").write_bytes(b"fake docx content")
    (tmp_path / "notes.txt").write_bytes(b"plain text")
    (tmp_path / "image.png").write_bytes(b"fake image")
    return tmp_path


def test_scan_finds_pdf_and_docx(downloads_dir):
    """Should find only PDF and DOCX files."""
    files = scan_directory(str(downloads_dir))
    extensions = {os.path.splitext(f)[1] for f in files}
    assert extensions == {".pdf", ".docx"}
    assert len(files) == 2


def test_scan_returns_absolute_paths(downloads_dir):
    """All returned paths should be absolute."""
    files = scan_directory(str(downloads_dir))
    for f in files:
        assert os.path.isabs(f)


def test_scan_empty_directory(tmp_path):
    """Should return empty list for directory with no documents."""
    files = scan_directory(str(tmp_path))
    assert files == []


def test_scan_nonexistent_directory():
    """Should return empty list for nonexistent directory."""
    files = scan_directory("/nonexistent/dir")
    assert files == []


def test_compute_file_hash(tmp_path):
    """Should compute SHA-256 hash of file content."""
    path = tmp_path / "test.pdf"
    content = b"test content"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert compute_file_hash(str(path)) == expected
