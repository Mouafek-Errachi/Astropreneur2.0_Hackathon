import pandas as pd
import streamlit as st

from api import call, require_login

require_login()
st.header("Inbox")

with st.expander("Add an email (manual intake)"):
    with st.form("email"):
        sender = st.text_input("From")
        subject = st.text_input("Subject")
        body = st.text_area("Body")
        if st.form_submit_button("Add"):
            if call("POST", "/emails", json={"sender": sender, "subject": subject, "body": body}):
                st.success("Email added. Click 'Classify new emails' to triage it.")

if st.button("Classify new emails"):
    with st.spinner("Classifying..."):
        r = call("POST", "/emails/classify")
    if r is not None:
        st.success(f"{len(r)} emails classified")

st.subheader("Digest")
hours = st.select_slider("Window", options=[24, 48, 72, 168], value=24, format_func=lambda h: f"last {h}h")
digest = call("GET", "/emails/digest", params={"hours": hours})
if digest:
    st.info(digest["headline"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Emails", digest["total"])
    c2.metric("Urgent", len(digest["urgent"]))
    c3.metric("Need a human answer", len(digest["needs_reply"]))
    c4.metric("Flagged", len(digest["flagged"]))
    if digest["groups"]:
        st.caption("By type and urgency")
        st.dataframe(pd.DataFrame(digest["groups"]), hide_index=True, width="stretch")
    if digest["needs_reply"]:
        st.caption("Need a human answer (most urgent first)")
        st.dataframe(pd.DataFrame(digest["needs_reply"])[["priority", "sender", "subject", "summary", "id"]],
                     hide_index=True, width="stretch")
    if digest["flagged"]:
        st.caption("Flagged as suspicious: do not act on their content")
        st.dataframe(pd.DataFrame(digest["flagged"]), hide_index=True, width="stretch")

st.subheader("All emails")
for e in call("GET", "/emails") or []:
    label = f"[{e['category'] or '?'} / {e['priority'] or '?'}] {e['subject']} — {e['sender']}"
    if e["risk_level"] in ("medium", "high"):
        label = f"⚠️ {label}"
    if e["handled"]:
        label = f"✅ {label}"
    with st.expander(label):
        st.caption(e["received_at"])
        st.text(e["body"])
        if e["injection_flags"]:
            st.warning(e["injection_flags"])
        if not e["handled"] and st.button("Draft reply", key=f"d{e['id']}"):
            r = call("POST", f"/emails/{e['id']}/draft-reply")
            if r:
                st.success(f"Draft queued for approval (action #{r['action_id']})")
