"""File checks (magic bytes, size, page count) and text extraction.
TODO: detect hidden text (white / tiny fonts, hidden docx runs), zip-bomb checks."""
import io
import zipfile
from dataclasses import dataclass, field

import magic

from backend.config import get_settings
from backend.core.sanitizer import sanitize_text

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"
ALLOWED = {PDF, DOCX, TXT}


class IngestionError(Exception):
    pass


@dataclass
class IngestedDoc:
    mime: str
    text: str
    notes: list[str] = field(default_factory=list)


def detect_mime(data: bytes) -> str:
    mime = magic.from_buffer(data[:8192], mime=True)
    if mime in ("application/zip", "application/octet-stream") and data[:2] == b"PK":
        try:
            if "word/document.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist():
                return DOCX
        except zipfile.BadZipFile:
            pass
    return mime


def ingest(data: bytes) -> IngestedDoc:
    s = get_settings()
    if not data:
        raise IngestionError("empty file")
    if len(data) > s.max_upload_bytes:
        raise IngestionError(f"file larger than {s.max_upload_mb} MB")
    mime = detect_mime(data)
    if mime not in ALLOWED:
        raise IngestionError(f"file type not allowed: {mime}")

    if mime == PDF:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > s.max_pdf_pages:
                raise IngestionError(f"PDF has more than {s.max_pdf_pages} pages")
            raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
    elif mime == DOCX:
        import docx

        raw = "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    else:
        raw = data.decode("utf-8", errors="replace")

    text, notes = sanitize_text(raw, s.max_text_chars)
    if len(text) < 20:
        raise IngestionError("no extractable text")
    return IngestedDoc(mime=mime, text=text, notes=notes)
