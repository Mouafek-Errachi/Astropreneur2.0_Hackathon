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


# ------------------------------------------------------------------ job descriptions (project staffing)
MONEY = re.compile(r"(\$|€|£|\b(usd|eur|tnd|dinars?|salary|salaries|per (year|month|annum)|k/year|bonus|bonuses|equity|"
                   r"stock options|compensation package)\b)", re.I)
EXCLUSIONARY = [
    (re.compile(r"\b(young|youthful|digital natives?|recent graduates? only|under \d{2}|years old|age \d{2})\b", re.I),
     "age_wording", "high"),
    (re.compile(r"\bnative (english |french |arabic )?speakers?\b", re.I), "origin_wording", "high"),
    (re.compile(r"\b(he|him|his|she|her|hers|guys|salesman|chairman|manpower)\b", re.I), "gendered_wording", "medium"),
    (re.compile(r"\b(rock ?stars?|ninjas?)\b", re.I), "exclusionary_jargon", "low"),
    (re.compile(r"\b(married|single|children|pregnan\w*|religio\w*|nationality|ethnic\w*)\b", re.I),
     "protected_characteristic", "high"),
]


def check_job_description(jd, required: set[str], allowed_nice: set[str], employee_names: list[str]) -> list[Flag]:
    """A drafted job description may only ask for the gap skill, may not invent pay, benefits or links, may not
    exclude people for who they are, and may not mention current employees."""
    text = f"{jd.title}\n{jd.summary}\n" + "\n".join(jd.responsibilities) + f"\n{jd.body}"
    flags = [Flag("output_" + f.type, f.severity, f.snippet) for f in injection.scan(text)]
    if {s.lower() for s in jd.required_skills} != {s.lower() for s in required}:
        flags.append(Flag("skills_not_the_gap", "high", ", ".join(jd.required_skills)[:120]))
    if extra := [s for s in jd.nice_to_have if s.lower() not in {a.lower() for a in allowed_nice}]:
        flags.append(Flag("nice_to_have_not_in_project", "medium", ", ".join(extra)[:120]))
    if m := MONEY.search(text):
        flags.append(Flag("invented_compensation", "high", m.group(0)))
    for url in URL.findall(text):
        flags.append(Flag("link_in_job_description", "high", url[:120]))
    for rx, kind, sev in EXCLUSIONARY:
        if m := rx.search(text):
            flags.append(Flag(kind, sev, m.group(0)))
    for name in employee_names:
        if name and re.search(rf"\b{re.escape(name)}\b", text, re.I):
            flags.append(Flag("mentions_employee", "high", name))
    return flags
