"""Features served by the n8n + Gemini backend (see n8n/README.md). Same rule: the agent drafts, a human decides."""
import uuid

import pandas as pd
import streamlit as st

from api import is_admin, n8n_call, n8n_configured, require_login
from ui import page_header

require_login()
page_header("HR ops via n8n", "The same HR features, served by the n8n + Gemini workflow backend. Same rule: the agent "
                              "drafts, a person decides.")
if not n8n_configured():
    st.info("Set N8N_BASE_URL and the N8N_*_KEY variables for the dashboard (see .env.example and n8n/README.md).")
    st.stop()

TEAMS = ["engineering", "sales", "media_team", "operations"]
ACTIONS = ["completed_onboarding", "training_completed", "extra_task", "mentoring_session", "peer_review",
           "event_participation"]


def show_security(resp: dict) -> None:
    sec = resp.get("security") or {}
    if sec.get("injection_suspected"):
        st.warning("Possible prompt injection detected: the content was treated as data only.")
    if sec.get("flags"):
        with st.expander("Security flags"):
            st.json(sec["flags"])


copilot, tickets, broadcast, queue, recognition = st.tabs(
    ["Interview co-pilot", "Helpdesk tickets", "Team broadcast", "Approval queue", "Recognition"])

with copilot:
    st.caption("Suggests follow-up questions to the interviewer when an answer is vague or evasive. The candidate never "
               "sees it, it never scores anyone, and it only judges job-related competencies. Tell candidates the "
               "interview is assisted by AI.")
    with st.form("copilot"):
        role = st.text_input("Job role", "Backend Engineer")
        comps = st.text_input("Competencies (comma separated)", "REST API design, PostgreSQL, Caching, Testing")
        resume = st.text_area("Resume summary", "4 years Node.js; built payment APIs; claims Redis caching and PostgreSQL tuning.")
        prev = st.text_area("Previous transcript chunk", "Interviewer: How did you speed up the payment API?")
        cur = st.text_area("Latest transcript chunk", "Candidate: We used a lot of best practices and the team made it faster overall.")
        if st.form_submit_button("Analyse answer"):
            r = n8n_call("POST", "/api/interview/assist", "extension", json={
                "candidate_id": "dash_demo", "job_role": role, "resume_summary": resume, "previous_chunk": prev,
                "current_transcript_chunk": cur, "job_competencies": [c.strip() for c in comps.split(",") if c.strip()]})
            if r:
                d = r["data"]
                if d["weakness_detected"]:
                    st.warning(f"{d['severity'].upper()} · {d['competency']}: {d['detected_gap']}")
                    st.caption(f"Evidence: \"{d['evidence']}\"")
                    for q in d["suggested_probing_questions"]:
                        st.write(f"• {q}")
                else:
                    st.success("No weakness detected in this answer.")
                show_security(r)

with tickets:
    st.caption("What an employee sees in the portal. Identity comes from the employee token, never from the form.")
    with st.form("ticket"):
        emp = st.text_input("Employee ID", "emp_011")
        token = st.text_input("Employee portal token", type="password")
        text = st.text_area("Request", "Hi, I need a certificate of employment for my visa appointment. "
                                       "Also, how many vacation days do I have left?")
        if st.form_submit_button("Submit ticket"):
            r = n8n_call("POST", "/api/hr/tickets", "portal", headers={"X-Employee-Token": token},
                         json={"employee_id": emp, "request_text": text})
            if r:
                st.success(f"{r['data']['ticket_id']}: {r['data']['message']} (category: {r['data']['category']}, "
                           f"status: {r['data']['status']})")
                st.caption("The drafted reply is waiting in the Approval queue tab.")

with broadcast:
    st.caption("Turns rough notes into a message in the team's tone. A code check flags any number or link that "
               "was not in your notes. The person who drafts a broadcast cannot approve it.")
    with st.form("broadcast"):
        team = st.selectbox("Team", TEAMS)
        notes = st.text_area("Your notes", "- office closed fri 7 nov for electrical maintenance\n- everyone works "
                                           "remote that day\n- update VPN client before thu 6 nov")
        if st.form_submit_button("Draft broadcast"):
            r = n8n_call("POST", "/api/hr/broadcast", "hr", json={"target_team": team, "message_prompt": notes})
            if r:
                d = r["data"]
                st.success(f"Draft {d['approval_id']} queued for approval ({d['recipient_count']} recipients)")
                st.text(f"Subject: {d['draft']['subject']}\n\n{d['draft']['body']}")
                checks = d["checks"]
                if not checks["passed"]:
                    st.warning(f"Check failed. Figures not in your notes: {checks['unverified_figures'] or '-'}; "
                               f"links removed: {checks['added_links'] or '-'}")
                if d.get("missing_info"):
                    st.info(f"Missing information: {', '.join(d['missing_info'])}")
                show_security(r)

