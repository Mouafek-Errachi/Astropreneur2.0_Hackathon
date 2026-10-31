"""L5 Output Validator: checks what the agents PRODUCE before it is stored as a proposed action.
Schema validation happens in agents/llm.py (Pydantic); these are the policy checks."""
import re

from backend.config import get_settings
from backend.security.guard import injection
from backend.security.guard.injection import Flag

URL = re.compile(r"https?://[^\s\"'<>)]+")
NUMBER = re.compile(r"\b\d[\d:./-]*\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _allowed(url: str) -> bool:
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    domains = [d.strip() for d in get_settings().allowed_link_domains.split(",") if d.strip()]
    return any(host == d or host.endswith("." + d) for d in domains)


def check_outgoing_text(text: str, *, source_text: str = "", fact_check: bool = False) -> list[Flag]:
    """Flags injected instructions, links to unapproved domains, and (optionally) facts that are
    not present in the source text (numbers, links, email addresses), as in the n8n broadcast check."""
    flags = [Flag("output_" + f.type, f.severity, f.snippet) for f in injection.scan(text)]
    for url in URL.findall(text):
        if not _allowed(url) and url not in source_text:
            flags.append(Flag("link_to_unapproved_domain", "high", url[:120]))
    for addr in EMAIL.findall(text):
        if addr not in source_text and not _allowed("http://" + addr.split("@")[1]):
            flags.append(Flag("unexpected_email_address", "medium", addr))
    if fact_check:
        for n in set(NUMBER.findall(text)):
            if n not in source_text:
                flags.append(Flag("unverified_figure", "medium", n))
    return flags


def check_citations(cited: list[str], valid: set[str]) -> list[str]:
    """Citations must point at sources that were actually retrieved."""
    return [c for c in cited if c in valid]


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
