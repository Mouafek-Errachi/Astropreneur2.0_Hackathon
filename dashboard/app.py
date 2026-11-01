"""L1 presentation (Streamlit). A thin client: every rule lives in the API.

This file is the router: the log-in page until there is a token, then the pages the role may use, grouped in the menu.
"""
import html

import streamlit as st

from api import call
from nav import visible_pages
from roles import public_roles, role_table
from ui import chip, inject_css

st.set_page_config(page_title="Secure AI HR", page_icon=":material/shield_person:", layout="wide")
inject_css()

if "token" not in st.session_state:
    st.navigation([st.Page("login.py", title="Log in", icon=":material/login:")], position="hidden").run()
    st.stop()

if not st.session_state.get("me"):
    st.session_state.me = call("GET", "/auth/me")
if not (me := st.session_state.me):
    st.stop()  # call() has already said what went wrong
st.session_state.role = me["role"]
if "health" not in st.session_state:
    st.session_state.health = call("GET", "/health") or {}

menu: dict[str, list] = {"": [st.Page("home.py", title="Home", icon=":material/home:", default=True)]}
for e in visible_pages(me):
    menu.setdefault(e.section, []).append(st.Page(e.path, title=e.title, icon=f":material/{e.icon}:", url_path=e.url))
page = st.navigation(menu)

with st.sidebar:
    st.html(f"<b>{html.escape(me['email'])}</b>")  # st.markdown would turn the address into a link
    st.markdown(chip(me["role"].capitalize(), "blue"), help=me.get("role_summary") or None)
    with st.popover("What my role can do", icon=":material/badge:", width="stretch"):
        st.caption(me.get("role_summary", ""))
        role_table(public_roles()["access"], highlight=me["role"])
    health = st.session_state.health
    if health.get("llm_provider") == "mock":
        st.markdown(chip("AI off", "gray", "smart_toy"), help="Mock mode: rule-based outputs. Set LLM_PROVIDER=gemini "
                    "or grok in .env to use the AI.")
    elif health.get("llm_ready"):
        st.markdown(chip(f"AI on · {health['llm_provider']}", "green", "smart_toy"),
                    help=f"Fallback: {health['llm_fallback']}" if health.get("llm_fallback") else None)
    else:
        st.markdown(chip("AI key missing", "orange", "smart_toy"),
                    help=f"LLM_PROVIDER={health.get('llm_provider')} but its API key is empty in .env, so the simple "
                         "rules are used.")
    if st.button("Log out", icon=":material/logout:", width="stretch"):
        st.session_state.clear()
        st.rerun()

page.run()