with queue:
    st.caption("Approving is the only path that sends an email or records points. Each item can be decided once.")
    status = st.selectbox("Status", ["pending_approval", "failed", "sent", "recorded", "rejected"])
    r = n8n_call("GET", "/api/hr/approvals", "hr", params={"status": status, "limit": 50})
    items = r["data"]["items"] if r else []
    if not items:
        st.info("Nothing here.")
    for it in items:
        with st.expander(f"{it['approval_id']} · {it['type']} · {it['target']} · by {it['created_by']}"
                         + (" · ⚠️ review" if it["security"]["injection_suspected"] else "")
                         + (" · stale" if it.get("stale") else "")):
            st.json(it["details"])
            if it.get("checks"):
                st.write(it["checks"])
            draft = it.get("draft") or {}
            subject = st.text_input("Subject", draft.get("subject", ""), key=f"ns{it['approval_id']}")
            body = st.text_area("Body", draft.get("body", ""), key=f"nb{it['approval_id']}", height=180)
            reason = st.text_input("Reason (required to reject)", key=f"nr{it['approval_id']}")
            if status in ("pending_approval", "failed"):
                if not is_admin():
                    st.info("Only an HR admin can decide.")
                    continue
                c1, c2 = st.columns(2)
                if c1.button("Approve and send", key=f"na{it['approval_id']}"):
                    edited = {k: v for k, v in (("subject", subject), ("body", body)) if v != draft.get(k)}
                    payload = {"approval_id": it["approval_id"], "decision": "approve"}
                    if edited:
                        payload["edited_content"] = edited
                    if reason:
                        payload["reason"] = reason
                    if n8n_call("POST", "/api/hr/approvals/decision", "hr_admin", json=payload):
                        st.rerun()
                if c2.button("Reject", key=f"nx{it['approval_id']}"):
                    if n8n_call("POST", "/api/hr/approvals/decision", "hr_admin",
                                json={"approval_id": it["approval_id"], "decision": "reject", "reason": reason}):
                        st.rerun()

with recognition:
    st.caption("Points come from a fixed rule table on the server, never from the client. Each award is idempotent.")
    c1, c2, c3 = st.columns(3)
    period = c1.text_input("Period (YYYY-MM)", "")
    team = c2.selectbox("Team", ["all"] + TEAMS)
    summary = c3.checkbox("AI shoutout", value=False)
    params = {"limit": 10, "include_summary": str(summary).lower()}
    if period:
        params["period"] = period
    if team != "all":
        params["team"] = team
    r = n8n_call("GET", "/api/hr/leaderboard", "hr", params=params)
    if r:
        d = r["data"]
        if d.get("shoutout"):
            st.info(f"**{d['shoutout'].get('headline', '')}** {d['shoutout'].get('summary', '')}")
        if d["leaderboard"]:
            st.dataframe(pd.DataFrame(d["leaderboard"]), hide_index=True, width="stretch")
        else:
            st.caption("No points in this period yet.")
    with st.form("award"):
        emp = st.text_input("Employee ID", "emp_003")
        action = st.selectbox("Action", ACTIONS)
        note = st.text_input("Note")
        if st.form_submit_button("Award points"):
            res = n8n_call("POST", "/api/hr/points/award", "hr", json={
                "employee_id": emp, "action_type": action, "note": note, "source": "hr_dashboard",
                "idempotency_key": f"dash_{uuid.uuid4().hex[:20]}"})
            if res:
                d = res["data"]
                st.success(f"+{d['points_awarded']} points to {d['employee_id']} (period total {d['period_total']})"
                           + (f" · new badges: {', '.join(d['badges_unlocked'])}" if d["badges_unlocked"] else ""))
