"""File checks (magic bytes, size, page count, zip bombs) and text extraction for every resume format.

Text a human reader would not see (white or tiny fonts, hidden Word/ODT/RTF runs, hidden HTML) is kept out of the
text the LLM reads and returned separately as hidden_text, so the injection scanner can flag it."""
import io
import json
import re
import zipfile
from dataclasses import dataclass, field

import magic

from backend.config import get_settings
from backend.core.sanitizer import html_to_text, sanitize_text

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ODT = "application/vnd.oasis.opendocument.text"
RTF = "text/rtf"
HTML = "text/html"
JSON = "application/json"
TXT = "text/plain"
JPEG, PNG, WEBP = "image/jpeg", "image/png", "image/webp"
IMAGES = {JPEG, PNG, WEBP}
ALLOWED = {PDF, DOCX, ODT, RTF, HTML, JSON, TXT} | IMAGES

MAX_ZIP_ENTRIES = 500
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100  # a CV never compresses 100x; zip bombs do
MAX_IMAGE_PIXELS = 25_000_000


class IngestionError(Exception):
    pass


@dataclass
class IngestedDoc:
    mime: str
    text: str
    notes: list[str] = field(default_factory=list)
    hidden_text: str = ""


def _zip_names(data: bytes) -> list[str]:
    try:
        return zipfile.ZipFile(io.BytesIO(data)).namelist()
    except zipfile.BadZipFile:
        return []


def detect_mime(data: bytes) -> str:
    mime = magic.from_buffer(data[:8192], mime=True)
    if data[:2] == b"PK":
        names = _zip_names(data)
        if "word/document.xml" in names:
            return DOCX
        if "content.xml" in names and data[38:38 + len(ODT)] == ODT.encode():  # ODF: 'mimetype' is the first entry
            return ODT
    if data[:5] == b"{\\rtf":
        return RTF
    if mime in (TXT, HTML, JSON, "text/xml", "application/octet-stream"):
        head = data[:4096].decode("utf-8", errors="ignore").lstrip()
        if head.startswith(("{", "[")):
            try:
                json.loads(data.decode("utf-8"))
                return JSON
            except ValueError:
                pass
        if re.match(r"<(!doctype html|html|head|body|div|p|section|article)\b", head, re.I):
            return HTML
    return mime


def check_zip(data: bytes) -> zipfile.ZipFile:
    """Refuses zip bombs before anything is decompressed: too many entries, too large, or too compressed."""
    z = zipfile.ZipFile(io.BytesIO(data))
    infos = z.infolist()
    total = sum(i.file_size for i in infos)
    if len(infos) > MAX_ZIP_ENTRIES or total > MAX_UNZIPPED_BYTES or total > MAX_ZIP_RATIO * max(len(data), 1):
        raise IngestionError("archive expands too much (possible zip bomb)")
    return z


# ------------------------------------------------------------------ PDF
def _white(color) -> bool:
    if not isinstance(color, (tuple, list)) or not color or not all(isinstance(c, (int, float)) for c in color):
        return False
    if len(color) == 4:  # CMYK: white is no ink
        return all(c <= 0.05 for c in color)
    return all(c >= 0.95 for c in color)


def _pdf(data: bytes, max_pages: int) -> tuple[str, str]:
    import pdfplumber

    visible, hidden = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > max_pages:
            raise IngestionError(f"PDF has more than {max_pages} pages")
        for page in pdf.pages:
            def is_hidden(c, page=page) -> bool:
                off_page = c["x1"] < 0 or c["x0"] > page.width or c["bottom"] < 0 or c["top"] > page.height
                return c.get("size", 10) < 2 or _white(c.get("non_stroking_color")) or off_page

            hidden.append("".join(c["text"] for c in page.chars if is_hidden(c)))
            shown = page.filter(lambda o: o.get("object_type") != "char" or not is_hidden(o))
            visible.append(shown.extract_text() or "")
    return "\n".join(visible), " ".join(h for h in hidden if h.strip())


# ------------------------------------------------------------------ Word
def _docx(data: bytes) -> tuple[str, str]:
    import docx

    check_zip(data)
    doc = docx.Document(io.BytesIO(data))
    paragraphs = list(doc.paragraphs) + [p for t in doc.tables for row in t.rows for cell in row.cells
                                         for p in cell.paragraphs]
    visible, hidden = [], []
    for p in paragraphs:
        line = []
        for run in p.runs:
            f = run.font
            try:
                white = f.color is not None and f.color.type is not None and str(f.color.rgb) == "FFFFFF"
            except (AttributeError, ValueError):
                white = False
            if f.hidden or white or (f.size is not None and f.size.pt < 2):
                hidden.append(run.text)
            else:
                line.append(run.text)
        visible.append("".join(line))
    return "\n".join(visible), " ".join(h for h in hidden if h.strip())


# ------------------------------------------------------------------ OpenDocument
_ODF = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
        "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"}


