import pandas as pd
import streamlit as st

from api import call, require_login

require_login()
st.header("Resume screening")

with st.expander("Create job"):
    with st.form("job"):
        title = st.text_input("Title")
        desc = st.text_area("Description")
        req = st.text_input("Required skills (comma separated)")
        nice = st.text_input("Nice to have (comma separated)")
        years = st.number_input("Min years", 0.0, 40.0, 2.0)
        if st.form_submit_button("Create"):
            split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
            call("POST", "/jobs", json={"title": title, "description": desc, "required_skills": split(req),
                                        "nice_to_have": split(nice), "min_years": years})

jobs = call("GET", "/jobs") or []
if not jobs:
    st.info("Create a job first.")
    st.stop()
job = st.selectbox("Job", jobs, format_func=lambda j: f"{j['title']} ({j['candidate_count']} candidates)")

files = st.file_uploader("Upload resumes (PDF, DOCX, TXT)", accept_multiple_files=True)
if files and st.button("Upload"):
    res = call("POST", f"/jobs/{job['id']}/resumes", files=[("files", (f.name, f.getvalue())) for f in files])
    if res:
        st.dataframe(pd.DataFrame(res))

if st.button("Run screening"):
    call("POST", f"/jobs/{job['id']}/screen")

ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
if ranking:
    df = pd.DataFrame([{k: c[k] for k in ("rank", "name", "score", "bucket", "risk_level", "needs_review", "status", "id")}
                       for c in ranking])
    st.dataframe(df, use_container_width=True)
    cid = st.selectbox("Candidate", [c["id"] for c in ranking])
    cand = next(c for c in ranking if c["id"] == cid)
    st.json({"score": cand["score_breakdown"], "flags": cand["injection_flags"], "profile": cand["profile"]})
    decision = st.selectbox("Propose decision", ["shortlisted", "interview", "rejected"])
    if st.button("Send to approval queue"):
        r = call("POST", f"/candidates/{cid}/decision", json={"decision": decision})
        if r:
            st.success(f"Pending action #{r['action_id']} created")
