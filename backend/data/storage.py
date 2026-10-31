"""Uploads go to quarantine, then get promoted under a random UUID name into a non-public dir."""
import os
import uuid
from pathlib import Path

from backend.config import get_settings

EXT_FOR_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "text/rtf": ".rtf",
    "text/html": ".html.txt",  # never served as HTML
    "application/json": ".json",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def quarantine(data: bytes) -> Path:
    path = get_settings().quarantine_dir / f"{uuid.uuid4().hex}.bin"
    path.write_bytes(data)
    return path


def promote(qpath: Path, mime: str) -> str:
    name = f"{uuid.uuid4().hex}{EXT_FOR_MIME.get(mime, '.bin')}"
    os.replace(qpath, get_settings().storage_dir / name)
    return name


def discard(qpath: Path) -> None:
    qpath.unlink(missing_ok=True)
