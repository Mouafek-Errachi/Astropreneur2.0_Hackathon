import uuid

import pandas as pd
import streamlit as st

from api import call, can, require_login
from ui import empty, esc, human, page_header

require_login()
page_header("HR operations", "Group emails, team broadcasts, recognition points and the interview co-pilot. Every "
                             "message and award waits in Approvals.")
writer = can("hr:write")
ACTIONS = ["mentoring_session", "training_completed", "extra_task", "peer_review", "event_participation",
           "completed_onboarding"]
tab_group, tab2, tab3, tab4 = st.tabs(["Email a group", "Team broadcast", "Recognition points", "Interview co-pilot"])

with tab_group:
    st.caption("Say who should get it and what to tell them, in your own words. The AI writes the email, the "
               "recipients are looked up by job title, team or skill, and someone else approves it before it is sent.")
    with st.form("group_email"):
        request = st.text_area("Your request", placeholder="e.g. Send to all backend engineers that the meeting is "
                                                           "tomorrow at 18:00 in room B.")
        asked = st.form_submit_button("Draft the email", type="primary", icon=":material/edit_note:",
                                      disabled=not writer)
    if asked:
        if len(request.strip()) < 10:
            st.warning("Write who should receive it and what to tell them.")
        elif r := call("POST", "/hr/group-email", json={"request": request}):
            p = r["payload"]
            st.success(f"Drafted for **{len(p['recipients'])} people** ({esc(p['audience'])}). It waits in "
                       "Approvals: someone else checks the list and the message, then sends it.",
                       icon=":material/check_circle:")
            left, right = st.columns([3, 2], gap="large")
            with left, st.container(border=True):
                st.markdown(f"**{esc(p['subject'])}**")
                st.text(p["body"])
            with right:
                st.dataframe([{"Name": x["name"], "Job title": x["job_title"], "Team": human(x["team"])}
                              for x in p["recipients"]], hide_index=True, width="stretch")
            if r["risk_level"] != "none":
                st.warning("The checks flagged something (a figure or link not in your request, or an injection "
                           "attempt): see the flags in Approvals.", icon=":material/gpp_maybe:")
            st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")

with tab2:
    st.caption("Turn rough notes into a message for a team. A check flags any number or link that was not in your notes, "
               "and the person who drafts a broadcast cannot approve it.")
    teams = call("GET", "/hr/teams") or []
    with st.form("broadcast"):
        team = st.selectbox("Team", teams, format_func=lambda t: f"{t['display_name']} ({t['member_count']} people)")
        notes = st.text_area("Your notes", "team lunch fri 7 nov 12:30, cafeteria. bring your badge")
        drafted = st.form_submit_button("Draft broadcast", type="primary", icon=":material/campaign:", disabled=not writer)
    if drafted and team:
        r = call("POST", "/hr/broadcast", json={"target_team": team["id"], "message_prompt": notes})
        if r:
            st.success(r["note"], icon=":material/check_circle:")
            with st.container(border=True):
                st.markdown(f"**{esc(r['payload']['subject'])}**")
                st.text(r["payload"]["body"])
            if r["risk_level"] != "none":
                st.warning(f"Risk {r['risk_level']}: check the flags in Approvals (invented figures or links).",
                           icon=":material/gpp_maybe:")
            st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")

with tab3:
    left, right = st.columns([2, 3], gap="large")
    with left, st.form("award"):
        st.markdown("**Propose an award**")
        emps = [e for e in call("GET", "/hr/employees") or [] if e["active"]]
        emp = st.selectbox("Employee", emps, format_func=lambda e: f"{e['name']} · {e['team']}")
        action = st.selectbox("For", ACTIONS, format_func=human)
        note = st.text_input("Note", placeholder="Optional")
        if st.form_submit_button("Propose award", type="primary", icon=":material/military_tech:",
                                 disabled=not writer) and emp:
            r = call("POST", "/hr/points/award", json={"employee_id": emp["id"], "action_type": action, "note": note,
                                                        "idempotency_key": uuid.uuid4().hex[:16]})
            if r:
                st.success("Award sent to Approvals. The points come from the rule table, never from the request.")
    with right:
        st.markdown("**Leaderboard**")
        board = call("GET", "/hr/leaderboard") or []
        if not board:
            empty("No points yet this period.")
        else:
            df = pd.DataFrame(board)[["rank", "employee", "team", "points", "badge"]]
            st.dataframe(df, hide_index=True, width="stretch", column_config={
                "rank": st.column_config.NumberColumn("#", width="small"), "employee": "Employee", "team": "Team",
                "points": st.column_config.ProgressColumn("Points", min_value=0, max_value=int(df.points.max() or 1),
                                                          format="%d"),
                "badge": "Badge"})

with tab4:
    st.caption("Suggests follow-up questions to the interviewer when an answer is vague. The candidate never sees it, "
               "and it never scores anyone.")
    with st.form("copilot"):
        role = st.text_input("Job role", "Backend Engineer")
        comps = st.text_input("Competencies (comma separated)", "python, sql, system design")
        chunk = st.text_area("Latest part of the transcript", "Interviewer: Tell me about a hard bug.\n"
                                                              "Candidate: we did stuff basically, things like debugging.")
        analysed = st.form_submit_button("Analyse answer", type="primary", icon=":material/psychology:",
                                         disabled=not can("interview:assist"))
    if analysed:
        r = call("POST", "/interview/assist", json={"job_role": role, "current_chunk": chunk,
                                                    "competencies": [c.strip() for c in comps.split(",") if c.strip()]})
        if r:
            if r["weakness_detected"]:
                st.warning(f"**{human(r['severity'])} gap in {esc(r['competency'])}:** {esc(r['detected_gap'])}",
                           icon=":material/help:")
                st.markdown("**Ask next:**\n" + "\n".join(f"- {esc(q)}" for q in r["suggested_probing_questions"]))
            else:
                st.success("The answer looks solid.", icon=":material/thumb_up:")
            if r["security"]["injection_suspected"]:
                st.error("The transcript contains an injection attempt; the evaluation was not affected.",
                         icon=":material/shield:")
