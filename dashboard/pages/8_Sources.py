import pandas as pd
import streamlit as st

from api import API_URL, call, is_admin, require_login

require_login()
st.header("CV sources")
st.caption("Every CV received from n8n (Gmail, Google Drive, the application form), job boards and dashboard uploads. "
           "All of them go through the same checks: file type, hidden text, prompt-injection scan, duplicates.")

jobs = call("GET", "/jobs") or []
job_names = {j["id"]: j["title"] for j in jobs}
f1, f2 = st.columns(2)
job_id = f1.selectbox("Job", [None, *job_names], format_func=lambda j: "All jobs" if j is None else job_names[j])
source = f2.selectbox("Source", [None, "upload", "email", "drive", "form", "job_board", "linkedin", "other", "demo"],
                      format_func=lambda s: "All sources" if s is None else s)
params = {k: v for k, v in {"job_id": job_id, "source": source}.items() if v is not None}
log = call("GET", "/intake/log", params=params) or []

if not log:
    st.info("Nothing received yet.")
else:
    df = pd.DataFrame(log)
    flagged = df["risk_level"].isin(["medium", "high"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Received", len(df))
    c2.metric("Accepted", int(df["accepted"].sum()))
    c3.metric("Refused", int((~df["accepted"]).sum()))
    c4.metric("Flagged for review", int((df["accepted"] & flagged).sum()))
    st.bar_chart(df.groupby("source").size().rename("CVs"), horizontal=True, height=180)
    df["job"] = df["job_id"].map(job_names)
    df["flags"] = df["flags"].map(", ".join)
    df["status"] = df.apply(lambda r: ("⚠️ review" if r["risk_level"] in ("medium", "high") else "✅ accepted")
                            if r["accepted"] else f"❌ {r['error']}", axis=1)
    st.dataframe(df[["created_at", "source", "job", "filename", "mime_type", "status", "flags", "candidate_id",
                     "source_ref", "actor"]], hide_index=True, width="stretch",
                 column_config={"created_at": st.column_config.DatetimeColumn("Received", format="DD/MM HH:mm"),
                                "candidate_id": st.column_config.NumberColumn("Candidate #", format="%d")})

# ------------------------------------------------------------------ integration keys (admin)
st.divider()
st.subheader("Connect a source")
st.markdown(f"""Outside tools send CVs to **`POST {API_URL}/intake/resumes`** with an `X-API-Key` header:

```json
{{"job_id": 1, "items": [{{"source": "email", "source_ref": "gmail-123", "filename": "cv.pdf",
                         "content_base64": "JVBERi0..."}}]}}
```
Each item has exactly one of `content_base64` (PDF, Word, OpenDocument, RTF, HTML, text, JSON, JPG/PNG/WebP), `text`
or `profile` (structured JSON). The n8n workflow `n8n/workflows/intake-resume-collector.json` already does this for
Gmail, Google Drive, an application form and a job-board webhook.""")

if not is_admin():
    st.info("Ask an admin for an upload key.")
    st.stop()

if new_key := st.session_state.pop("new_key", None):
    st.success("Copy this key now: it is shown only once.")
    st.code(new_key)
with st.form("new_key_form"):
    name = st.text_input("Key name", "n8n intake")
    if st.form_submit_button("Create an upload-only key"):
        r = call("POST", "/admin/api-keys", json={"name": name, "scopes": ["resumes:upload"]})
        if r:
            st.session_state.new_key = r["key"]
            st.rerun()
st.caption("An upload-only key can send CVs and nothing else: it cannot read the ranking, approve or send emails.")
for k in call("GET", "/admin/api-keys") or []:
    left, right = st.columns([5, 1])
    used = k["last_used_at"] or "never"
    left.write(f"{'🟢' if k['active'] else '⚫'} **{k['name']}** · `hrk_{k['prefix']}_…` · {', '.join(k['scopes'])} "
               f"· last used {used}")
    if k["active"] and right.button("Revoke", key=f"revoke{k['id']}"):
        if call("POST", f"/admin/api-keys/{k['id']}/revoke"):
            st.rerun()
