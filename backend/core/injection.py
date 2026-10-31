"""Heuristic prompt-injection detector. It never blocks on its own: it flags, and flagged items
require explicit human acknowledgement before any action derived from them can be approved."""
import base64
import re
from dataclasses import asdict, dataclass

SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class Flag:
    type: str
    severity: str
    snippet: str

    def to_dict(self) -> dict:
        return asdict(self)


_P = lambda s: re.compile(s, re.I | re.S)  # noqa: E731

PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("instruction_override", "high", _P(r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the|these)\b.{0,30}\b(instructions?|prompts?|rules|guidelines|directions|context)")),
    ("new_instructions", "high", _P(r"\b(new|updated|real|actual|hidden)\s+(system\s+)?(instructions?|prompt|directive)s?\b\s*[:\-]")),
    ("role_hijack", "high", _P(r"\b(you are now|act as|pretend (to be|you are)|from now on,? you|you must now)\b")),
    ("system_prompt_probe", "medium", _P(r"\b(system prompt|developer message|reveal (your|the) (prompt|instructions)|print your instructions)\b")),
    ("ai_addressed", "medium", _P(r"\b(dear|attention|note (to|for)|hey|hello)?\s*(ai|a\.i\.|llm|chatgpt|gpt|claude|language model|assistant|screening (bot|system|agent)|recruiting (bot|ai|agent)|hr (bot|ai|agent))\b\s*[:,]")),
    ("score_manipulation", "high", _P(r"\b(rank|rate|score|mark|place|put|list)\b.{0,40}\b(me|this (candidate|resume|applicant)|him|her|them)\b.{0,40}\b(first|top|highest|best|#?1|number one|100|10/10|perfect|excellent|strong(est)? (hire|match))")),
    ("score_manipulation", "high", _P(r"\b(give|assign|award|output)\b.{0,30}\b(score|rating|ranking)\b.{0,20}\b(of\s*)?(100|10|maximum|max|highest|perfect)\b")),
    ("decision_manipulation", "high", _P(r"\b(this (candidate|applicant) (is|must be)|you (must|should) (hire|shortlist|select|approve))\b.{0,40}\b(hire|shortlist|select|approve|qualified|top)")),
    ("action_request", "high", _P(r"\b(send|forward|email|e-mail|transfer|export|upload|post)\b.{0,60}\b(all|every|the)\b.{0,40}\b(resumes?|cvs?|candidates?|applicants?|data|records|files|database|emails?|passwords?|credentials)\b")),
    ("action_request", "medium", _P(r"\b(approve|execute|auto-?approve|skip (the )?(approval|review|human))\b.{0,40}\b(this|request|action|transfer|payment|without)")),
    ("exfiltration_url", "medium", _P(r"!\[[^\]]*\]\(https?://|(https?://[^\s]+\?(.*)(data|q|exfil|leak|token|secret)=)")),
    ("chat_template_tokens", "high", _P(r"(<\|im_start\|>|<\|im_end\|>|<\|system\|>|\[/?INST\]|<<SYS>>|<\|endoftext\|>|###\s*(system|instruction)\s*:|</?(system|assistant|untrusted_document)[^>]*>)")),
    ("credential_phishing", "medium", _P(r"\b(verify|confirm|update|reset)\b.{0,30}\b(your )?(password|credentials|login|account|bank details|payroll details)\b")),
    ("urgency_pressure", "low", _P(r"\b(urgent(ly)?|immediately|within (1|one|24) hours?|account (will be )?(suspended|locked|closed)|final notice)\b")),
]

B64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{60,}={0,2}")

NOTE_FLAGS = {
    "zero_width_chars": ("obfuscation_zero_width", "medium"),
    "bidi_override_chars": ("obfuscation_bidi", "medium"),
    "unicode_tag_chars": ("obfuscation_unicode_tags", "high"),
    "html_hidden_text": ("hidden_html_text", "medium"),
}


def _snippet(text: str, m: re.Match) -> str:
    s, e = max(0, m.start() - 20), min(len(text), m.end() + 20)
    return re.sub(r"\s+", " ", text[s:e]).strip()[:200]


def scan(text: str, *, hidden_text: str = "", sanitizer_notes: list[str] | None = None) -> list[Flag]:
    flags: list[Flag] = []
    seen: set[tuple[str, str]] = set()

    def add(f: Flag) -> None:
        key = (f.type, f.snippet[:60])
        if key not in seen:
            seen.add(key)
            flags.append(f)

    for body, boost in ((text, False), (hidden_text, True)):
        if not body:
            continue
        for ftype, sev, pat in PATTERNS:
            for m in pat.finditer(body):
                add(Flag(f"{ftype}{'_in_hidden_text' if boost else ''}", "high" if boost else sev, _snippet(body, m)))
                break
        for m in B64_BLOB.finditer(body):
            try:
                decoded = base64.b64decode(m.group(0) + "==", validate=False).decode("utf-8", "ignore")
            except Exception:
                continue
            if any(p.search(decoded) for _, sev, p in PATTERNS if sev == "high"):
                add(Flag("encoded_instructions", "high", m.group(0)[:80]))

    if hidden_text.strip():
        add(Flag("hidden_text", "high" if len(hidden_text.strip()) > 20 else "medium", hidden_text.strip()[:200]))
    for note in sanitizer_notes or []:
        if note in NOTE_FLAGS:
            t, sev = NOTE_FLAGS[note]
            add(Flag(t, sev, note))
    return flags


def risk_level(flags: list[Flag] | list[dict]) -> str:
    level = 0
    for f in flags:
        sev = f["severity"] if isinstance(f, dict) else f.severity
        level = max(level, SEVERITY_RANK.get(sev, 0))
    return {v: k for k, v in SEVERITY_RANK.items()}[level]


def scan_output(text: str, allowed_domains: list[str]) -> list[Flag]:
    """Checks LLM *output* before it is queued for approval (defence against successful injections)."""
    flags = scan(text)
    for m in re.finditer(r"https?://([^/\s\"'>)]+)", text):
        host = m.group(1).lower().split(":")[0]
        if not any(host == d or host.endswith("." + d) for d in allowed_domains):
            flags.append(Flag("link_to_unapproved_domain", "high", m.group(0)[:120]))
    return flags
