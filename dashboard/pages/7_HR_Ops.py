import uuid

import pandas as pd
import streamlit as st

from api import call, require_login

require_login()
st.header("HR operations")
tab1, tab2, tab3, tab4 = st.tabs(["Tickets", "Team broadcast", "Points & leaderboard", "Interview co-pilot"])

with tab1:
    st.dataframe(call("GET", "/hr/tickets") or [], width="stretch")
    st.caption("Each ticket's drafted reply waits in Approvals.")

with tab2:
    teams = call("GET", "/hr/teams") or []
    team = st.selectbox("Team", teams, format_func=lambda t: f"{t['display_name']} ({t['member_count']})")
    notes = st.text_area("Rough notes", "team lunch fri 7 nov 12:30, cafeteria. bring your badge")
    if st.button("Draft broadcast") and team:
        r = call("POST", "/hr/broadcast", json={"target_team": team["id"], "message_prompt": notes})
        if r:
            st.success(r["note"])
            st.text(r["payload"]["subject"] + "\n\n" + r["payload"]["body"])
            if r["risk_level"] != "none":
                st.warning(f"Risk {r['risk_level']}: check the flags in Approvals (invented figures or links).")

with tab3:
    emps = call("GET", "/hr/employees") or []
    c1, c2 = st.columns(2)
    emp = c1.selectbox("Employee", [e for e in emps if e["active"]], format_func=lambda e: f"{e['name']} · {e['team']}")
    action = c2.selectbox("Action", ["mentoring_session", "training_completed", "extra_task", "peer_review",
                                     "event_participation", "completed_onboarding"])
    note = st.text_input("Note")
    if st.button("Propose award") and emp:
        r = call("POST", "/hr/points/award", json={"employee_id": emp["id"], "action_type": action, "note": note,
                                                    "idempotency_key": uuid.uuid4().hex[:16]})
        if r:
            st.success("Award queued for approval (points come from the rule table, never from the request)")
    board = call("GET", "/hr/leaderboard") or []
    if board:
        st.dataframe(pd.DataFrame(board)[["rank", "employee", "team", "points", "badge"]], hide_index=True,
                     width="stretch")

with tab4:
    role = st.text_input("Job role", "Backend Engineer")
    comps = st.text_input("Competencies (comma separated)", "python, sql, system design")
    chunk = st.text_area("Latest transcript chunk", "Interviewer: Tell me about a hard bug.\nCandidate: we did stuff basically, things like debugging.")
    if st.button("Analyse answer"):
        r = call("POST", "/interview/assist", json={"job_role": role, "competencies": [c.strip() for c in comps.split(",") if c.strip()],
                                                    "current_chunk": chunk})
        if r:
            if r["weakness_detected"]:
                st.warning(f"{r['severity']} gap in {r['competency']}: {r['detected_gap']}")
                for q in r["suggested_probing_questions"]:
                    st.write(f"❓ {q}")
            else:
                st.success("Answer looks solid.")
            if r["security"]["injection_suspected"]:
                st.error("The transcript contains an injection attempt; evaluation unaffected.")
