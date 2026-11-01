"""Inbox by urgency. Email text is untrusted: it is shown as plain text, never rendered as Markdown."""
import streamlit as st

from api import call, can, require_login
from ui import URGENCY, chip, empty, esc, flash, human, page_header, when

require_login()
page_header("Inbox", "Emails sorted by urgency, with a reply drafted on request. High-risk emails are quarantined: "
                     "the agents never read them.")

digest = call("GET", "/emails/digest") or {}
emails = call("GET", "/emails") or []
counts = digest.get("unread_by_urgency", {})

if unsorted := counts.get("unclassified", 0):
    left, right = st.columns([4, 1], vertical_alignment="center")
    left.info(f"**{unsorted} new emails** are not sorted by urgency yet.", icon=":material/mail:")
    if right.button("Sort them now", type="primary", disabled=not can("emails:process"), width="stretch"):
        with st.spinner("Classifying..."):
            r = call("POST", "/emails/classify")
        if r:
            flash(f"{r['classified']} emails sorted by urgency")
            st.rerun()

cols = st.columns(5)
cols[0].metric("🔴 Urgent", counts.get("high", 0), border=True, help="Unread emails classified as high urgency.")
cols[1].metric("🟠 Medium", counts.get("medium", 0), border=True)
cols[2].metric("⚪ Low", counts.get("low", 0), border=True)
cols[3].metric("Need a reply", digest.get("needs_reply", 0), border=True)
cols[4].metric("🛡️ Quarantined", digest.get("quarantined", 0), border=True,
               help="High-risk emails (phishing, injection attempts). The agents will not process them.")

if not emails:
    empty("No emails yet.", "Emails arrive from n8n (IMAP) or from the demo data on the Home page.")
    st.stop()

VIEWS = {
    "All": lambda e: True,
    "Need a reply": lambda e: e["needs_reply"] and not e["handled"],
    "Urgent": lambda e: e["urgency"] == "high",
    "Quarantined": lambda e: e["quarantined"],
}
f1, f2, f3 = st.columns([3, 3, 1.4], vertical_alignment="bottom")
view = f1.segmented_control("Show", list(VIEWS), default="All", required=True)
query = f2.text_input("Search", placeholder="Sender or subject").strip().lower()
hide_done = f3.toggle("Hide handled")

RANK = {"high": 0, "medium": 1, "low": 2}
shown = sorted(sorted(emails, key=lambda e: e["received_at"] or "", reverse=True),  # newest first within a level
               key=lambda e: (e["handled"], e["quarantined"], RANK.get(e["urgency"], 3)))
shown = [e for e in shown if VIEWS[view](e) and not (hide_done and e["handled"])
         and (not query or query in f"{e['sender']} {e['subject']}".lower())]
st.caption(f"Showing {len(shown)} of {len(emails)} emails, most urgent first.")
if not shown:
    empty("No email matches these filters.")

DOT = {"high": "🔴", "medium": "🟠", "low": "⚪"}
for e in shown:
    icon = "🛡️" if e["quarantined"] else "✅" if e["handled"] else DOT.get(e["urgency"], "❔")
    with st.expander(f"{icon} **{esc(e['subject'] or '(no subject)')}** · {esc(e['sender'])}"):
        level, color = URGENCY.get(e["urgency"], ("Not sorted yet", "gray"))
        tags = [chip(level, color)]
        if e["category"]:
            tags.append(chip(human(e["category"]), "blue"))
        if e["needs_reply"] and not e["handled"]:
            tags.append(chip("Needs a reply", "violet", "reply"))
        if e["handled"]:
            tags.append(chip("Handled", "green", "check"))
        tags.append(chip(when(e["received_at"]), "gray", "schedule"))
        st.markdown(" ".join(tags))
        if e["summary"]:
            st.markdown(f"**Summary:** {esc(e['summary'])}")
        with st.container(border=True):
            st.text(e["body"])
        if e["flags"]:
            st.warning("The guard flagged: " + ", ".join(f"{esc(human(f['type']))} ({f['severity']})" for f in e["flags"]),
                       icon=":material/gpp_maybe:")
        if e["quarantined"]:
            st.error("High-risk content: the agents will not process this email. Handle it by hand.",
                     icon=":material/shield:")
        elif not e["handled"] and can("drafts:create") and st.button("Draft a reply", key=f"d{e['id']}",
                                                                     icon=":material/edit:"):
            r = call("POST", f"/emails/{e['id']}/draft-reply")
            if r:
                st.success("Reply drafted. It waits in Approvals until a person approves it.")
                st.text(r["payload"]["body"])
                st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")
