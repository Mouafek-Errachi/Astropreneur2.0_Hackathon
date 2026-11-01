"""Render the summary + HR notes to Word or PDF. RTL is decided per paragraph from its first strong letter."""
import html
import io
import os
import unicodedata
from datetime import date
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

SECTIONS = [
    ("background", "Background", "text"),
    ("strengths", "Key strengths", "list"),
    ("concerns", "Concerns or gaps", "list"),
    ("notable_answers", "Notable answers", "list"),
    ("role_fit", "Role fit", "text"),
]
NOTES_TITLE = "HR notes"
NOTES_HINT = "Written by the interviewer. Not part of the AI summary."
FOOTER = "AI-generated from the interview transcript and reviewed by the interviewer. Not a hiring recommendation."


def is_rtl(text: str) -> bool:
    for ch in text:
        d = unicodedata.bidirectional(ch)
        if d in ("R", "AL"):
            return True
        if d == "L":
            return False
    return False


def _title(job_title: str) -> str:
    return f"Interview summary: {job_title}" if job_title else "Interview summary"


def _items(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [v for v in value if v and v.strip()]


# ---------------------------------------------------------------- DOCX

def _rtl_paragraph(p, text: str):
    if is_rtl(text):
        ppr = p._p.get_or_add_pPr()
        ppr.append(OxmlElement("w:bidi"))
        # No explicit alignment: under w:bidi the default "start" edge is the right side.
        for r in p.runs:
            r._r.get_or_add_rPr().append(OxmlElement("w:rtl"))


def _add(doc, text: str, style: str | None = None, italic=False, color=None, size=None):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    run.italic = italic
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if size:
        run.font.size = Pt(size)
    _rtl_paragraph(p, text)
    return p


def build_docx(summary: dict, notes: list[str], job_title: str = "") -> bytes:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:cs"), "Arial")

    doc.add_heading(_title(job_title), level=0)
    _add(doc, date.today().strftime("%d %B %Y"), color="647084", size=10)

    for key, title, kind in SECTIONS:
        doc.add_heading(title, level=2)
        items = _items(summary.get(key, ""))
        if not items:
            _add(doc, "None noted.", italic=True, color="647084")
        elif kind == "text":
            for t in items:
                _add(doc, t)
        else:
            for t in items:
                _add(doc, t, style="List Bullet")

    note_items = _items(notes)
    if note_items:
        doc.add_heading(NOTES_TITLE, level=2)
        _add(doc, NOTES_HINT, italic=True, color="647084", size=9)
        for t in note_items:
            _add(doc, t, style="List Bullet")

    _add(doc, FOOTER, italic=True, color="647084", size=9)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF (WeasyPrint: real bidi + Arabic shaping)

_CSS = """
@page { size: A4; margin: 22mm 20mm; @bottom-center { content: counter(page); font-size: 9pt; color: #647084; } }
body { font-family: "Noto Sans", "Noto Sans Hebrew", "Noto Sans Arabic", "DejaVu Sans", Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.5; color: #1a2230; }
h1 { font-size: 19pt; margin: 0 0 2mm; }
h2 { font-size: 12.5pt; margin: 7mm 0 2mm; padding-bottom: 1mm; border-bottom: 1px solid #dde2ea; color: #2f55d4; }
.date, .hint, .none, .footer { color: #647084; }
.date { margin: 0 0 2mm; font-size: 9.5pt; }
.hint, .footer { font-size: 9pt; font-style: italic; }
.none { font-style: italic; }
p { margin: 0 0 2mm; text-align: start; }
ul { margin: 0 0 2mm; padding-inline-start: 6mm; }
li { margin-bottom: 1.5mm; text-align: start; }
.notes { margin-top: 8mm; padding: 3mm 4mm; background: #f4f6fb; border-inline-start: 3px solid #2f55d4; }
.notes h2 { margin-top: 0; }
.footer { margin-top: 10mm; }
"""


def _d(text: str) -> str:
    return 'dir="rtl"' if is_rtl(text) else 'dir="ltr"'


def _p(text: str, cls: str = "") -> str:
    return f'<p {_d(text)} class="{cls}">{html.escape(text)}</p>'


def _ul(items: list[str]) -> str:
    lis = "".join(f'<li {_d(t)}>{html.escape(t)}</li>' for t in items)
    return f'<ul {_d(items[0])}>{lis}</ul>'


def build_pdf_html(summary: dict, notes: list[str], job_title: str = "") -> str:
    parts = [f"<h1 {_d(_title(job_title))}>{html.escape(_title(job_title))}</h1>",
             f'<p class="date">{date.today().strftime("%d %B %Y")}</p>']
    for key, title, kind in SECTIONS:
        parts.append(f"<h2>{title}</h2>")
        items = _items(summary.get(key, ""))
        if not items:
            parts.append('<p class="none">None noted.</p>')
        elif kind == "text":
            parts += [_p(t) for t in items]
        else:
            parts.append(_ul(items))
    note_items = _items(notes)
    if note_items:
        parts.append(f'<div class="notes"><h2>{NOTES_TITLE}</h2>{_p(NOTES_HINT, "hint")}{_ul(note_items)}</div>')
    parts.append(f'<p class="footer">{html.escape(FOOTER)}</p>')
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(_title(job_title))}</title>' \
           f'<style>{_CSS}</style></head><body>{"".join(parts)}</body></html>'


