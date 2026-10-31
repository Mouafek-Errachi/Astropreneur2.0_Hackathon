from datetime import datetime

import streamlit as st

from api import call, require_login

require_login()
st.header("Approval queue")
st.caption("Nothing leaves the system until a human approves it here. Decisions about a candidate need a second person.")


def _slot_label(s: dict) -> str:
    start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
    return f"{start:%a %d %b, %H:%M}-{end:%H:%M} UTC"


pending = call("GET", "/approvals", params={"status": "pending"}) or []
if not pending:
    st.info("Nothing waiting for approval.")
for a in pending:
    with st.expander(f"#{a['id']} {a['type']} — {a['title']} (risk: {a['risk_level']})"):
        st.caption(f"Proposed by {a['created_by']} · {a['rationale']}")
        p = a["payload"]
        slot = None
        if a["type"] == "schedule_interview":
            slot = st.radio("Interview slot", range(len(p["slots"])), key=f"slot{a['id']}",
                            format_func=lambda i, slots=p["slots"]: _slot_label(slots[i]))
        if "to" in p and p["to"]:
            st.write(f"To: {p['to']}")
        subject = st.text_input("Subject", p.get("subject", ""), key=f"s{a['id']}") if "subject" in p else None
        body = st.text_area("Body", p.get("body", ""), key=f"b{a['id']}", height=200) if "body" in p else None
        if "body" not in p:
            st.json(p)
        ack = st.checkbox("I reviewed the risk flags", key=f"k{a['id']}") if a["risk_level"] == "high" else False
        note = st.text_input("Note", key=f"n{a['id']}")
        col1, col2 = st.columns(2)
        if col1.button("Approve", key=f"a{a['id']}"):
            r = call("POST", f"/approvals/{a['id']}/approve",
                     json={"edited_subject": subject, "edited_body": body, "acknowledge_risk": ack,
                           "selected_slot": slot, "note": note})
            if r:
                st.rerun()
        if col2.button("Reject", key=f"r{a['id']}"):
            if call("POST", f"/approvals/{a['id']}/reject", json={"note": note}):
                st.rerun()

st.subheader("Outbox (sent after approval)")
st.dataframe(call("GET", "/approvals/outbox") or [], width="stretch")

with st.expander("Decided recently"):
    for status in ("executed", "rejected", "cancelled"):
        rows = call("GET", "/approvals", params={"status": status}) or []
        st.caption(f"{status} ({len(rows)})")
        st.dataframe([{k: r[k] for k in ("id", "type", "title", "decided_by", "decision_note", "result")} for r in rows[:20]],
                     width="stretch")
