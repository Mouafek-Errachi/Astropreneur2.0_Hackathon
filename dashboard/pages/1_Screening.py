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
    st.info("Create a job first (or load the demo data from the home page as admin).")
    st.stop()
job = st.selectbox("Job", jobs, format_func=lambda j: f"{j['title']} ({j['candidate_count']} candidates)")
st.caption(f"Required: {', '.join(job['required_skills']) or '-'} · Nice to have: {', '.join(job['nice_to_have']) or '-'} "
           f"· Min years: {job['min_years']:g}")

files = st.file_uploader("Upload resumes (PDF, DOCX, TXT)", accept_multiple_files=True)
if files and st.button("Upload"):
    res = call("POST", f"/jobs/{job['id']}/resumes", files=[("files", (f.name, f.getvalue())) for f in files])
    if res:
        st.dataframe(pd.DataFrame(res))

if st.button("Run screening"):
    with st.spinner("Screening..."):
        call("POST", f"/jobs/{job['id']}/screen")

ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
if not ranking:
    st.stop()

st.subheader("Ranked shortlist")
st.caption("Scores come from job criteria only (skills and experience), computed by code from the extracted facts.")
df = pd.DataFrame([{"rank": c["rank"], "name": c["name"] or c["original_filename"], "score": c["score"],
                    "bucket": c["bucket"], "justification": c["justification"],
                    "risk": ("⚠️ " if c["needs_review"] else "") + c["risk_level"], "status": c["status"], "id": c["id"]}
                   for c in ranking])
st.dataframe(df, width="stretch", hide_index=True)

cid = st.selectbox("Candidate", [c["id"] for c in ranking],
                   format_func=lambda i: next(f"#{c['rank'] or '-'} {c['name'] or c['original_filename']}"
                                              for c in ranking if c["id"] == i))
cand = next(c for c in ranking if c["id"] == cid)
left, right = st.columns(2)
with left:
    st.markdown(f"**Why this rank:** {cand['justification'] or 'not screened yet'}")
    b = cand["score_breakdown"]
    if b:
        st.write(f"Required matched: {', '.join(b['required_matched']) or '-'}")
        st.write(f"Required missing: {', '.join(b['required_missing']) or '-'}")
        st.write(f"Nice to have: {', '.join(b['nice_matched']) or '-'}")
    if cand["injection_flags"]:
        st.warning("Possible prompt injection in this resume. Review it before any decision.")
        st.json(cand["injection_flags"])
    with st.expander("Extracted profile"):
        st.json(cand["profile"])
with right:
    st.markdown("**Propose a decision** (someone else approves it)")
    decision = st.selectbox("Decision", ["shortlisted", "interview", "rejected"])
    note = st.text_input("Reason (job-related)")
    if st.button("Send decision to approval queue"):
        r = call("POST", f"/candidates/{cid}/decision", json={"decision": decision, "note": note})
        if r:
            st.success(f"Pending action #{r['action_id']} created")
    st.markdown("**Draft an email to this candidate** (from their own file only)")
    kind = st.selectbox("Email type", ["acknowledgement", "invitation", "rejection"])
    if st.button("Draft email"):
        r = call("POST", f"/candidates/{cid}/draft-reply", json={"kind": kind})
        if r:
            st.success(f"Draft queued for approval (action #{r['action_id']}, risk: {r['risk_level']})")
            st.text(f"Subject: {r['draft']['subject']}\n\n{r['draft']['body']}")