def _odt(data: bytes) -> tuple[str, str]:
    from lxml import etree

    xml = check_zip(data).read("content.xml")
    root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    t, st, fo = (f"{{{_ODF[k]}}}" for k in ("text", "style", "fo"))
    hidden_styles = set()
    for s in root.iter(f"{st}style"):
        props = s.find(f"{st}text-properties")
        if props is not None and (props.get(f"{t}display") == "none" or props.get(f"{fo}color", "").lower() == "#ffffff"
                                  or props.get(f"{fo}font-size", "").startswith(("0pt", "1pt", "0.", "1."))):
            hidden_styles.add(s.get(f"{st}name"))

    def is_hidden(el) -> bool:
        while el is not None:
            if el.tag in (f"{t}hidden-text", f"{t}hidden-paragraph") or el.get(f"{t}style-name") in hidden_styles:
                return True
            el = el.getparent()
        return False

    visible, hidden = [], []
    for block in root.iter(f"{t}p", f"{t}h"):
        line = []
        for el in [block, *block.iterdescendants()]:
            for chunk, owner in ((el.text, el), (el.tail, el.getparent()) if el is not block else (None, None)):
                if chunk:
                    (hidden if is_hidden(owner) else line).append(chunk)
        visible.append("".join(line))
    return "\n".join(visible), " ".join(h for h in hidden if h.strip())


# ------------------------------------------------------------------ RTF
_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "object", "themedata",
             "listtable", "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "latentstyles"}
_RTF_TOKEN = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|([^\\{}\r\n]+)", re.I)


def _rtf(data: bytes) -> tuple[str, str]:
    """Small RTF reader: skips non-text destinations, follows \\v (hidden text) per group."""
    visible, hidden = [], []
    stack, skip, vanish, uc_skip = [], False, False, 0
    for m in _RTF_TOKEN.finditer(data.decode("latin-1")):
        word, arg, hexchar, sym, brace, text = m.groups()
        if brace == "{":
            stack.append((skip, vanish))
            continue
        if brace == "}":
            skip, vanish = stack.pop() if stack else (False, False)
            continue
        out = None
        if word:
            if word in _RTF_SKIP:
                skip = True
            elif word == "v":
                vanish = arg != "0"
            elif word in ("par", "line", "row"):
                out = "\n"
            elif word in ("tab", "cell"):
                out = "\t"
            elif word == "u" and arg:
                out, uc_skip = chr(int(arg) % 0x10000), 1
        elif sym == "*":
            skip = True
        elif sym in ("\\", "{", "}"):
            out = sym
        elif hexchar:
            if uc_skip:
                uc_skip -= 1
                continue
            out = bytes([int(hexchar, 16)]).decode("cp1252", errors="replace")
        elif text:
            out = text
        if out and not skip:
            (hidden if vanish else visible).append(out)
    return "".join(visible), "".join(hidden).strip()


# ------------------------------------------------------------------ JSON profiles
def profile_to_text(obj, max_depth: int = 6) -> str:
    """A structured profile (job board export, JSON Resume, or any JSON) as 'field: value' lines, so it goes through
    the same sanitising, injection scan and parser as any other resume. Every string field is included."""
    lines, names = [], []

    def walk(v, key: str, depth: int, in_list: bool = False) -> None:
        if depth > max_depth:
            raise IngestionError("profile JSON is nested too deeply")
        if isinstance(v, dict):
            for k, x in v.items():
                # [{"name": "python"}] under "skills" reads "skills: python"
                label = key if in_list and k in ("name", "value", "keyword") else re.sub(r"[_\-]+", " ", str(k))[:40]
                walk(x, label, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, key, depth + 1, in_list=True)
        elif v not in (None, ""):
            if key.lower() in ("name", "full name", "fullname") and depth <= 2:
                names.append(str(v))  # the person's name goes first, like at the top of a resume
            lines.append(f"{key}: {v}" if key else str(v))

    walk(obj, "", 0)
    return "\n".join(names[:1] + lines)


# ------------------------------------------------------------------ images
def _image(data: bytes, mime: str) -> str:
    from PIL import Image

    from backend.core.llm import LLMUnavailable, transcribe_image

    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                raise IngestionError("image too large")
            im.verify()
    except IngestionError:
        raise
    except Exception:
        raise IngestionError("not a valid image")
    try:
        return transcribe_image(data, mime)
    except LLMUnavailable as e:
        raise IngestionError(f"images need the AI to read them ({e})")


def ingest(data: bytes) -> IngestedDoc:
    s = get_settings()
    if not data:
        raise IngestionError("empty file")
    if len(data) > s.max_upload_bytes:
        raise IngestionError(f"file larger than {s.max_upload_mb} MB")
    mime = detect_mime(data)
    if mime not in ALLOWED:
        raise IngestionError(f"file type not allowed: {mime}")

    hidden, extra = "", []
    try:
        if mime == PDF:
            raw, hidden = _pdf(data, s.max_pdf_pages)
        elif mime == DOCX:
            raw, hidden = _docx(data)
        elif mime == ODT:
            raw, hidden = _odt(data)
        elif mime == RTF:
            raw, hidden = _rtf(data)
        elif mime == HTML:
            raw, hidden = html_to_text(data.decode("utf-8", errors="replace"))
            extra.append("html_stripped")
        elif mime == JSON:
            raw = profile_to_text(json.loads(data.decode("utf-8")))
        elif mime in IMAGES:
            raw = _image(data, mime)
            extra.append("image_transcribed")
        else:
            raw = data.decode("utf-8", errors="replace")
    except IngestionError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError) as e:
        raise IngestionError(f"could not read the file ({type(e).__name__})")

    text, notes = sanitize_text(raw, s.max_text_chars)
    hidden, _ = sanitize_text(hidden, s.max_text_chars)
    if len(text) < 20:
        raise IngestionError("no extractable text")
    return IngestedDoc(mime=mime, text=text, notes=notes + extra, hidden_text=hidden)
