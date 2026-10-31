import streamlit as st

from api import call, require_login

require_login()
st.header("Interview scheduling")

interviewers = call("GET", "/scheduling/interviewers") or []
cid = st.number_input("Candidate ID", min_value=1, step=1)
who = st.selectbox("Interviewer", interviewers)
duration = st.slider("Duration (minutes)", 15, 120, 45, 15)
if st.button("Propose slots") and who:
    r = call("POST", "/scheduling/propose", json={"candidate_id": int(cid), "interviewer": who, "duration_minutes": duration})
    if r:
        st.success(f"Proposal queued for approval (action #{r['action_id']})")
        st.table(r["slots"])
