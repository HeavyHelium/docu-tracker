import os

PDF_MAX_PAGES = 4
DOCX_MAX_CHARS = 5000


def extract_text(file_path: str) -> str:
    if not os.path.exists(file_path):
        return ""

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".docx":
        return _extract_docx(file_path)
    else:
        return ""


def _extract_pdf(file_path: str) -> str:
    try:
        import fitz
        doc = fitz.open(file_path)
        pages = min(len(doc), PDF_MAX_PAGES)
        text = ""
        for i in range(pages):
            text += doc[i].get_text()
        doc.close()
        return text
    except Exception:
        return ""


def _extract_docx(file_path: str) -> str:
    try:
        from docx import Document
        doc = Document(file_path)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
            if len(text) >= DOCX_MAX_CHARS:
                return text[:DOCX_MAX_CHARS]
        return text
    except Exception:
        return ""
