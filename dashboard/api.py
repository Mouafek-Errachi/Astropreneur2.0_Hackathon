"""Thin HTTP clients: the dashboard holds no business logic, it only calls the FastAPI (and optionally n8n) backends."""
import os

import requests
import streamlit as st

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and adds ~2s to every request.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
N8N_URL = os.getenv("N8N_BASE_URL", "").rstrip("/")
# n8n API keys live in the dashboard server's environment; they are never sent to the browser.
N8N_KEYS = {"hr": "N8N_HR_KEY", "hr_admin": "N8N_HR_ADMIN_KEY", "portal": "N8N_PORTAL_KEY", "extension": "N8N_EXTENSION_KEY"}


@st.cache_resource
def _http() -> requests.Session:
    return requests.Session()  # keep-alive: reuse one connection across reruns


def call(method: str, path: str, timeout: int = 300, **kwargs):
    headers = kwargs.pop("headers", {})
    if token := st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = _http().request(method, f"{API_URL}{path}", headers=headers, timeout=timeout, **kwargs)
    except requests.RequestException as e:
        st.error(f"Backend unreachable: {e}")
        st.stop()
    if r.status_code == 401:
        st.session_state.pop("token", None)
        st.warning("Session expired, please log in again.")
        st.stop()
    if r.status_code == 403 and method == "GET":
        st.info("Your role does not have access to this page.")
        st.stop()
    if not r.ok:
        st.error(f"{r.status_code}: {r.json().get('detail', r.text) if r.headers.get('content-type','').startswith('application/json') else r.text}")
        return None
    return r.json()


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
        st.info("Please log in from the home page.")
        st.stop()


def is_admin() -> bool:
    return st.session_state.get("role") == "admin"
