import streamlit as st

from api import call, require_login

require_login()
st.header("Inbox by urgency")

if st.button("Classify new emails"):
    r = call("POST", "/emails/classify")
    if r:
        st.toast(f"{r['classified']} emails classified")

d = call("GET", "/emails/digest") or {}
if d:
    u = d["unread_by_urgency"]
    c = st.columns(5)
    c[0].metric("🔴 High", u.get("high", 0))
    c[1].metric("🟠 Medium", u.get("medium", 0))
    c[2].metric("⚪ Low", u.get("low", 0))
    c[3].metric("Need a reply", d["needs_reply"])
    c[4].metric("🛡️ Quarantined", d["quarantined"])

ICON = {"high": "🔴", "medium": "🟠", "low": "⚪", None: "❔"}
for e in call("GET", "/emails") or []:
    label = f"{ICON.get(e['urgency'])} [{e['category'] or 'unclassified'}] {e['subject'] or '(no subject)'} — {e['sender']}"
    if e["quarantined"]:
        label = f"🛡️ QUARANTINED · {label}"
    if e["handled"]:
        label = f"✅ {label}"
    with st.expander(label):
        if e["summary"]:
            st.caption(e["summary"])
        st.text(e["body"])
        if e["flags"]:
            st.warning("Guard flags: " + ", ".join(f"{f['type']} ({f['severity']})" for f in e["flags"]))
        if e["quarantined"]:
            st.error("High-risk content: the agents will not process this email. Handle it manually.")
        elif not e["handled"] and st.button("Draft a reply", key=f"d{e['id']}"):
            r = call("POST", f"/emails/{e['id']}/draft-reply")
            if r:
                st.success("Draft queued in Approvals")
                st.text(r["payload"]["body"])
