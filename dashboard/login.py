"""Log-in page: the router shows it until the API has issued a token."""
import requests
import streamlit as st

from api import API_URL, call

DEMO_USERS = {
    "recruiter@acme.test": ("Recruiter", "Screens, drafts and approves."),
    "admin@acme.test": ("Admin", "Everything, plus the audit log, API keys and demo data."),
    "manager@acme.test": ("Manager", "Like a recruiter, and sees and validates employee skills."),
}

_, middle, _ = st.columns([1, 1.7, 1])
with middle:
    st.title(":material/shield_person: Secure AI HR")
    st.caption("AI agents do the daily HR work. People make every decision.")
    if notice := st.session_state.pop("login_notice", None):
        st.warning(notice, icon=":material/schedule:")

    with st.container(border=True):
        st.subheader("Sign in")
        email = st.selectbox("Account", list(DEMO_USERS), format_func=lambda e: f"{DEMO_USERS[e][0]} · {e}")
        st.caption(DEMO_USERS[email][1])
        with st.form("login", border=False):
            password = st.text_input("Password", type="password", placeholder="Demo passwords are in .env.example")
            if st.form_submit_button("Log in", type="primary", width="stretch"):
                try:
                    r = requests.post(f"{API_URL}/auth/login", data={"username": email, "password": password}, timeout=10)
                except requests.RequestException:
                    st.error(f"Cannot reach the server at {API_URL}. Check that the API is running.",
                             icon=":material/cloud_off:")
                else:
                    if r.ok:
                        st.session_state.token, st.session_state.role = r.json()["access_token"], r.json()["role"]
                        st.session_state.me = call("GET", "/auth/me")
                        st.rerun()
                    elif r.status_code == 401:
                        st.error("Wrong password for this account.", icon=":material/lock:")
                    elif r.status_code == 429:
                        st.error("Too many attempts. Wait a minute and try again.", icon=":material/timer:")
                    else:
                        st.error(f"Login failed ({r.status_code}).")

    how = st.columns(3)
    how[0].markdown(":material/smart_toy: **Agents propose**  \n:gray[Screen CVs, triage email, draft replies.]")
    how[1].markdown(":material/verified_user: **People decide**  \n:gray[Nothing is sent without a signed approval.]")
    how[2].markdown(":material/shield: **Always guarded**  \n:gray[Hidden text and injections are blocked and logged.]")
