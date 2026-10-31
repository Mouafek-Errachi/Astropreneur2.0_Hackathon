import streamlit as st

from api import call, require_login

require_login()
st.header("Approval queue (human in the loop)")
st.caption("Agents only propose. Each approval is signed and verified by the executor before anything is sent or recorded.")

RISK = {"none": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴"}
pending = call("GET", "/approvals", params={"status": "pending"}) or []
if not pending:
    st.success("Nothing waiting for approval.")
for a in pending:
    with st.expander(f"{RISK[a['risk_level']]} {a['type']} — {a['title']}  ·  by {a['created_by_agent']}", expanded=True):
        st.caption(f"{a['rationale']}  ·  requested by {a['requested_by'] or '—'}")
        p = a["payload"]
        if a["risk_flags"]:
            st.error("Flags: " + "; ".join(f"{f['type']} ({f['severity']})" for f in a["risk_flags"][:8]))
        subject = body = None
        if "body" in p:
            st.text(f"To: {p.get('to')}  (the recipient cannot be edited)")
            subject = st.text_input("Subject", p.get("subject", ""), key=f"s{a['id']}")
            body = st.text_area("Body", p.get("body", ""), key=f"b{a['id']}", height=220)
        if "slots" in p:
            st.write("Slots offered:", ", ".join(s["start"].replace("T", " ")[:16] for s in p["slots"]))
        if "body" not in p:
            st.json(p)
        ack = st.checkbox("I reviewed the risk flags", key=f"k{a['id']}") if a["risk_level"] == "high" else False
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("✅ Approve", key=f"a{a['id']}"):
            edits = {}
            if subject is not None and subject != p.get("subject"):
                edits["edited_subject"] = subject
            if body is not None and body != p.get("body"):
                edits["edited_body"] = body
            r = call("POST", f"/approvals/{a['id']}/approve", json={**edits, "acknowledge_risk": ack})
            if r:
                st.toast(f"{r['status']}: {r['result']}")
                st.rerun()
        if c2.button("❌ Reject", key=f"r{a['id']}"):
            call("POST", f"/approvals/{a['id']}/reject", json={})
            st.rerun()

tab1, tab2 = st.tabs(["Sent emails", "History"])
with tab1:
    st.dataframe(call("GET", "/approvals/sent") or [], width="stretch")
with tab2:
    hist = [a for a in call("GET", "/approvals") or [] if a["status"] != "pending"]
    st.dataframe([{"type": a["type"], "title": a["title"], "status": a["status"], "result": a["result"],
                   "reviewer": (a["approval"] or {}).get("reviewer")} for a in hist], width="stretch")
