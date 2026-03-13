import os
import fitz  # PyMuPDF
from docx import Document
import pytest
from docu_tracker.extractor import extract_text


@pytest.fixture
def sample_pdf(tmp_path):
    """Create a minimal PDF with known text."""
    doc = fitz.open()
    for i in range(6):
        page = doc.new_page()
        text = f"Page {i+1} content. This is test text for page {i+1}."
        page.insert_text((72, 72), text)
    path = str(tmp_path / "sample.pdf")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def sample_docx(tmp_path):
    """Create a minimal DOCX with known text."""
    doc = Document()
    for i in range(20):
        doc.add_paragraph(f"Paragraph {i+1}. " + "Lorem ipsum dolor sit amet. " * 20)
    path = str(tmp_path / "sample.docx")
    doc.save(path)
    return path


def test_extract_text_from_pdf(sample_pdf):
    """Should extract text from first 4 pages only."""
    text = extract_text(sample_pdf)
    assert "Page 1 content" in text
    assert "Page 4 content" in text
    assert "Page 5 content" not in text


def test_extract_text_from_docx(sample_docx):
    """Should extract at most 5000 characters from DOCX."""
    text = extract_text(sample_docx)
    assert len(text) <= 5000
    assert "Paragraph 1" in text


def test_extract_text_unsupported_format(tmp_path):
    """Should return empty string for unsupported formats."""
    path = str(tmp_path / "readme.txt")
    with open(path, "w") as f:
        f.write("hello")
    text = extract_text(path)
    assert text == ""


def test_extract_text_nonexistent_file():
    """Should return empty string for missing files."""
    text = extract_text("/nonexistent/file.pdf")
    assert text == ""
