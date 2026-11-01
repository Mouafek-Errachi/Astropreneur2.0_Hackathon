"""Approval queue (human in the loop): agents only propose; nothing is sent or recorded until a person approves here."""
import re
from datetime import datetime

import streamlit as st

from api import call, can, require_login
from ui import RISK, chip, empty, esc, flash, human, page_header, when

require_login()
page_header("Approvals", "Agents only propose. Each approval is signed and checked again before anything is sent or "
                         "recorded. Decisions about a candidate, learned weights and broadcasts need a second person.")

TYPES = {"send_email": ("Email", "mail"), "send_reminder": ("Reminder", "notifications"),
         "record_decision": ("Candidate decision", "how_to_reg"), "schedule_interview": ("Interview invitation", "event"),
         "apply_weights": ("Learned weights", "tune"), "award_points": ("Recognition points", "military_tech"),
         "broadcast": ("Team broadcast", "campaign"), "create_job_offer": ("Job offer", "work")}


def _slot_label(s: dict) -> str:
    start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
    return f"{start:%a %d %b, %H:%M}-{end:%H:%M}"


def facts(p: dict) -> None:
    """The simple fields of a non-email proposal, without the internal ids; everything else under 'All details'."""
    def value(v):  # mentoring_session -> Mentoring session
        return human(v) if isinstance(v, str) and re.fullmatch(r"[a-z]+(_[a-z]+)+", v) else v
    rows = [f"- **{human(k)}:** {esc(value(v))}" for k, v in p.items()
            if isinstance(v, (str, int, float, bool)) and str(v).strip()
            and k not in ("id", "idempotency_key") and not k.endswith("_id")]
    if rows:
        st.markdown("\n".join(rows))
    with st.expander("All details"):
        st.json(p)


def review(a: dict) -> None:
    p = a["payload"]
    kind, icon = TYPES.get(a["type"], (human(a["type"]), "task"))
    level, color = RISK.get(a["risk_level"], ("Risk unknown", "gray"))
    st.markdown(f"#### {esc(a['title'])}")
    st.markdown(" ".join([chip(kind, "blue", icon), chip(level, color),
                          chip(f"proposed by {human(a['created_by_agent'])}", "gray", "smart_toy")]))
    st.caption(" · ".join(x for x in (esc(a["rationale"]), f"requested by {esc(a['requested_by'] or '—')}") if x))
    if a["risk_flags"]:
        st.warning("Check before approving: " + "; ".join(f"{esc(human(f['type']))} ({f['severity']})"
                                                         for f in a["risk_flags"][:8]), icon=":material/gpp_maybe:")
    subject = body = slot = None
    if "body" in p:
        st.text_input("To", p.get("to"), disabled=True, key=f"to{a['id']}", help="The recipient cannot be changed.")
        subject = st.text_input("Subject", p.get("subject", ""), key=f"s{a['id']}")
        body = st.text_area("Message", p.get("body", ""), key=f"b{a['id']}", height=220,
                            help="Edit freely: your version is what will be sent.")
    if "slots" in p:
        options = [None] + list(range(len(p["slots"])))
        slot = st.radio("Interview slot", options, key=f"slot{a['id']}",
                        format_func=lambda i, slots=p["slots"]: "Let the candidate choose (single-use link)"
                        if i is None else f"Book now: {_slot_label(slots[i])}")
    if "body" not in p:
        facts(p)
    note = st.text_input("Note (optional)", key=f"n{a['id']}", placeholder="Kept in the audit log")
    high = a["risk_level"] == "high"
    ack = st.checkbox("I have reviewed the risk flags", key=f"k{a['id']}") if high else False
    decide = can("approvals:decide")
    why_not = None if decide else "Your role can see the queue but not decide."
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Approve", key=f"a{a['id']}", type="primary", icon=":material/check:", width="stretch",
                 disabled=not decide or (high and not ack), help=why_not or ("Tick the box above first." if high else None)):
        edits = {}
        if subject is not None and subject != p.get("subject"):
            edits["edited_subject"] = subject
        if body is not None and body != p.get("body"):
            edits["edited_body"] = body
        r = call("POST", f"/approvals/{a['id']}/approve",
                 json={**edits, "acknowledge_risk": ack, "selected_slot": slot, "note": note})
        if r:
            flash(f"{human(r['status'])}: {esc(r['result'])}")
            st.rerun()
    if c2.button("Reject", key=f"r{a['id']}", icon=":material/close:", width="stretch", disabled=not decide, help=why_not):
        if call("POST", f"/approvals/{a['id']}/reject", json={"note": note}):
            flash("Rejected. Nothing was sent or recorded.", icon=":material/block:")
            st.rerun()


pending = call("GET", "/approvals", params={"status": "pending"}) or []
history = [a for a in call("GET", "/approvals") or [] if a["status"] != "pending"]
sent = call("GET", "/approvals/sent") or []
tab_wait, tab_sent, tab_hist = st.tabs([f"Waiting ({len(pending)})", f"Sent emails ({len(sent)})",
                                        f"History ({len(history)})"])

with tab_wait:
    if not pending:
        st.success("Nothing is waiting for approval.", icon=":material/task_alt:")
    for a in pending:
        with st.container(border=True):
            review(a)

with tab_sent:
    if not sent:
        empty("No email sent yet.", "Approved emails appear here once the executor has sent them.")
    else:
        rows = [{"Sent": when(m["sent_at"]), "To": m["to"], "Subject": m["subject"], "Via": m["transport"]} for m in sent]
        picked = st.dataframe(rows, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                              key="sent_table")
        if picked.selection.rows:
            m = sent[picked.selection.rows[0]]
            with st.container(border=True):
                st.markdown(f"**{esc(m['subject'])}**  \nto {esc(m['to'])} · {when(m['sent_at'])}")
                st.text(m["body"])
        else:
            st.caption("Select a row to read the email.")

with tab_hist:
    if not history:
        empty("No decision yet.")
    else:
        st.dataframe([{"What": TYPES.get(a["type"], (human(a["type"]),))[0], "Title": a["title"],
                       "Outcome": human(a["status"]), "Result": a["result"],
                       "Decided by": (a["approval"] or {}).get("reviewer")} for a in history],
                     hide_index=True, width="stretch")
