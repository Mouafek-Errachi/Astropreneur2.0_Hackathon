import streamlit as st

from api import call, require_login

require_login()
st.header("Inbox")

if st.button("Classify new emails"):
    call("POST", "/emails/classify")

digest = call("GET", "/emails/digest")
if digest:
    c1, c2, c3 = st.columns(3)
    c1.metric("Emails", digest["total"])
    c2.metric("Need reply", digest["needs_reply"])
    c3.metric("Flagged", len(digest["flagged"]))
    st.json(digest["by_category"])

for e in call("GET", "/emails") or []:
    label = f"[{e['category'] or '?'} / {e['priority'] or '?'}] {e['subject']} — {e['sender']}"
    if e["risk_level"] in ("medium", "high"):
        label = f"⚠️ {label}"
    with st.expander(label):
        st.text(e["body"])
        if e["injection_flags"]:
            st.warning(e["injection_flags"])
        if st.button("Draft reply", key=f"d{e['id']}"):
            r = call("POST", f"/emails/{e['id']}/draft-reply")
            if r:
                st.success(f"Draft queued for approval (action #{r['action_id']})")
