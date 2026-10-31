"""File guard: magic-byte type check, size/page/zip-bomb limits, and text extraction that
separates VISIBLE text from HIDDEN text (white or tiny fonts in PDFs, hidden runs in DOCX).
Only the visible text ever reaches the agents."""
import io
import zipfile
from dataclasses import dataclass, field

import magic

from backend.config import get_settings
from backend.security.guard.sanitizer import sanitize_text

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"
ALLOWED = {PDF, DOCX, TXT}
MAX_DOCX_UNCOMPRESSED = 30 * 1024 * 1024
MAX_ZIP_ENTRIES = 1000


class FileRejected(Exception):
    pass


@dataclass
class ExtractedDoc:
    mime: str
    text: str
    hidden_text: str = ""
    notes: list[str] = field(default_factory=list)


def detect_mime(data: bytes) -> str:
    mime = magic.from_buffer(data[:8192], mime=True)
    if data[:2] == b"PK":
        try:
            if "word/document.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist():
                return DOCX
        except zipfile.BadZipFile:
            pass
    return mime


def _is_white(color) -> bool:
    if color is None:
        return False
    if isinstance(color, (int, float)):
        color = (color,)
    try:
        vals = [float(c) for c in color]
    except (TypeError, ValueError):
        return False
    if len(vals) == 1:
        return vals[0] >= 0.95  # grayscale
    if len(vals) == 3:
        return all(v >= 0.95 for v in vals)  # RGB
    if len(vals) == 4:
        return all(v <= 0.05 for v in vals)  # CMYK white
    return False


def _pdf(data: bytes, max_pages: int) -> tuple[str, str]:
    import pdfplumber

    visible, hidden = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > max_pages:
            raise FileRejected(f"PDF has more than {max_pages} pages")
        for page in pdf.pages:
            w, h = page.width, page.height

            def is_hidden(obj) -> bool:
                return obj.get("object_type") == "char" and (
                    obj.get("size", 10) < 2
                    or _is_white(obj.get("non_stroking_color"))
                    or obj.get("x0", 0) > w or obj.get("top", 0) > h or obj.get("x1", 1) < 0
                )

            visible.append(page.filter(lambda o: not is_hidden(o)).extract_text() or "")
            hidden.append(page.filter(lambda o: o.get("object_type") == "char" and is_hidden(o)).extract_text() or "")
    return "\n".join(visible), " ".join(t for t in hidden if t.strip())


def _docx(data: bytes) -> tuple[str, str]:
    import docx

    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_DOCX_UNCOMPRESSED:
        raise FileRejected("DOCX archive too large (possible zip bomb)")
    doc = docx.Document(io.BytesIO(data))
    paragraphs = list(doc.paragraphs) + [p for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    visible, hidden = [], []
    for p in paragraphs:
        vis_line = []
        for run in p.runs:
            f = run.font
            rgb = f.color.rgb if f.color is not None and f.color.type is not None else None
            tiny = f.size is not None and f.size.pt < 2
            if f.hidden or tiny or (rgb is not None and str(rgb).upper() == "FFFFFF"):
                hidden.append(run.text)
            else:
                vis_line.append(run.text)
        visible.append("".join(vis_line))
    return "\n".join(visible), " ".join(t for t in hidden if t.strip())


def extract(data: bytes) -> ExtractedDoc:
    s = get_settings()
    if not data:
        raise FileRejected("empty file")
    if len(data) > s.max_upload_bytes:
        raise FileRejected(f"file larger than {s.max_upload_mb} MB")
    mime = detect_mime(data)
    if mime not in ALLOWED:
        raise FileRejected(f"file type not allowed: {mime}")
    try:
        if mime == PDF:
            raw, hidden = _pdf(data, s.max_pdf_pages)
        elif mime == DOCX:
            raw, hidden = _docx(data)
        else:
            raw, hidden = data.decode("utf-8", errors="replace"), ""
    except FileRejected:
        raise
    except Exception as e:  # corrupted / encrypted files
        raise FileRejected(f"could not read file: {type(e).__name__}")
    text, notes = sanitize_text(raw, s.max_text_chars)
    hidden_clean, _ = sanitize_text(hidden, 5000)
    if len(text) < 20:
        raise FileRejected("no extractable text")
    return ExtractedDoc(mime=mime, text=text, hidden_text=hidden_clean, notes=notes)
