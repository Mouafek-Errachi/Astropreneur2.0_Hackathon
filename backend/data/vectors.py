"""Embeddings + similarity search.

Embeddings are stored as JSON float lists and searched in Python (fine for hundreds of rows).
TODO: on Postgres, switch the columns to pgvector `Vector` and push the search into SQL.
"""
import hashlib
import json
import math
import re

from backend.config import get_settings

DIM = 256
_WORD = re.compile(r"[a-z0-9+#]{2,}")


def _local_embed(text: str) -> list[float]:
    """Deterministic hashed bag-of-words embedding (no API needed)."""
    vec = [0.0] * DIM
    for w in _WORD.findall(text.lower()):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed(text: str) -> list[float]:
    s = get_settings()
    if s.embeddings_provider == "gemini" and s.gemini_api_key:
        try:
            from backend.agents.llm import gemini_embed

            return gemini_embed(text[:8000])
        except Exception:
            pass  # fall back to local so ingestion never breaks
    return _local_embed(text)


def dumps(vec: list[float]) -> str:
    return json.dumps([round(v, 5) for v in vec])


def cosine(a: list[float] | str, b: list[float] | str) -> float:
    a = json.loads(a) if isinstance(a, str) else a
    b = json.loads(b) if isinstance(b, str) else b
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
