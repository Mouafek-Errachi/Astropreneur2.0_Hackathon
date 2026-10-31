import streamlit as st

from api import call, require_login

require_login()
st.header("Approval queue")
st.caption("Nothing leaves the system until a human approves it here.")

for a in call("GET", "/approvals", params={"status": "pending"}) or []:
    with st.expander(f"#{a['id']} {a['type']} — {a['title']} (risk: {a['risk_level']})"):
        st.write(a["rationale"])
        p = a["payload"]
        subject = st.text_input("Subject", p.get("subject", ""), key=f"s{a['id']}") if "subject" in p else None
        body = st.text_area("Body", p.get("body", ""), key=f"b{a['id']}", height=200) if "body" in p else None
        if "body" not in p:
            st.json(p)
        ack = st.checkbox("I reviewed the risk flags", key=f"k{a['id']}") if a["risk_level"] == "high" else False
        col1, col2 = st.columns(2)
        if col1.button("Approve", key=f"a{a['id']}"):
            call("POST", f"/approvals/{a['id']}/approve",
                 json={"edited_subject": subject, "edited_body": body, "acknowledge_risk": ack})
            st.rerun()
        if col2.button("Reject", key=f"r{a['id']}"):
            call("POST", f"/approvals/{a['id']}/reject", json={})
            st.rerun()

st.subheader("Outbox (sent after approval)")
st.dataframe(call("GET", "/approvals/outbox") or [])
