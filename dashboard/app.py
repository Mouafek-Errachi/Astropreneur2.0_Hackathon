import requests
import streamlit as st

from api import API_URL, call

st.set_page_config(page_title="Secure AI HR", layout="wide")
st.title("Secure AI HR System")
st.caption("The agent executes, the human decides. Nothing is sent or recorded without approval.")

if "token" not in st.session_state:
    with st.form("login"):
        email = st.text_input("Email", "recruiter@acme.test")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in"):
            r = requests.post(f"{API_URL}/auth/login", data={"username": email, "password": password}, timeout=10)
            if r.ok:
                st.session_state.token = r.json()["access_token"]
                st.session_state.role = r.json()["role"]
                st.rerun()
            else:
                st.error("Login failed")
    st.stop()

me = call("GET", "/auth/me")
st.session_state.role = me["role"]
col1, col2 = st.columns([5, 1])
col1.success(f"Logged in as {me['email']} ({me['role']})")
if col2.button("Log out"):
    st.session_state.clear()
    st.rerun()

st.subheader("Today")
digest = call("GET", "/emails/digest", params={"hours": 24}) or {}
pending = call("GET", "/approvals", params={"status": "pending"}) or []
followups = [f for f in call("GET", "/followups") or [] if f["status"] == "open"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Waiting for your approval", len(pending))
c2.metric("Emails (24h)", digest.get("total", 0))
c3.metric("Need a human answer", len(digest.get("needs_reply", [])))
c4.metric("Open follow-ups", len(followups))
if digest.get("headline"):
    st.info(digest["headline"])
st.write("Use the pages in the sidebar: screening, inbox, approvals, follow-ups, scheduling, Ask HR, and HR Ops (n8n).")

if me["role"] == "admin":
    with st.expander("Load demo data (fictional people only)"):
        st.caption("Runs the normal pipelines: sanitising, injection scan and duplicate checks. Safe to run twice.")
        n_res = st.number_input("Generated resumes", 0, 500, 60, 10)
        n_mail = st.number_input("Generated emails", 0, 500, 40, 10)
        if st.button("Load demo data"):
            with st.spinner("Loading..."):
                r = call("POST", "/admin/demo-data", json={"resumes": int(n_res), "emails": int(n_mail)})
            if r:
                st.success(f"Resumes accepted: {r['resumes']['accepted']} (flagged: {r['resumes']['flagged']}), "
                           f"emails added: {r['emails']['added']}, policy sections indexed: {r['policy_chunks']}")
                st.caption("Next: run screening on the Backend Engineer job, then classify the inbox.")
