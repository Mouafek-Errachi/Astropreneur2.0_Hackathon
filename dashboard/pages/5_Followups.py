import streamlit as st

from api import call, can, require_login
from ui import chip, empty, esc, flash, page_header, when

require_login()
page_header("Follow-ups", "Candidates who have not answered our last message, and emails nobody answered. In production "
                          "n8n runs this scan on a schedule; reminders to candidates still need approval.")

KIND = {"email_unanswered": ("Email not answered", "mail"), "candidate_no_contact": ("Candidate has not answered", "person")}
items = call("GET", "/followups") or []
open_items = [f for f in items if f["status"] == "open"]

left, right = st.columns([4, 1], vertical_alignment="center")
left.markdown(f"**{len(open_items)} open** · {len(items) - len(open_items)} done")
if right.button("Scan now", type="primary", icon=":material/search:", width="stretch",
                disabled=not can("followups:manage")):
    r = call("POST", "/followups/scan")
    if r:
        flash(f"{r['created']} new follow-ups, {r['reminders_proposed']} reminder drafts sent to Approvals")
        st.rerun()

show_done = st.toggle("Show done", value=False)
shown = items if show_done else open_items
if not shown:
    empty("Nothing to follow up.", "Click Scan now to look for candidates and emails waiting for an answer.")
for f in shown:
    with st.container(border=True):
        text, action = st.columns([6, 1], vertical_alignment="center")
        kind, icon = KIND.get(f["kind"], (f["kind"], "schedule"))
        tags = [chip(kind, "blue", icon), chip(f"due {when(f['due_at'])}", "gray", "schedule")]
        if f["action_id"]:
            tags.append(chip("reminder waiting in Approvals", "violet", "fact_check"))
        done = f["status"] == "done"
        text.markdown(f"{'✅' if done else '🕒'} **{esc(f['title'])}**  \n" + " ".join(tags))
        if not done and action.button("Mark done", key=f["id"], icon=":material/check:", width="stretch",
                                      disabled=not can("followups:manage")):
            if call("POST", f"/followups/{f['id']}/done"):
                flash("Marked as done")
                st.rerun()
