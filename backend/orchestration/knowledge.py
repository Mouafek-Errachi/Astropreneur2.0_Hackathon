"""Company documents the HR assistant answers from: the built-in policies (data/policies) plus documents HR uploads.

An uploaded file goes through the file guard (type, size, hidden text) and the injection scan. The assistant reads these
documents as trusted context, so one with hidden text or a likely injection is refused, not just flagged. The extracted
text is kept as Markdown under var/company_docs/<tenant>/, which the policy index reads next to data/policies: a
re-index keeps the uploads."""
import re
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session

from backend.config import get_settings
from backend.security import audit, events
from backend.security.guard import inspect_text
from backend.security.guard.files import FileRejected, extract
from backend.security.guard.sanitizer import safe_display_filename

SECTION_CHARS = 1200  # one indexed section: small enough that the excerpts sent to the AI stay focused


def docs_dir(tenant_id: int) -> Path:
    return get_settings().var_dir / "company_docs" / str(tenant_id)


def _builtin() -> list[Path]:
    return sorted((get_settings().data_dir / "policies").glob("*.md"))


def _title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").lstrip().splitlines()[:1]
    return first[0].lstrip("# ").strip() if first else path.stem


def _sections(text: str) -> list[tuple[str, str]]:
    """The document's own '## ' headings when it has them, otherwise paragraphs grouped into parts."""
    if re.search(r"^## ", text, re.M):
        parts = [p.strip().splitlines() for p in re.split(r"\n(?=## )", text) if p.strip()]
        return [(p[0].lstrip("# ").strip(), "\n".join(p[1:]).strip()) for p in parts if len(p) > 1]
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > SECTION_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para.strip()}".strip()
    if cur:
        out.append(cur)
    return [(f"Part {i}", body) for i, body in enumerate(out, 1)]


def list_docs(tenant_id: int) -> list[dict]:
    rows = [{"doc_id": p.stem, "title": _title(p), "kind": "built-in"} for p in _builtin()]
    rows += [{"doc_id": p.stem, "title": _title(p), "kind": "uploaded"} for p in sorted(docs_dir(tenant_id).glob("*.md"))]
    return rows


def add(session: Session, tenant_id: int, filename: str, data: bytes, actor: str) -> dict:
    name = safe_display_filename(filename)
    try:
        doc = extract(data)
    except FileRejected as e:
        events.record(session, tenant_id=tenant_id, source="file", kind="blocked_file", severity="medium",
                      detail={"filename": name, "reason": str(e), "for": "company_document"})
        return {"filename": name, "accepted": False, "error": str(e)}
    g = inspect_text(session, doc.text, source="company_document", tenant_id=tenant_id, hidden_text=doc.hidden_text,
                     sanitizer_notes=doc.notes, already_clean=True)
    if doc.hidden_text.strip() or g.risk_level in ("medium", "high"):
        return {"filename": name, "accepted": False,
                "error": "it contains hidden text or instructions aimed at the AI, and the assistant would read it as "
                         "trusted: clean the document and upload it again"}
    taken = {d["doc_id"] for d in list_docs(tenant_id)}
    stem = base = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:50] or "document"
    n = 2
    while stem in taken:
        stem, n = f"{base}-{n}", n + 1
    title = re.sub(r"[-_]+", " ", Path(name).stem).strip().capitalize() or "Document"
    sections = _sections(doc.text)
    folder = docs_dir(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.md").write_text(f"# {title}\n\n" + "\n\n".join(f"## {t}\n{b}" for t, b in sections),
                                       encoding="utf-8")
    from backend.orchestration import seed  # seed imports this module

    seed.index_policies(session, tenant_id)
    audit.log(session, actor=actor, event="knowledge.document_added", tenant_id=tenant_id, target=f"doc:{stem}",
              detail={"file": name, "sections": len(sections)})
    return {"filename": name, "accepted": True, "doc_id": stem, "title": title, "sections": len(sections)}


def delete(session: Session, tenant_id: int, doc_id: str, actor: str) -> None:
    path = docs_dir(tenant_id) / f"{doc_id}.md"
    if not re.fullmatch(r"[a-z0-9-]{1,60}", doc_id) or not path.exists():
        raise HTTPException(404, "Uploaded document not found (built-in policies cannot be removed here)")
    path.unlink()
    from backend.orchestration import seed

    seed.index_policies(session, tenant_id)
    audit.log(session, actor=actor, event="knowledge.document_removed", tenant_id=tenant_id, target=f"doc:{doc_id}")
