import streamlit as st

from api import call, require_login

require_login()
st.header("Interview scheduling")
st.caption("The agent proposes free slots; after approval the candidate gets a single-use link to pick one.")

jobs = call("GET", "/jobs") or []
if jobs:
    job = st.selectbox("Job", jobs, format_func=lambda j: j["title"])
    apps = [r for r in call("GET", f"/jobs/{job['id']}/ranking") or [] if r["email"]]
    if apps:
        app = st.selectbox("Candidate", apps, format_func=lambda r: f"#{r['rank']} {r['name']} ({r['email']})")
        who = st.selectbox("Interviewer", call("GET", "/scheduling/interviewers") or [])
        duration = st.slider("Duration (minutes)", 15, 120, 45, 15)
        if st.button("Propose slots") and who:
            r = call("POST", "/scheduling/propose", json={"application_id": app["application_id"], "interviewer": who,
                                                          "duration_minutes": duration})
            if r:
                st.success("Invitation queued for approval (see Approvals)")
    else:
        st.info("Screen candidates first.")

st.subheader("Booked interviews")
st.dataframe(call("GET", "/scheduling/events") or [], width="stretch")
