"""Project Staffing & Gap Analysis: what each project needs, what the team covers, and job descriptions for the
true gaps. The AI extracts and drafts; people confirm the requirements, choose internal or external, and approve."""
import pandas as pd
import streamlit as st

from api import call, require_login
from ui import esc, page_header

require_login()
me = st.session_state.get("me") or call("GET", "/auth/me")
scopes = set(me["scopes"])
if "projects:read" in scopes:
    page_header("Project staffing", "What each project needs, what the team covers, and a job description for each true "
                                    "gap. The AI extracts and drafts; people confirm, decide and approve.")
else:
    page_header("My skills", "Declare your skills and level. They count for project staffing once a manager validates them.")

LEVELS = {1: "1 beginner", 2: "2 working", 3: "3 senior", 4: "4 expert"}
KIND = {"missing": "🔴 missing", "insufficient_level": "🟠 level too low", "insufficient_capacity": "🟡 not enough capacity"}


def my_skills() -> None:
    data = call("GET", "/employees/me/skills")
    if not data:
        return
    st.subheader(f"My skills ({esc(data['name'])})")
    st.caption("Skills you declare count for project staffing only after a manager validates them.")
    if data["skills"]:
        st.dataframe([{"skill": s["skill"], "level": LEVELS[s["level"]],
                       "status": "✅ validated" if s["validated"] else "⏳ waiting for a manager"} for s in data["skills"]],
                     hide_index=True, width="stretch")
    with st.form("my_skill"):
        c1, c2 = st.columns([3, 2])
        skill = c1.text_input("Skill", placeholder="e.g. Kubernetes")
        level = c2.selectbox("Level", list(LEVELS), format_func=LEVELS.get, index=1)
        if st.form_submit_button("Add or update") and skill.strip():
            if call("PUT", "/employees/me/skills", json={"skills": [{"skill": skill.strip(), "level": level}]}):
                st.rerun()


if "projects:read" not in scopes:  # employees: only their own skill profile
    if me.get("employee_id"):
        my_skills()
    else:
        st.info("Your role does not have access to this page.")
    st.stop()

can_write = "projects:write" in scopes
projects = call("GET", "/projects") or []

with st.expander("New project", expanded=not projects, icon=":material/add:"):
    if not can_write:
        st.caption("Read-only role.")
    else:
        with st.form("new_project"):
            name = st.text_input("Project name")
            summary = st.text_area("Summary", help="Describe the skills the project needs (e.g. 'a senior Python developer "
                                                "full time, React half time'). The AI reads it even without documents.")
            c1, c2 = st.columns(2)
            team = c1.selectbox("Team", [None] + [t["id"] for t in call("GET", "/hr/teams") or []])
            deadline = c2.text_input("Deadline (YYYY-MM-DD)")
            if st.form_submit_button("Create") and name.strip():
                r = call("POST", "/projects", json={"name": name, "summary": summary, "team_id": team, "deadline": deadline})
                if r:
                    st.session_state.project_id = r["id"]
                    st.rerun()

if not projects:
    st.info("No project yet. Create one above, or log in as admin and click 'Load demo data' on the home page.")
    st.stop()

tab_projects, tab_matrix = st.tabs(["Projects", "Team skill matrix"])

