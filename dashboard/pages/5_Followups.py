import streamlit as st

from api import call, require_login

require_login()
st.header("Follow-ups")
st.caption("In production n8n calls the scan on a schedule; reminders to candidates still need approval.")

if st.button("Scan now"):
    r = call("POST", "/followups/scan")
    if r:
        st.success(f"{r['created']} new follow-ups, {r['reminders_proposed']} reminder drafts queued")

for f in call("GET", "/followups") or []:
    c1, c2 = st.columns([5, 1])
    c1.write(f"{'✅' if f['status'] == 'done' else '🕒'} {f['title']}" + ("  · reminder in Approvals" if f["action_id"] else ""))
    if f["status"] == "open" and c2.button("Done", key=f["id"]):
        call("POST", f"/followups/{f['id']}/done")
        st.rerun()
