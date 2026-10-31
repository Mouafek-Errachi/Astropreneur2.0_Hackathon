import pandas as pd
import streamlit as st

from api import call, require_login

require_login()
st.header("Interview scheduling")
st.caption("Slots come from the interviewer's calendar and interviews already booked. You pick the slot when approving.")

jobs = call("GET", "/jobs") or []
if not jobs:
    st.info("Create a job and screen candidates first.")
    st.stop()
job = st.selectbox("Job", jobs, format_func=lambda j: j["title"])
candidates = [c for c in call("GET", f"/jobs/{job['id']}/ranking") or [] if c["status"] != "rejected"]
if not candidates:
    st.info("No candidates for this job yet.")
    st.stop()
cand = st.selectbox("Candidate", candidates,
                    format_func=lambda c: f"#{c['rank'] or '-'} {c['name'] or c['original_filename']} ({c['status']})")
who = st.selectbox("Interviewer", call("GET", "/scheduling/interviewers") or [])
duration = st.slider("Duration (minutes)", 15, 120, 45, 15)
if st.button("Propose slots") and who:
    r = call("POST", "/scheduling/propose", json={"candidate_id": cand["id"], "interviewer": who, "duration_minutes": duration})
    if r:
        st.success(f"Proposal queued for approval (action #{r['action_id']}). The invitation is sent once approved.")
        st.table(r["slots"])

st.subheader("Booked interviews")
events = call("GET", "/scheduling/events") or []
if events:
    st.dataframe(pd.DataFrame(events)[["start", "end", "interviewer", "title"]], hide_index=True, width="stretch")
else:
    st.caption("None yet.")
