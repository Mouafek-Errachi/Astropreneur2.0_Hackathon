import streamlit as st

from api import call, require_login

require_login()
st.header("Follow-ups")
st.caption("Unanswered inbox emails, and candidates who have not answered our last message. "
           "Reminder drafts go to the approval queue; they are withdrawn automatically if the candidate writes back.")

if st.button("Scan for follow-ups"):
    r = call("POST", "/followups/scan")
    if r:
        st.success(f"{r['emails']} unanswered emails, {r['candidates']} silent candidates, "
                   f"{r['reminders_queued']} reminder drafts queued for approval")

ICON = {"email_unanswered": "📨", "candidate_no_reply": "👤"}
for f in call("GET", "/followups") or []:
    col1, col2 = st.columns([4, 1])
    status = "✅" if f["status"] == "done" else "🕒"
    extra = f" · reminder draft #{f['action_id']} in the approval queue" if f.get("action_id") and f["status"] == "open" else ""
    col1.write(f"{status} {ICON.get(f['kind'], '')} {f['title']}{extra}")
    if f["status"] == "open" and col2.button("Done", key=f"f{f['id']}"):
        if call("POST", f"/followups/{f['id']}/done"):
            st.rerun()