with tab_projects:
    by_id = {p["id"]: p for p in projects}
    ids = list(by_id)
    cur = st.session_state.get("project_id")
    pid = st.selectbox("Project", ids, index=ids.index(cur) if cur in ids else 0,
                       format_func=lambda i: f"{by_id[i]['name']}  ·  "
                                             + (f"{by_id[i]['gap_count']} gaps ({by_id[i]['gap_fte']:g} FTE)"
                                                if by_id[i]["requirements_confirmed"] else "requirements to confirm"))
    st.session_state.project_id = pid
    p = call("GET", f"/projects/{pid}")
    st.caption(f"{esc(p['summary'])}  ·  team: {p['team_id'] or '—'}  ·  deadline: {p['deadline'] or '—'}  ·  "
               f"already assigned: {p['assigned_fte']:g} FTE")

    # ---------------------------------------------------------------- description (analysed like a document)
    if can_write:
        with st.expander("Project description", expanded=not p["documents"] and not p["requirements"]):
            with st.form(f"desc{pid}"):
                new_summary = st.text_area("Summary", value=p["summary"], height=150,
                                           help="The AI extracts the required skills from this text and from the "
                                                "documents. Skills found in documents take precedence.")
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("💾 Save and analyse")
                again = c2.form_submit_button("🔄 Analyse again")
            if save and call("PATCH", f"/projects/{pid}", json={"summary": new_summary}):
                st.rerun()
            if again and call("POST", f"/projects/{pid}/analyze"):
                st.rerun()

    # ---------------------------------------------------------------- documents
    with st.expander(f"Documents ({len(p['documents'])})", expanded=not p["documents"]):
        for d in p["documents"]:
            icon = {"none": "📄", "low": "📄", "medium": "⚠️", "high": "🛡️"}[d["risk_level"]]
            st.write(f"{icon} **{esc(d['filename'])}** ({d['kind']}, risk {d['risk_level']})")
            if d["flags"]:
                st.warning("Injection guard: " + "; ".join(f"{f['type']} ({f['severity']})" for f in d["flags"][:5])
                           + ". The AI only extracts skills; the gaps are computed in code, so this text cannot hide a gap.")
            if d["hidden_text"]:
                st.error(f"Hidden text removed before the AI saw it: “{esc(d['hidden_text'])}”")
        if can_write:
            files = st.file_uploader("Charter (PDF, DOCX, TXT/MD) and manifests (requirements.txt, package.json, "
                                     "pom.xml, go.mod, Dockerfile, docker-compose.yml)", accept_multiple_files=True,
                                     key=f"docs{pid}")
            if files and st.button("Upload and analyse"):
                r = call("POST", f"/projects/{pid}/documents", files=[("files", (f.name, f.getvalue())) for f in files])
                if r:
                    for x in r["documents"]:
                        if not x["accepted"]:
                            st.warning(f"{esc(x['filename'])}: {esc(x['error'])}")
                    st.rerun()

    # ---------------------------------------------------------------- requirements (human confirms)
    st.subheader("Required skills")
    if p["requirements_confirmed"]:
        st.caption(f"Version {p['requirements_version']}, confirmed by {p['confirmed_by']}. Edit and confirm again to "
                   "recompute the gaps.")
    elif p["requirements"]:
        st.warning(f"Draft version {p['requirements_version']} extracted by the AI from the project description and "
                   "documents. Check it: it is not used until a person confirms it.")
    else:
        st.info("No skills found yet. Describe them in the project description, upload a charter, or add rows below.")
    req_df = pd.DataFrame(p["requirements"] or [], columns=["skill", "level", "headcount", "allocation_pct", "evidence", "source"])
    edited = st.data_editor(
        req_df, key=f"req{pid}_{p['requirements_version']}", num_rows="dynamic" if can_write else "fixed",
        disabled=not can_write or ["evidence", "source"], hide_index=True, width="stretch",
        column_config={
            "skill": st.column_config.TextColumn(required=True),
            "level": st.column_config.SelectboxColumn(options=[1, 2, 3, 4], help="1 beginner, 2 working, 3 senior, 4 expert"),
            "headcount": st.column_config.NumberColumn(min_value=1, max_value=20, step=1),
            "allocation_pct": st.column_config.NumberColumn("time %", min_value=10, max_value=100, step=10),
            "evidence": st.column_config.TextColumn(width="large"),
        })
    if can_write and st.button("✅ Confirm requirements and compute gaps", type="primary"):
        rows = [r for r in edited.to_dict("records") if str(r.get("skill") or "").strip()]
        body = {"skills": [{"skill": str(r["skill"]).strip(), "level": int(r["level"] or 2), "headcount": int(r["headcount"] or 1),
                            "allocation_pct": int(r["allocation_pct"] or 100), "evidence": str(r.get("evidence") or "")[:200]}
                           for r in rows]}
        if call("PUT", f"/projects/{pid}/requirements", json=body):
            st.rerun()

    # ---------------------------------------------------------------- gaps
    if p["requirements_confirmed"]:
        st.subheader("Skill gaps")
        if not p["gaps"]:
            st.success("The team covers every required skill at the level and capacity asked.")
        else:
            done = sum(g["decision"] != "open" for g in p["gaps"])
            st.caption(f"{done} of {len(p['gaps'])} gaps decided. Decide each gap: cover it internally (pick how) or "
                       "hire externally (a job description goes to Approvals).")
        for g in p["gaps"]:
            state = ("✅ covered internally · " if g["decision"] == "internal" else
                     "🧾 hiring · " if g["status"] == "hiring" else
                     "⏳ job description in Approvals · " if g["status"] == "jd_proposed" else
                     "➡️ hire externally · " if g["decision"] == "hire_externally" else "❔ undecided · ")
            head = (f"{state}{KIND[g['kind']]} **{esc(g['skill'])}** (level {g['level']}): {g['gap_fte']:g} FTE missing "
                    f"({g['covered_fte']:g} of {g['needed_fte']:g} covered)")
            with st.expander(head, expanded=g["decision"] == "open" or (g["decision"] == "hire_externally" and g["status"] == "open")):
                labels = [f"{o['kind']}: {o['employee_id'] + ' - ' if 'employee_id' in o else ''}{o['reason']}"
                          for o in g["internal_options"]]
                if labels:
                    st.markdown("**Internal options first:**")
                    for label in labels:
                        st.markdown(f"- {esc(label)}")
                    if "skills:read" not in scopes:
                        st.caption("Names are visible to managers only.")
                else:
                    st.caption("No internal option: nobody has this skill or a related one.")

                if g["decision"] == "internal":
                    st.success(f"Covered internally: {esc(g['internal_plan'])}  (decided by {g['decided_by']})")
                elif g["status"] == "hiring":
                    st.success(f"Job offer created: rank applicants on the Screening page ({g['job_offer_id']}).")
                elif g["status"] == "jd_proposed":
                    st.info("Job description waiting in Approvals: someone other than you must approve it.")
                elif g["decision"] == "hire_externally":
                    st.info(f"Decided by {g['decided_by']}: hire externally. Draft the job description.")

                if not can_write or g["status"] in ("jd_proposed", "hiring"):
                    pass
                elif g["decision"] == "open":
                    with st.form(f"internal{g['id']}"):
                        st.markdown("**Cover internally**")
                        pick = st.selectbox("How", ["(write my own plan)"] + labels, key=f"pick{g['id']}")
                        own = st.text_input("Plan", key=f"own{g['id']}", placeholder="e.g. emp_002 takes the FastAPI "
                                            "training in November, emp_001 mentors")
                        if st.form_submit_button("✅ Confirm: cover internally", type="primary"):
                            plan = own.strip() or ("" if pick.startswith("(") else pick)
                            if not plan:
                                st.warning("Pick an internal option or write a plan first.")
                            elif call("PUT", f"/projects/{pid}/gaps/{g['id']}", json={"decision": "internal", "plan": plan}):
                                st.rerun()
                    if st.button("➡️ Hire externally instead", key=f"ext{g['id']}"):
                        if call("PUT", f"/projects/{pid}/gaps/{g['id']}", json={"decision": "hire_externally"}):
                            st.rerun()
                else:
                    c1, c2 = st.columns(2)
                    if g["decision"] == "hire_externally" and c1.button("📝 Draft job description", key=f"jd{g['id']}",
                                                                       type="primary"):
                        r = call("POST", f"/projects/{pid}/gaps/{g['id']}/job-description")
                        if r:
                            st.session_state[f"jd_{g['id']}"] = r
                            st.rerun()
                    if c2.button("↩️ Undo decision (back to undecided)", key=f"undo{g['id']}"):
                        if call("PUT", f"/projects/{pid}/gaps/{g['id']}", json={"decision": "open"}):
                            st.rerun()
                if jd := st.session_state.get(f"jd_{g['id']}"):
                    st.markdown(f"**{esc(jd['payload']['title'])}** (risk: {jd['risk_level']})")
                    st.text(jd["payload"]["body"])
                    if jd["flags"]:
                        st.warning("Checks: " + "; ".join(f"{f['type']} ({f['severity']})" for f in jd["flags"]))

