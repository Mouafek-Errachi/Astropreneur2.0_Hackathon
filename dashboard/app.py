"""L1 presentation (Streamlit). A thin client: every rule lives in the API."""
import requests
import streamlit as st

from api import API_URL, call

st.set_page_config(page_title="Secure AI HR", layout="wide")
st.title("Secure AI HR System")
st.caption("The agent executes, the human decides. Nothing is sent or recorded without a signed human approval.")

DEMO_USERS = {
    "recruiter@acme.test": "Recruiter — screens, drafts, approves",
    "admin@acme.test": "Admin — everything + audit, API keys, demo data",
    "viewer@acme.test": "Viewer — read only",
    "yasmine.b@hr-demo.test": "Employee — asks HR, opens tickets",
}

if "token" not in st.session_state:
    with st.form("login"):
        email = st.selectbox("Account", list(DEMO_USERS), format_func=lambda e: f"{e}  ·  {DEMO_USERS[e]}")
        password = st.text_input("Password", type="password", help="Demo passwords are in .env.example")
        if st.form_submit_button("Log in"):
            r = requests.post(f"{API_URL}/auth/login", data={"username": email, "password": password}, timeout=10)
            if r.ok:
                st.session_state.token = r.json()["access_token"]
                st.session_state.role = r.json()["role"]
                st.rerun()
            else:
                st.error("Login failed" if r.status_code == 401 else f"Login failed ({r.status_code})")
    st.stop()

me = call("GET", "/auth/me")
st.session_state.me, st.session_state.role = me, me["role"]
col1, col2 = st.columns([5, 1])
col1.success(f"Logged in as {me['email']} ({me['role']})")
if col2.button("Log out"):
    st.session_state.clear()
    st.rerun()
health = call("GET", "/health") or {}
if health.get("llm_provider") == "mock":
    st.caption("AI: off (mock mode, rule-based outputs). Set LLM_PROVIDER=gemini or grok in .env to use the AI.")
elif health.get("llm_ready"):
    st.caption(f"AI: on ({health['llm_provider']}" + (f", fallback {health['llm_fallback']})" if health.get("llm_fallback") else ")"))
else:
    st.warning(f"AI: LLM_PROVIDER={health.get('llm_provider')} but its API key is empty in .env, so the simple rules are used.")

if "approvals:read" in me["scopes"]:
    st.subheader("Today")
    digest = call("GET", "/emails/digest", params={"hours": 24}) or {}
    pending = call("GET", "/approvals", params={"status": "pending"}) or []
    followups = [f for f in call("GET", "/followups") or [] if f["status"] == "open"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Waiting for your approval", len(pending))
    c2.metric("Emails (24h)", digest.get("total", 0))
    c3.metric("Need a human answer", digest.get("needs_reply", 0))
    c4.metric("Open follow-ups", len(followups))
    if digest.get("headline"):
        st.info(digest["headline"])

if "admin" in me["scopes"]:
    st.subheader("Demo data")
    bulk = st.checkbox("Include 100 bulk resumes across the 5 jobs")
    if st.button("Load demo data (5 jobs, resumes incl. 10 attacks, 43 emails)"):
        r = call("POST", "/admin/demo/load", params={"bulk": str(bulk).lower()})
        if r:
            ok = sum(x["accepted"] for x in r["resumes"])
            st.success(f"{r['jobs']} job, {ok}/{len(r['resumes'])} resumes accepted, {r['emails']} emails ingested")
            st.dataframe([{k: x.get(k) for k in ("filename", "accepted", "risk_level", "hidden_text_found", "error")}
                          for x in r["resumes"]], width="stretch")

st.write("Use the pages in the sidebar: inbox, screening, approvals, scheduling, follow-ups, Ask HR, HR Ops and AI Firewall.")
