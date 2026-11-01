import streamlit as st

from api import call, can, require_login
from ui import empty, page_header, when

require_login()
page_header("Scheduling", "The agent finds free slots in the interviewer's calendar. After approval the candidate gets "
                          "the slot you booked, or a single-use link to pick one.")

left, right = st.columns([2, 3], gap="large")
with left, st.container(border=True):
    st.subheader("Invite a candidate")
    jobs = call("GET", "/jobs") or []
    if not jobs:
        empty("No job yet.", "Create a job on the Screening page first.")
    else:
        job = st.selectbox("Job", jobs, format_func=lambda j: j["title"])
        apps = [r for r in call("GET", f"/jobs/{job['id']}/ranking") or [] if r["email"]]
        if not apps:
            empty("No candidate with an email address for this job.", "Screen resumes on the Screening page first.")
        else:
            app = st.selectbox("Candidate", apps, format_func=lambda r: f"#{r['rank']} {r['name']} · {r['email']}")
            who = st.selectbox("Interviewer", call("GET", "/scheduling/interviewers") or [])
            duration = st.slider("Duration (minutes)", 15, 120, 45, 15)
            if st.button("Propose slots", type="primary", icon=":material/event_available:", width="stretch",
                         disabled=not (who and can("scheduling:manage"))):
                r = call("POST", "/scheduling/propose", json={"application_id": app["application_id"], "interviewer": who,
                                                              "duration_minutes": duration})
                if r:
                    st.success("Invitation drafted with free slots. Approve it in Approvals: you can book a slot there "
                               "or let the candidate choose.")
                    st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")

with right:
    st.subheader("Booked interviews")
    events = call("GET", "/scheduling/events") or []
    if not events:
        empty("No interview booked yet.", "Booked slots appear here, and are never offered twice.")
    else:
        st.dataframe([{"When": f"{when(e['start'], '%a %d %b, %H:%M')}–{when(e['end'], '%H:%M')}", "Interview": e["title"],
                       "Interviewer": e["interviewer"]} for e in events], hide_index=True, width="stretch")
