"""Normalise untrusted text before it reaches the LLM or the UI."""
import html
import re
import unicodedata
from html.parser import HTMLParser

ZERO_WIDTH = re.compile(r"[​‌‍‎‏⁠⁡⁢⁣⁤﻿­᠎]")
BIDI = re.compile(r"[‪-‮⁦-⁩]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
TAG_CHARS = re.compile(r"[\U000E0000-\U000E007F]")  # invisible "tag" unicode block used for smuggling
LOOKS_LIKE_HTML = re.compile(r"<\s*(html|body|div|p|span|br|script|style|table|a|img|iframe)\b", re.I)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "head", "title", "noscript", "template", "iframe", "object"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_parts: list[str] = []
        self._skip = 0
        self._hidden_stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        hidden = any(k in style for k in ("display:none", "visibility:hidden", "font-size:0", "color:#fff", "color:white", "opacity:0"))
        hidden = hidden or "hidden" in dict(attrs)
        if tag not in ("br", "img", "hr", "meta", "link", "input"):
            self._hidden_stack.append(hidden)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if self._hidden_stack and tag not in ("br", "img", "hr", "meta", "link", "input"):
            self._hidden_stack.pop()
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if any(self._hidden_stack):
            self.hidden_parts.append(data)
        else:
            self.parts.append(data)


def html_to_text(raw: str) -> tuple[str, str]:
    """Returns (visible_text, hidden_text)."""
    p = _TextExtractor()
    try:
        p.feed(raw)
        p.close()
    except Exception:  # malformed HTML: fall back to crude tag strip
        return re.sub(r"<[^>]+>", " ", raw), ""
    return "".join(p.parts), " ".join(p.hidden_parts)


def sanitize_text(text: str, max_chars: int = 30_000) -> tuple[str, list[str]]:
    """Returns (clean_text, notes). Notes describe what was removed (used as risk signals)."""
    notes: list[str] = []
    if not text:
        return "", notes
    text = unicodedata.normalize("NFKC", text)
    if ZERO_WIDTH.search(text):
        notes.append("zero_width_chars")
        text = ZERO_WIDTH.sub("", text)
    if BIDI.search(text):
        notes.append("bidi_override_chars")
        text = BIDI.sub("", text)
    if TAG_CHARS.search(text):
        notes.append("unicode_tag_chars")
        text = TAG_CHARS.sub("", text)
    if CONTROL.search(text):
        text = CONTROL.sub(" ", text)
    if LOOKS_LIKE_HTML.search(text):
        visible, hidden = html_to_text(text)
        notes.append("html_stripped")
        if hidden.strip():
            notes.append("html_hidden_text")
        text = visible
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) > max_chars:
        notes.append("truncated")
        text = text[:max_chars]
    return text, notes


def safe_display_filename(name: str) -> str:
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120]
    return name or "file"
