"""Home: what needs attention now, and a way into every page the role may use."""
import streamlit as st

from api import call, can, is_admin
from nav import visible_pages
from ui import flash, page_header, readable_hours

me = st.session_state.me
page_header(f"Welcome back, {me['email'].split('@')[0].split('.')[0].capitalize()}",
            "The agent executes, the human decides. Nothing is sent or recorded without a signed human approval.")

def outcome(x: dict) -> str:
    """What happened to one CV. A suspicious CV is kept (a person must see the attack) but held out of normal flow."""
    if not x["accepted"]:
        return "⛔ Refused"
    if x.get("risk_level") in ("medium", "high"):
        return "⚠️ Held for review"
    return "✅ Clean"


if r := st.session_state.pop("demo_result", None):
    results = [outcome(x) for x in r["resumes"]]
    clean, held, refused = (results.count(o) for o in ("✅ Clean", "⚠️ Held for review", "⛔ Refused"))
    st.success(f"Demo data loaded: {r['jobs']} jobs, {len(results)} CVs ({clean} clean, {held} held for review, "
               f"{refused} refused), {r['emails']} emails.", icon=":material/science:")
    with st.expander("What happened to each CV"):
        st.caption("Held for review: the guard found hidden text or instructions aimed at the AI. The CV is kept so a "
                   "person can see the attack, its application is marked 'needs review', and the planted text cannot "
                   "set its score (the score is computed in code). Refused: the file itself was not allowed (wrong type, fake extension).")
        st.dataframe([{"file": x["filename"], "job": x.get("job"), "result": res, "risk": x.get("risk_level") or "—",
                       "hidden text": "yes" if x.get("hidden_text_found") else "",
                       "reason refused": x.get("error") or ""} for x, res in zip(r["resumes"], results)],
                     width="stretch", hide_index=True)


def stat(col, label: str, value: int, page: str, link: str, help: str) -> None:
    with col.container(border=True):
        st.metric(label, value, help=help)
        st.page_link(page, label=link, icon=":material/arrow_forward:")


if can("approvals:read"):
    digest = call("GET", "/emails/digest") or {} if can("emails:read") else {}
    pending = call("GET", "/approvals", params={"status": "pending"}) or []
    followups = [f for f in call("GET", "/followups") or [] if f["status"] == "open"] if can("followups:read") else []
    unclassified = digest.get("unread_by_urgency", {}).get("unclassified", 0)

    cols = st.columns(4)
    stat(cols[0], "To approve", len(pending), "pages/3_Approvals.py", "Approvals",
         "Drafts and decisions the agents proposed. Nothing happens until a person approves.")
    stat(cols[1], "Need a reply", digest.get("needs_reply", 0), "pages/1_Inbox.py", "Inbox",
         "Emails the classifier says need a human answer.")
    stat(cols[2], "Follow-ups", len(followups), "pages/5_Followups.py", "Follow-ups",
         "Candidates and emails still waiting for an answer.")
    stat(cols[3], "Quarantined", digest.get("quarantined", 0), "pages/8_AI_Firewall.py", "AI Firewall",
         "High-risk emails the agents will not read.")

    needs_reply = digest.get("needs_reply", 0)
    todo = st.container(border=True)
    todo.subheader("What to do next")
    if unclassified:
        left, right = todo.columns([5, 1.4], vertical_alignment="center")
        left.write(f":material/mail: **{unclassified} new emails** have not been sorted by urgency yet.")
        if right.button("Sort them now", type="primary", disabled=not can("emails:process"), width="stretch"):
            with st.spinner("Classifying..."):
                done = call("POST", "/emails/classify")
            if done:
                flash(f"{done['classified']} emails sorted by urgency")
                st.rerun()
    for count, icon, text, link, label in [
        (len(pending), "fact_check", "**{n} proposals** are waiting for approval.", "pages/3_Approvals.py", "Review"),
        (needs_reply, "reply", "**{n} emails** need an answer from a person.", "pages/1_Inbox.py", "Open inbox"),
        (len(followups), "notifications_active", "**{n} follow-ups** are open.", "pages/5_Followups.py", "Open"),
    ]:
        if count:
            left, right = todo.columns([5, 1.4], vertical_alignment="center")
            left.write(f":material/{icon}: " + text.format(n=count))
            right.page_link(link, label=label, icon=":material/arrow_forward:")
    if not (unclassified or pending or needs_reply or followups):
        todo.success("You are all caught up.", icon=":material/task_alt:")
    if digest.get("headline"):
        todo.caption(readable_hours(digest["headline"]))

st.subheader("Where to go")
grid = st.columns(3)
for i, e in enumerate(visible_pages(me)):
    with grid[i % 3].container(border=True, height=120):
        st.page_link(e.path, label=f"**{e.title}**", icon=f":material/{e.icon}:")
        st.caption(e.blurb)

if is_admin():
    with st.expander("Demo data", icon=":material/science:"):
        st.caption("Fictional jobs, resumes (10 of them attacks) and 43 emails, to try every page.")
        bulk = st.checkbox("Also add 100 bulk resumes across the 5 jobs")
        if st.button("Load demo data"):
            with st.spinner("Loading demo data..."):
                loaded = call("POST", "/admin/demo/load", params={"bulk": str(bulk).lower()})
            if loaded:
                st.session_state.demo_result = loaded
                st.rerun()
