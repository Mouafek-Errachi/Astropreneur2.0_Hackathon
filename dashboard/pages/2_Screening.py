import pandas as pd
import streamlit as st

from api import call, require_login

require_login()
st.header("Resume screening")

with st.expander("Create a job"):
    with st.form("job"):
        title = st.text_input("Title")
        desc = st.text_area("Description")
        req = st.text_input("Required skills (comma separated)")
        nice = st.text_input("Nice to have (comma separated)")
        years = st.number_input("Minimum years", 0.0, 40.0, 2.0)
        if st.form_submit_button("Create"):
            split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
            call("POST", "/jobs", json={"title": title, "description": desc, "required_skills": split(req),
                                        "nice_to_have": split(nice), "min_years": years})

jobs = call("GET", "/jobs") or []
if not jobs:
    st.info("Create a job, or log in as admin and click 'Load demo data'.")
    st.stop()
job = st.selectbox("Job", jobs, format_func=lambda j: f"{j['title']} ({j['applications']} applications)")
st.caption(f"Required: {', '.join(job['criteria']['required_skills'])} · Nice: {', '.join(job['criteria']['nice_to_have'])} "
           f"· Min years: {job['criteria']['min_years']}")

c1, c2 = st.columns([3, 1])
files = c1.file_uploader("Upload resumes (PDF, DOCX, TXT, up to 120)", accept_multiple_files=True)
if files and c1.button("Upload"):
    res = call("POST", f"/jobs/{job['id']}/resumes", files=[("files", (f.name, f.getvalue())) for f in files])
    if res:
        st.dataframe(pd.DataFrame(res), width="stretch")
if c2.button("▶ Run screening", type="primary"):
    with st.spinner("Screening…"):
        r = call("POST", f"/jobs/{job['id']}/screen")
    if r:
        st.toast(f"{r['screened']} resumes screened")

ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
if not ranking:
    st.stop()
RISK = {"none": "", "low": "⚠️", "medium": "🟠", "high": "🛡️"}
df = pd.DataFrame([{"rank": r["rank"], "name": r["name"] or r["filename"], "score": r["score"], "fit": r["bucket"],
                    "risk": f"{RISK[r['risk_level']]} {r['risk_level']}", "bias Δ": (r["fairness"] or {}).get("rank_delta"),
                    "status": r["status"]} for r in ranking])
st.dataframe(df, width="stretch", hide_index=True)

sel = st.selectbox("Candidate", ranking, format_func=lambda r: f"#{r['rank']} {r['name'] or r['filename']}")
left, right = st.columns(2)
with left:
    j = sel["justification"]
    st.subheader(f"{sel['name']} — {sel['score']}/100")
    for s in j.get("strengths", []):
        st.write(f"✅ {s}")
    for g in j.get("gaps", []):
        st.write(f"➖ {g}")
    if sel["fairness"]:
        f = sel["fairness"]
        st.caption(f"Bias auditor: score with identity masked = {f['counterfactual_score']} (Δ {f['rank_delta']}); "
                   f"masked: {', '.join(f.get('masked', []))}")
    if sel["flags"]:
        st.error("Injection guard: " + "; ".join(f"{x['type']} — “{x['snippet'][:80]}”" for x in sel["flags"][:5]))
    if sel["hidden_text"]:
        st.warning(f"Hidden text removed before the AI saw it: “{sel['hidden_text'][:300]}”")
    if sel["llm_fallback"]:
        st.info("LLM unavailable for this one: deterministic fallback used.")
with right:
    st.subheader("Propose an action")
    decision = st.selectbox("Decision", ["shortlisted", "interview", "rejected"])
    if st.button("Queue decision for approval"):
        r = call("POST", f"/applications/{sel['application_id']}/decision", json={"decision": decision})
        if r:
            st.success(f"Pending (risk: {r['risk_level']}) — see Approvals")
    kind = st.selectbox("Email", ["acknowledgement", "rejection", "reminder"])
    if st.button("Queue email for approval"):
        if call("POST", f"/applications/{sel['application_id']}/email", json={"kind": kind}):
            st.success("Email draft queued — see Approvals")
    st.subheader("Ask about this candidate")
    q = st.text_input("Question (answered from this resume only)")
    if q and st.button("Ask"):
        r = call("POST", f"/candidates/{sel['candidate_id']}/chat", json={"question": q})
        if r:
            st.write(r["answer"])
            for ev in r["evidence"]:
                st.caption(f"“{ev}”")
