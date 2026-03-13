import hashlib
import os

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def scan_directory(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    files = []
    for name in os.listdir(directory):
        ext = os.path.splitext(name)[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            files.append(os.path.join(directory, name))
    return sorted(files)


def compute_file_hash(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