with tab_matrix:
    team = st.selectbox("Team", [None] + [t["id"] for t in call("GET", "/hr/teams") or []],
                        format_func=lambda t: t or "all teams")
    m = call("GET", "/skills/matrix", params={"team": team} if team else {})
    if m and m["skills"]:
        df = pd.DataFrame([{k: r[k] for k in ("skill", "level_1", "level_2", "level_3", "level_4", "unvalidated")}
                           for r in m["skills"]]).set_index("skill")
        st.caption("Validated people per skill and level. Self-declared skills are counted apart and do not cover gaps.")
        top = int(df[["level_1", "level_2", "level_3", "level_4"]].to_numpy().max() or 1)
        bar = lambda label: st.column_config.ProgressColumn(label, min_value=0, max_value=top, format="%d")  # noqa: E731
        st.dataframe(df, width="stretch", column_config={
            "level_1": bar("beginner"), "level_2": bar("working"), "level_3": bar("senior"), "level_4": bar("expert"),
            "unvalidated": st.column_config.NumberColumn("self-declared", format="%d")})
        if m["individual"]:
            with st.expander("Who has what (managers only)"):
                st.dataframe([{"skill": r["skill"], **x} for r in m["skills"] for x in r["people"]], hide_index=True,
                             width="stretch")
        else:
            st.caption("Individual levels are visible to managers only.")
    else:
        st.info("No skills recorded yet.")
    if me.get("employee_id"):
        my_skills()