_weasy_ok: bool | None = None


def build_pdf(summary: dict, notes: list[str], job_title: str = "") -> bytes:
    global _weasy_ok
    if _weasy_ok is None:
        try:
            import weasyprint  # noqa: F401  needs Pango on the server
            _weasy_ok = True
        except OSError:        # no Pango (e.g. Windows without GTK): pure-Python fallback
            _weasy_ok = False
    if not _weasy_ok:
        return build_pdf_fpdf(summary, notes, job_title)
    from weasyprint import HTML
    return HTML(string=build_pdf_html(summary, notes, job_title)).write_pdf()


# ---------------------------------------------------------------- PDF fallback (fpdf2 + HarfBuzz shaping)

_FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
              Path("/usr/share/fonts/truetype/noto"), Path("/usr/share/fonts/truetype/dejavu"),
              Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
# (family, regular, bold, italic): first one found is the main font, the rest are glyph fallbacks
_FONTS = [("main", ["arial.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf", "Arial.ttf"],
           ["arialbd.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
           ["ariali.ttf", "NotoSans-Italic.ttf", "DejaVuSans-Oblique.ttf", "Arial Italic.ttf"]),
          ("hebrew", ["NotoSansHebrew-Regular.ttf"], [], []),
          ("arabic", ["NotoSansArabic-Regular.ttf"], [], [])]


def _find_font(names: list[str]) -> str | None:
    for d in _FONT_DIRS:
        for n in names:
            if (d / n).is_file():
                return str(d / n)
    return None


def build_pdf_fpdf(summary: dict, notes: list[str], job_title: str = "") -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(20, 22, 20)
    pdf.set_auto_page_break(True, 22)
    fallbacks = []
    for family, reg, bold, ital in _FONTS:
        r = _find_font(reg)
        if not r:
            continue
        pdf.add_font(family, "", r)
        pdf.add_font(family, "B", _find_font(bold) or r)
        pdf.add_font(family, "I", _find_font(ital) or r)
        if family != "main":
            fallbacks.append(family)
    if "main" not in pdf.fonts:  # fpdf2 keys fonts as family+style
        raise RuntimeError("No TrueType font found for the PDF")
    pdf.set_fallback_fonts(fallbacks)
    try:
        pdf.set_text_shaping(True)  # HarfBuzz: Arabic joining + bidi
        shaping = True
    except Exception:
        shaping = False
    pdf.add_page()
    width = pdf.epw

    def line(text, size=10.5, style="", color=(26, 34, 48), bullet=False, gap=2):
        pdf.set_font("main", style, size)
        pdf.set_text_color(*color)
        rtl = is_rtl(text)
        if shaping:
            pdf.set_text_shaping(True, direction="rtl" if rtl else "ltr")
        body = f"• {text}" if bullet else text  # logical start: the right edge under rtl shaping
        pdf.multi_cell(width, size * 0.55, body, align="R" if rtl else "L",
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)

    def heading(text):
        pdf.ln(3)
        line(text, 12.5, "B", (47, 85, 212), gap=0)
        pdf.set_draw_color(221, 226, 234)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(2)

    grey = (100, 112, 132)
    line(_title(job_title), 19, "B", gap=1)
    line(date.today().strftime("%d %B %Y"), 9.5, color=grey)
    for key, title, kind in SECTIONS:
        heading(title)
        items = _items(summary.get(key, ""))
        if not items:
            line("None noted.", style="I", color=grey)
        for t in items:
            line(t, bullet=kind == "list")
    note_items = _items(notes)
    if note_items:
        heading(NOTES_TITLE)
        line(NOTES_HINT, 9, "I", grey)
        for t in note_items:
            line(t, bullet=True)
    pdf.ln(6)
    line(FOOTER, 9, "I", grey)
    return bytes(pdf.output())
