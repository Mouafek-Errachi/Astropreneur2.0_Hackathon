"""Thin HTTP clients: the dashboard holds no business logic, it only calls the FastAPI (and optionally n8n) backends."""
import os
import re

import requests
import streamlit as st

from ui import esc

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and adds ~2s to every request.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
N8N_URL = os.getenv("N8N_BASE_URL", "").rstrip("/")
# n8n API keys live in the dashboard server's environment; they are never sent to the browser.
N8N_KEYS = {"hr": "N8N_HR_KEY", "hr_admin": "N8N_HR_ADMIN_KEY", "portal": "N8N_PORTAL_KEY", "extension": "N8N_EXTENSION_KEY"}


@st.cache_resource
def _http() -> requests.Session:
    return requests.Session()  # keep-alive: reuse one connection across reruns


HINTS = {"FOUR_EYES": "Ask a colleague to approve it."}


def _problem(r: requests.Response) -> str:
    """The API's own explanation of a refused request, as a sentence ('FOUR_EYES: the person…' -> 'The person…')."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:300]
    detail = body.get("detail", "") if isinstance(body, dict) else ""
    if isinstance(detail, list):  # request validation: one entry per bad field
        detail = "; ".join(f"{'.'.join(map(str, e.get('loc', [])[1:]))}: {e.get('msg')}".lstrip(": ") for e in detail)
    code, sentence = re.match(r"(?:([A-Z][A-Z_]+):\s*)?(.*)", str(detail), re.S).groups()
    text = sentence[:1].upper() + sentence[1:]
    if hint := HINTS.get(code):
        text = f"{text.rstrip('. ')}. {hint}"
    return text


def call(method: str, path: str, timeout: int = 300, **kwargs):
    headers = kwargs.pop("headers", {})
    if token := st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = _http().request(method, f"{API_URL}{path}", headers=headers, timeout=timeout, **kwargs)
    except requests.RequestException:
        st.error(f"Cannot reach the server at {API_URL}. Check that the API is running, then reload this page.",
                 icon=":material/cloud_off:")
        st.stop()
    if r.status_code == 401:
        st.session_state.clear()
        st.session_state.login_notice = "Your session expired. Please log in again."
        st.rerun()
    if r.status_code == 403 and method == "GET":
        st.info("Your role does not have access to this page.", icon=":material/lock:")
        st.stop()
    if not r.ok:
        problem = esc(_problem(r)) or "The request was refused."
        st.error(problem if r.status_code < 500 else f"The server had a problem ({r.status_code}). {problem}",
                 icon=":material/error:")
        return None
    return r.json()


def can(scope: str) -> bool:
    """Whether the logged-in role holds this scope (the API enforces it; this only hides what would be refused)."""
    return scope in (st.session_state.get("me") or {}).get("scopes", [])


def n8n_configured() -> bool:
    return bool(N8N_URL)


def n8n_call(method: str, path: str, key: str, **kwargs):
    """Calls an n8n webhook and unwraps its {ok, data, error, security} envelope."""
    headers = {**kwargs.pop("headers", {}), "X-API-Key": os.getenv(N8N_KEYS[key], "")}
    try:
        r = requests.request(method, f"{N8N_URL}{path}", headers=headers, timeout=90, **kwargs)
    except requests.RequestException as e:
        st.error(f"n8n unreachable: {e}")
        return None
    try:
        body = r.json()
    except ValueError:
        st.error(f"{r.status_code}: {r.text[:300]}")
        return None
    if not body.get("ok"):
        err = body.get("error") or {}
        st.error(f"{r.status_code} {err.get('code', '')}: {err.get('message', r.text[:300])}")
        return None
    return body


def require_login():
    if "token" not in st.session_state:
        st.info("Please log in first.", icon=":material/lock:")
        st.stop()


def is_admin() -> bool:
    return st.session_state.get("role") == "admin"
