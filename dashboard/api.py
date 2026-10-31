"""Thin HTTP client: the dashboard holds no business logic, it only calls the FastAPI backend."""
import os

import requests
import streamlit as st

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and adds ~2s to every request.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


@st.cache_resource
def _http() -> requests.Session:
    return requests.Session()  # keep-alive: reuse one connection across reruns


def call(method: str, path: str, **kwargs):
    headers = kwargs.pop("headers", {})
    if token := st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = _http().request(method, f"{API_URL}{path}", headers=headers, timeout=120, **kwargs)
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


def require_login():
    if "token" not in st.session_state:
        st.info("Please log in from the home page.")
        st.stop()
