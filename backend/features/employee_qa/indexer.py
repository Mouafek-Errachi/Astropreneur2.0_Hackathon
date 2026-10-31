"""Splits data/policies/*.md into chunks and does simple keyword retrieval. TODO: TF-IDF / embeddings."""
import re

from sqlmodel import Session, delete, select

from backend.config import get_settings
from backend.models import PolicyChunk

WORD = re.compile(r"[a-z]{3,}")


def reindex(session: Session, tenant_id: int) -> int:
    session.exec(delete(PolicyChunk).where(PolicyChunk.tenant_id == tenant_id))
    n = 0
    for path in sorted((get_settings().data_dir / "policies").glob("*.md")):
        for i, section in enumerate(re.split(r"\n(?=## )", path.read_text(encoding="utf-8"))):
            if section.strip():
                heading = section.strip().splitlines()[0].lstrip("# ").strip()
                session.add(PolicyChunk(tenant_id=tenant_id, source=path.name, heading=heading,
                                        chunk_index=i, text=section.strip()))
                n += 1
    session.commit()
    return n


def search(session: Session, tenant_id: int, question: str, k: int = 3) -> list[tuple[float, PolicyChunk]]:
    q = set(WORD.findall(question.lower()))
    scored = []
    for chunk in session.exec(select(PolicyChunk).where(PolicyChunk.tenant_id == tenant_id)):
        words = set(WORD.findall(chunk.text.lower()))
        overlap = len(q & words) / (len(q) or 1)
        if overlap > 0:
            scored.append((overlap, chunk))
    return sorted(scored, key=lambda x: -x[0])[:k]
