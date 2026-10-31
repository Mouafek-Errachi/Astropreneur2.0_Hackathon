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
                st.rerun()
            else:
                st.error("Login failed")
else:
    me = call("GET", "/auth/me")
    st.success(f"Logged in as {me['email']} ({me['role']})")
    if st.button("Log out"):
        st.session_state.pop("token")
        st.rerun()
    st.write("Use the pages in the sidebar.")
