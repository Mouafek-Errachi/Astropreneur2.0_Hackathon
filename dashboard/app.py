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
                st.rerun()
            else:
                st.error("Login failed" if r.status_code == 401 else f"Login failed ({r.status_code})")
    st.stop()

me = call("GET", "/auth/me")
st.session_state.me = me
health = call("GET", "/health")
c1, c2, c3 = st.columns(3)
c1.metric("Logged in as", me["role"], me["email"], delta_color="off")
c2.metric("LLM provider", health["llm_provider"], f"fallback: {health['llm_fallback'] or '—'}", delta_color="off")
pending = call("GET", "/approvals", params={"status": "pending"}) if "approvals:read" in me["scopes"] else []
c3.metric("Waiting for approval", len(pending or []))

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

if st.button("Log out"):
    st.session_state.clear()
    st.rerun()
