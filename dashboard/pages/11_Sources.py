import pandas as pd
import streamlit as st

from api import API_URL, call, is_admin, require_login
from ui import empty, esc, human, page_header, when

require_login()
page_header("CV sources", "Every CV received from n8n (Gmail, Google Drive, the application form), job boards and "
                          "dashboard uploads. All of them go through the same checks: file type, hidden text, "
                          "prompt-injection scan, duplicates.")

SOURCES = ["upload", "email", "drive", "form", "job_board", "linkedin", "other", "demo"]
jobs = call("GET", "/jobs") or []
job_names = {j["id"]: j["title"] for j in jobs}
f1, f2 = st.columns(2)
job_id = f1.selectbox("Job", [None, *job_names], format_func=lambda j: "All jobs" if j is None else job_names[j])
source = f2.selectbox("Source", [None, *SOURCES], format_func=lambda s: "All sources" if s is None else human(s))
params = {k: v for k, v in {"job_id": job_id, "source": source}.items() if v is not None}
log = call("GET", "/intake/log", params=params) or []

if not log:
    empty("Nothing received yet.", "CVs appear here as soon as n8n, a job board or an upload sends one.")
else:
    df = pd.DataFrame(log)
    flagged = df["risk_level"].isin(["medium", "high"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Received", len(df), border=True)
    c2.metric("Accepted", int(df["accepted"].sum()), border=True)
    c3.metric("Refused", int((~df["accepted"]).sum()), border=True, help="Wrong file type, duplicate, unreadable…")
    c4.metric("Flagged for review", int((df["accepted"] & flagged).sum()), border=True,
              help="Accepted, but possible prompt injection or hidden text: review before any decision.")
    by_source = df.groupby(df["source"].map(human)).size().rename("CVs").sort_values()
    if len(by_source) > 1:  # one bar across the page says nothing
        st.bar_chart(by_source, horizontal=True, height=40 + 32 * len(by_source))
    else:
        st.caption(f"All {len(df)} CVs came from: {by_source.index[0]}.")
    table = pd.DataFrame({
        "Received": df["created_at"].map(when), "Source": df["source"].map(human), "Job": df["job_id"].map(job_names),
        "File": df["filename"],
        "Status": df.apply(lambda r: ("⚠️ review" if r["risk_level"] in ("medium", "high") else "✅ accepted")
                           if r["accepted"] else f"❌ {r['error']}", axis=1),
        "Flags": df["flags"].map(lambda f: ", ".join(human(x) for x in f)), "Reference": df["source_ref"].fillna(""),
        "By": df["actor"]})
    empty_cols = [c for c in ("Flags", "Reference") if not table[c].astype(str).str.strip().any()]
    st.dataframe(table.drop(columns=empty_cols), hide_index=True, width="stretch",
                 column_config={"File": st.column_config.TextColumn(width="medium")})

st.subheader("Connect a source")
with st.expander("How outside tools send CVs", icon=":material/integration_instructions:"):
    st.markdown(f"""Outside tools send CVs to **`POST {API_URL}/intake/resumes`** with an `X-API-Key` header:

```json
{{"job_id": "<job id>", "items": [{{"source": "email", "source_ref": "gmail-123", "filename": "cv.pdf",
                                  "content_base64": "JVBERi0..."}}]}}
```
Each item has exactly one of `content_base64` (PDF, Word, OpenDocument, RTF, HTML, text, JSON, JPG/PNG/WebP), `text`
or `profile` (structured JSON). The n8n workflow `n8n/workflows/intake-resume-collector.json` already does this for
Gmail, Google Drive, an application form and a job-board webhook.""")

if not is_admin():
    st.info("Ask an admin for an upload key.", icon=":material/key:")
    st.stop()

if new_key := st.session_state.pop("new_key", None):
    st.success("Copy this key now: it is shown only once.", icon=":material/key:")
    st.code(new_key)
with st.form("new_key_form"):
    name = st.text_input("Key name", "n8n intake")
    st.caption("An upload-only key can send CVs and nothing else: it cannot read the ranking, approve or send emails.")
    if st.form_submit_button("Create an upload-only key", icon=":material/add:"):
        r = call("POST", "/auth/api-keys", json={"name": name, "scopes": ["resumes:upload"]})
        if r:
            st.session_state.new_key = r["key"]
            st.rerun()
for k in call("GET", "/auth/api-keys") or []:
    with st.container(border=True):
        left, right = st.columns([5, 1], vertical_alignment="center")
        left.markdown(f"{'🟢' if k['active'] else '⚫'} **{esc(k['name'])}** · `hrk_{k['prefix']}_…` · "
                      f"{', '.join(k['scopes'])} · last used {when(k['last_used_at']) if k['last_used_at'] else 'never'}")
        if k["active"] and right.button("Revoke", key=f"revoke{k['id']}", icon=":material/block:", width="stretch"):
            if call("POST", f"/auth/api-keys/{k['id']}/revoke"):
                st.rerun()
