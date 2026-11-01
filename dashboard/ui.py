"""Shared look and small building blocks for the dashboard pages. No business logic: that lives in the API."""
import re
from datetime import datetime

import streamlit as st

CSS = """
<style>
[data-testid="stHeaderActionElements"] { display: none; }  /* the chain icon next to every heading */
[data-testid="stMainBlockContainer"] { padding-top: 2.75rem; }  /* the default leaves a large empty band on top */
.hr-me { display: flex; gap: .7rem; align-items: center; }
.hr-me .avatar { flex: none; width: 2.3rem; height: 2.3rem; border-radius: 50%; display: flex; align-items: center;
                 justify-content: center; font-weight: 700; color: #fff; background: #4f46e5; }
.hr-me .who { min-width: 0; line-height: 1.25; }
.hr-me .who b { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hr-me .who span { font-size: .82rem; opacity: .7; }
</style>
"""

URGENCY = {"high": ("High", "red"), "medium": ("Medium", "orange"), "low": ("Low", "gray")}
RISK = {"none": ("No risk", "green"), "low": ("Low risk", "yellow"), "medium": ("Medium risk", "orange"),
        "high": ("High risk", "red")}


def esc(text) -> str:
    """Escape Markdown: text from a resume, email or document must not render links, images, colours or formulas."""
    escaped = re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~:$])", r"\\\1", str(text if text is not None else ""))
    return escaped.replace("@", "@​")  # an invisible break, or every address becomes a mailto: link


def human(name: str) -> str:
    """send_email -> Send email."""
    return str(name or "").replace("_", " ").strip().capitalize()


def chip(label: str, color: str = "gray", icon: str | None = None) -> str:
    """Inline badge for Markdown text. `label` is escaped here; `icon` is a Material name such as 'warning'."""
    inner = f":material/{icon}: {esc(label)}" if icon else esc(label)
    return f":{color}-badge[{inner}]"


def when(ts: str | None, fmt: str = "%d %b, %H:%M") -> str:
    """ISO timestamp for people. One with a time zone is shown in local time; one without (e.g. a calendar slot) as is."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)
    return (dt.astimezone() if dt.tzinfo else dt).strftime(fmt)


def readable_hours(text: str) -> str:
    """'in the last 168h' -> 'in the last 7 days' (the API counts in hours)."""
    def fix(m: re.Match) -> str:
        hours = int(m[1])
        return f"last {hours // 24} days" if hours >= 48 and hours % 24 == 0 else f"last {hours} hours"
    return re.sub(r"last (\d+)h", fix, text)


def inject_css() -> None:
    st.html(CSS)


def flash(message: str, icon: str = ":material/check_circle:") -> None:
    """A toast that survives the st.rerun() right after it (a toast sent just before a rerun is lost)."""
    st.session_state.setdefault("flashes", []).append((message, icon))


def page_header(title: str, subtitle: str = "") -> None:
    """Every page starts here: the title, one sentence on what the page is for, and any pending toasts."""
    for message, icon in st.session_state.pop("flashes", []):
        st.toast(message, icon=icon)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def empty(title: str, hint: str = "") -> None:
    """Friendly empty state: say what is missing and what to do about it."""
    st.info(f"**{title}**" + (f"  \n{hint}" if hint else ""), icon=":material/inbox:")
