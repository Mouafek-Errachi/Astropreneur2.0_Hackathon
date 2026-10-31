import json
import re

import pandas as pd
import streamlit as st

from api import call, is_admin, require_login

require_login()
st.header("Resume screening")

DECISIONS = ["shortlisted", "interview", "rejected"]
SOURCE_BADGE = {"upload": "⬆️ upload", "email": "✉️ email", "linkedin": "💼 linkedin", "drive": "📁 drive",
                "form": "📝 form", "job_board": "🌐 job board", "other": "🔗 other", "demo": "🧪 demo"}
EMAIL_KIND = {"rejected": "rejection", "shortlisted": "invitation", "interview": "invitation"}


# Table callbacks only record what was clicked; run_ops() makes the API calls at the top of the next run,
# so the table is always drawn from fresh data.
def _queue(op: dict) -> None:
    st.session_state.setdefault("ops", []).append(op)


def on_table_edit(key: str, meta: list[dict], picked_key: str) -> None:
    picked = st.session_state.setdefault(picked_key, [])
    for i, change in st.session_state[key]["edited_rows"].items():
        row = meta[int(i)]
        if change.get("Compare") and row["id"] not in picked:
            picked.append(row["id"])
        elif change.get("Compare") is False and row["id"] in picked:
            picked.remove(row["id"])
        new = change.get("Decision")
        if new and new != row["decision"]:
            _queue({"op": "decide", "cid": row["id"], "name": row["name"], "value": new, "blocked": row["decide_block"]})
    # fresh editor: a decision is now a request, a tick is now in `picked`
    st.session_state.editor_rev = st.session_state.get("editor_rev", 0) + 1


def on_row_button(key: str, action: str, meta: list[dict]) -> None:
    if (click := st.session_state.get(key)) is not None:
        row = meta[click["row"]]
        _queue({"op": action, "cid": row["id"], "name": row["name"], "value": row["email_kind"],
                "blocked": row["draft_block"] if action == "draft" else None})


def run_ops() -> None:
    for op in st.session_state.pop("ops", []):
        if op["op"] == "view":
            st.session_state.view_cid = op["cid"]
        elif op["blocked"]:
            st.info(op["blocked"])
        elif op["op"] == "decide":
            if call("POST", f"/candidates/{op['cid']}/decision", json={"decision": op["value"]}):
                st.success(f"Decision '{op['value']}' for {op['name']} sent to the approval queue. "
                           "Someone else must approve it.")
        elif op["op"] == "draft":
            r = call("POST", f"/candidates/{op['cid']}/draft-reply", json={"kind": op["value"]})
            if r:
                st.session_state.last_draft = {"name": op["name"], "kind": op["value"], **r}


def row_meta(c: dict, pending_decision: dict, pending_email: set) -> dict:
    """What the Decision and Email cells show for one candidate, and why an action is refused."""
    name, cid = c["name"] or c["original_filename"], c["id"]
    if cid in pending_decision:
        decision, state = pending_decision[cid], "⏳ waiting for approval"
    elif c["status"] in DECISIONS:
        decision, state = c["status"], "✅ approved"
    else:
        decision, state = None, ""
    kind = EMAIL_KIND.get(c["status"], "acknowledgement")
    if not c["email"]:
        label, block = "— no email", f"No email address was found in {name}'s resume."
    elif cid in pending_email:
        label, block = "📨 in Approvals", f"A draft for {name} is already waiting in Approvals."
    elif cid in pending_decision:
        label, block = "⏳ after approval", f"The decision for {name} is waiting for approval: draft the email once it is approved."
    else:
        label, block = f"✉️ Draft {kind}", None
    decide_block = (f"{name} already has a decision waiting for approval. Approve or reject it in Approvals first."
                    if cid in pending_decision else None)
    return {"id": cid, "name": name, "decision": decision, "state": state, "email_kind": kind, "email_label": label,
            "draft_block": block, "decide_block": decide_block}


def md_escape(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~])", r"\\\1", text)


def compare_table(cands: list[dict], job: dict, metas: dict) -> pd.DataFrame:
    """One column per candidate, one row per criterion of the job (✅/❌), then the facts the score is built from."""
    asked = {s.lower() for s in job["required_skills"] + job["nice_to_have"]}
    years_label = f"Years of experience (asked {job['min_years']:g})" if job["min_years"] else "Years of experience"
    cols = {}
    for c in cands:
        b, p, m = c["score_breakdown"], c["profile"], metas[c["id"]]
        years = b.get("years_experience") or 0
        col = {"Rank": f"#{c['rank']}", "Score": f"{c['score'] or 0:g}"}
        col |= {f"{s} (required)": "✅" if s in b.get("required_matched", []) else "❌" for s in job["required_skills"]}
        col |= {f"{s} (nice to have)": "✅" if s in b.get("nice_matched", []) else "—" for s in job["nice_to_have"]}
        col |= {
            years_label: f"{years:g}" + ((" ✅" if years >= job["min_years"] else " ❌") if job["min_years"] else ""),
            "Current title": p.get("current_title") or "—",
            "Education": p.get("education") or "—",
            "Languages": ", ".join(p.get("languages", [])) or "—",
            "Other skills": ", ".join(s for s in p.get("skills", []) if s.lower() not in asked) or "—",
            "Why": c["justification"],
            "Decision": " ".join(x for x in (m["decision"], m["state"]) if x) or "—",
            "Review": "⚠️ possible prompt injection" if c["needs_review"] else "—",
        }
        cols[f"#{c['rank']} {m['name']}"] = col
    table = pd.DataFrame(cols)
    facts = ["Current title", "Education", "Languages", "Other skills"]
    return table.drop([r for r in facts if (table.loc[r] == "—").all()])  # nothing to compare on


def json_panel(cand: dict, where: str) -> None:
    """Everything the system holds on the candidate, as the API returns it."""
    st.json(cand, expanded=2)
    st.download_button("Download JSON", json.dumps(cand, indent=2, ensure_ascii=False),
                       file_name=f"candidate_{cand['id']}.json", mime="application/json",
                       key=f"json_{where}{cand['id']}")


def criteria_line(required_skills: list, nice_to_have: list, min_years: float) -> str:
    parts = [f"must have **{', '.join(required_skills)}**" if required_skills else "",
             f"nice to have **{', '.join(nice_to_have)}**" if nice_to_have else "",
             f"at least **{min_years:g} years** of experience" if min_years else ""]
    return " · ".join(p for p in parts if p) or "no criteria yet"


def apply_brief(job: dict, text: str, reread: bool, weights: dict) -> None:
    """The AI turns the request into criteria, then the job is re-ranked with them (weights unchanged)."""
    if len(text.strip()) < 3:
        st.warning("Describe the profile first, for example: Python and SQL, Linux is a plus, 3 years minimum.")
        return
    with st.spinner("The AI is reading your request..."):
        r = call("POST", f"/jobs/{job['id']}/criteria/from-text", json={"text": text.strip()})
    if not r:
        return
    crit = r["criteria"]
    if not (crit["required_skills"] or crit["nice_to_have"] or crit["min_years"]):
        st.warning("No skill or years of experience found in your request. Name the skills you need, for example: "
                   "Python and SQL, Linux is a plus.")
        return
    with st.spinner("The AI is re-reading every resume (a free Gemini key takes a few minutes)..." if reread
                    else "Re-ranking..."):
        ranked = call("PUT", f"/jobs/{job['id']}/criteria", params={"reparse": str(reread).lower()}, timeout=1800,
                      json={**crit, "weights": weights})
    if ranked is not None:
        st.session_state.brief_result = {"criteria": crit, "fallback": r["llm_fallback"]}
        st.rerun()


def create_job_form(expanded: bool) -> None:
    with st.expander("Create a job", expanded=expanded):
        with st.form("job"):
            title = st.text_input("Title")
            desc = st.text_area("Description")
            req = st.text_input("Required skills (comma separated)")
            nice = st.text_input("Nice to have (comma separated)")
            years = st.number_input("Minimum years of experience", 0.0, 40.0, 2.0)
            if st.form_submit_button("Create"):
                split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
                r = call("POST", "/jobs", json={"title": title, "description": desc, "required_skills": split(req),
                                                "nice_to_have": split(nice), "min_years": years})
                if r:
                    st.session_state.job_id = r["id"]
                    st.rerun()


def load_demo_button() -> None:
    if is_admin() and st.button("Load demo data (Backend Engineer job, ~60 fictional resumes)"):
        with st.spinner("Loading..."):
            r = call("POST", "/admin/demo-data", json={})
        if r:
            st.session_state.job_id = r["job_id"]
            st.rerun()


jobs = call("GET", "/jobs") or []
if not jobs:
    st.info("No job yet. Create one below" + (", or load the demo data." if is_admin() else "."))
    load_demo_button()
    create_job_form(expanded=True)
    st.stop()

by_id = {j["id"]: j for j in jobs}
ids = list(by_id)
current = st.session_state.get("job_id")
# Select by id and look the job up again: the selectbox would hand back the job as it was when first picked,
# with the criteria of that time.
job = by_id[st.selectbox("Job", ids, index=ids.index(current) if current in ids else len(ids) - 1,
                         format_func=lambda i: f"{by_id[i]['title']} ({by_id[i]['candidate_count']} resumes)")]
st.session_state.job_id = job["id"]
run_ops()

ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
unread = [c for c in ranking if c["score"] is None]
if unread:
    with st.spinner(f"The AI is reading {len(unread)} new resume(s)..."):
        call("POST", f"/jobs/{job['id']}/screen", timeout=1800)
    ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
if fallback := sum(c["llm_fallback"] for c in ranking):
    st.warning(f"{fallback} resume(s) were read by the simple rules because the AI did not answer (check the API key "
               "or the rate limit). Tick 'Re-read the resumes with the AI' below to retry.")

# ------------------------------------------------------------------ criteria
known = {s.lower() for c in ranking for s in c["profile"].get("skills", [])}
current_req = [s.lower() for s in job["required_skills"]]
current_nice = [s.lower() for s in job["nice_to_have"]]
pool = sorted(known | set(current_req) | set(current_nice))
w = job["weights"]

with st.form(f"brief{job['id']}"):
    st.markdown("**Who are you looking for?**")
    text = st.text_area("Who are you looking for?", key=f"brief_text{job['id']}", height=80, max_chars=2000,
                        label_visibility="collapsed",
                        placeholder="e.g. I am looking for people with Python and SQL. Linux is a plus. "
                                    "At least 3 years of experience.")
    go, opt = st.columns([1, 5], vertical_alignment="center")
    reread = opt.checkbox("Also re-read the resumes with the AI (slower, also catches synonyms)",
                          key=f"brief_reread{job['id']}")
    if go.form_submit_button("Rank", type="primary"):
        apply_brief(job, text, reread, w)
if brief := st.session_state.pop("brief_result", None):
    st.success(f"Understood: {criteria_line(**brief['criteria'])}. The table below is re-ranked. Not quite right? "
               "Correct it in 'Edit the criteria by hand'.")
    if brief["fallback"]:
        st.warning("The AI did not answer, so simple rules read your request: check the criteria above.")
else:
    st.caption(f"Ranked on: {criteria_line(current_req, current_nice, job['min_years'])}.")

# Keyed on the saved criteria: when the request box (or an approved weight change) updates them, the form
# starts again from the new values instead of keeping the old ones.
ver = f"{job['id']}_{hash((tuple(current_req), tuple(current_nice), job['min_years'], tuple(w.values())))}"
with st.expander("Edit the criteria by hand"), st.form(f"criteria{job['id']}"):
    left, right = st.columns([3, 2], gap="large")
    with left:
        req = st.multiselect("Required skills", pool, default=current_req, accept_new_options=True, key=f"req{ver}",
                             placeholder="Pick from the skills found in the resumes, or type one")
        nice = st.multiselect("Nice to have", pool, default=current_nice, accept_new_options=True, key=f"nice{ver}",
                              placeholder="Optional")
        years = st.number_input("Minimum years of experience", 0.0, 40.0, float(job["min_years"]), 0.5,
                                key=f"years{ver}")
    with right:
        st.caption("How much each criterion counts in the score")
        wr = st.slider("Required skills", 0, 100, w["required"], 5, key=f"wr{ver}")
        wn = st.slider("Nice to have", 0, 100, w["nice"], 5, key=f"wn{ver}")
        we = st.slider("Experience", 0, 100, w["experience"], 5, key=f"we{ver}")
        reparse = st.checkbox("Re-read the resumes with the AI (slower, also catches synonyms)")
    if st.form_submit_button("Apply and re-rank", type="primary"):
        with st.spinner("The AI is re-reading every resume (a free Gemini key takes a few minutes)..." if reparse
                        else "Re-ranking..."):
            r = call("PUT", f"/jobs/{job['id']}/criteria", params={"reparse": str(reparse).lower()}, timeout=1800,
                     json={"required_skills": req, "nice_to_have": nice, "min_years": years,
                           "weights": {"required": wr, "nice": wn, "experience": we}})
        if r is not None:
            st.rerun()

with st.expander("Learn the weights from past hires"):
    st.caption("CSV with one row per past hire: `skills` (at hiring time), `years_experience` (at hiring time), "
               "`jira_done` (Jira tickets completed), `performance` (review score out of 100), and optionally `group`, used "
               "only for the bias check. Any other column is refused. The rows are not stored; the learned weights "
               "go to the approval queue and someone else approves them.")
    hires = st.file_uploader("Past hires (CSV)", type="csv", key=f"hires{job['id']}")
    if hires and st.button("Learn weights"):
        r = call("POST", f"/jobs/{job['id']}/learn-weights", files={"file": (hires.name, hires.getvalue())})
        if r:
            rep, fair = r["report"], r["report"]["fairness"]
            st.success(f"Proposed in the approval queue (#{r['action_id']}).")
            st.table(pd.DataFrame({"Before": rep["previous_weights"], "Learned": rep["weights"]}))
            rule = rep["outcome_rule"]
            st.write(f"Success = {rule['performance_share']}% performance + {rule['jira_share']}% Jira tickets done "
                     f"(ranked among colleagues). The better half, success {rule['good_if_success_at_least']} or more, "
                     "counts as a good outcome.")
            st.write(f"Learned from {rep['learned_from']} hires. The model predicts the outcome right "
                     f"{rep['accuracy']:.0%} of the time, vs {rep['baseline_accuracy']:.0%} by always guessing "
                     "the most common outcome.")
            if fair["checked"]:
                st.write(f"Bias check passed: impact ratio {fair['impact_ratio']} (four-fifths rule needs 0.8 or more).")
                st.table(pd.DataFrame(fair["rates"]).T)
            else:
                st.warning(f"No bias check. {fair['reason']}.")

# ------------------------------------------------------------------ ranking
if not ranking:
    st.info("No resumes for this job yet. Add some below.")
    load_demo_button()
else:
    f1, f2, f3 = st.columns([2, 2, 1])
    min_score = f1.slider("Show scores from", 0, 100, 0, 5)
    all_required = f2.checkbox("Only candidates with every required skill")
    hide_rejected = f3.checkbox("Hide rejected")
    shown = [c for c in ranking
             if (c["score"] or 0) >= min_score
             and not (all_required and c["score_breakdown"].get("required_missing"))
             and not (hide_rejected and c["status"] == "rejected")]

    st.subheader(f"Ranking: {len(shown)} of {len(ranking)} resumes")
    weights = ranking[0]["score_breakdown"].get("weights") or {}
    if weights:
        names = {"required": "required skills", "nice": "nice-to-have", "experience": "experience"}
        st.caption("Score = " + " + ".join(f"{v}% {names[k]}" for k, v in weights.items())
                   + ". Pick a **Decision** in the table: it goes to the approval queue and someone else approves it. "
                     "**Email** drafts the message that matches the approved decision; it is sent only after approval. "
                     "🔎 shows the details and the full JSON. ⚠️ = possible prompt injection: review before any decision.")

    pending = call("GET", "/approvals", params={"status": "pending"}) or []
    pending_decision = {a["payload"]["candidate_id"]: a["payload"]["decision"] for a in pending
                        if a["type"] == "record_decision"}
    pending_email = {a["payload"].get("candidate_id") for a in pending if a["type"] in ("send_email", "send_reminder")}
    metas = {c["id"]: row_meta(c, pending_decision, pending_email) for c in ranking}
    meta = [metas[c["id"]] for c in shown]

    picked_key = f"picked{job['id']}"
    picked = st.session_state[picked_key] = [cid for cid in st.session_state.get(picked_key, []) if cid in metas]
    b1, b2, hint = st.columns([1, 1, 4], vertical_alignment="center")
    if b1.button(f"Compare ({len(picked)})", type="primary", disabled=len(picked) < 2):
        st.session_state.compare_job = job["id"]
    if b2.button("Clear selection", disabled=not picked):
        st.session_state[picked_key], st.session_state.compare_job = [], None
        st.rerun()
    hint.caption("Tick two or more candidates in the **Compare** column, then click Compare." if len(picked) < 2
                 else "Selected: " + ", ".join(metas[cid]["name"] for cid in picked))

    df = pd.DataFrame([{
        "Compare": c["id"] in picked,
        "Rank": c["rank"],
        "Candidate": m["name"],
        "Source": SOURCE_BADGE.get(c.get("source", "upload"), c.get("source", "upload")),
        "Score": c["score"],
        "Required skills": ", ".join(c["score_breakdown"].get("required_matched", [])),
        "Missing": ", ".join(c["score_breakdown"].get("required_missing", [])),
        "Years": c["score_breakdown"].get("years_experience"),
        "Why": c["justification"],
        "⚠️": "⚠️" if c["needs_review"] else "",
        "Decision": m["decision"],
        "Decision status": m["state"],
        "Email": m["email_label"],
        "Details": "🔎",
    } for c, m in zip(shown, meta)])
    editor_key = f"editor{job['id']}_{st.session_state.get('editor_rev', 0)}"
    email_key, view_key = f"email{job['id']}", f"view{job['id']}"
    st.data_editor(
        df, key=editor_key, hide_index=True, width="stretch", height=min(38 * (len(df) + 1) + 3, 600),
        disabled=[col for col in df.columns if col not in ("Compare", "Decision", "Email", "Details")],
        on_change=on_table_edit, args=(editor_key, meta, picked_key),
        column_config={
            "Compare": st.column_config.CheckboxColumn(
                width="small", pinned=True, help="Tick two or more candidates, then click Compare above the table."),
            "Rank": st.column_config.NumberColumn(width="small", pinned=True),
            "Candidate": st.column_config.TextColumn(pinned=True),
            "Source": st.column_config.TextColumn(width="small", help="Where the CV came from (see the Sources page)."),
            "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", width="small"),
            "Years": st.column_config.NumberColumn(width="small", format="%g"),
            "Why": st.column_config.TextColumn(width="large"),
            "⚠️": st.column_config.TextColumn(width="small"),
            "Decision": st.column_config.SelectboxColumn(
                options=DECISIONS, width="medium",
                help="Proposes a decision. It is recorded only after someone else approves it."),
            "Email": st.column_config.ButtonColumn(
                width="medium", key=email_key, on_click=on_row_button, args=(email_key, "draft", meta),
                help="Drafts the email that matches the approved decision. It goes to the approval queue."),
            "Details": st.column_config.ButtonColumn(
                width="small", type="tertiary", key=view_key, on_click=on_row_button, args=(view_key, "view", meta)),
        })

    if draft := st.session_state.pop("last_draft", None):
        st.success(f"{draft['kind'].capitalize()} email for {draft['name']} drafted and waiting in the approval queue "
                   f"(action #{draft['action_id']}, risk: {draft['risk_level']}). Edit and send it on the Approvals page.")
        st.text(f"Subject: {draft['draft']['subject']}\n\n{draft['draft']['body']}")

    # -------------------------------------------------------------- comparison
    if st.session_state.get("compare_job") == job["id"] and len(picked) >= 2:
        cands = [c for c in ranking if c["id"] in picked]  # in ranking order
        st.divider()
        head, close = st.columns([6, 1])
        head.subheader(f"Comparing {len(cands)} candidates")
        if close.button("Close", key="close_compare"):
            st.session_state.compare_job = None
            st.rerun()
        # Asked once per selection and scores: Streamlit reruns the page on every click.
        advice_key = ("advice", job["id"], tuple((c["id"], c["score"]) for c in cands))
        if not st.session_state.get(advice_key):  # a failed call (None) is retried on the next run
            with st.spinner("Writing the recommendation..."):
                st.session_state[advice_key] = call("POST", f"/jobs/{job['id']}/compare",
                                                    json={"candidate_ids": [c["id"] for c in cands]})
        if advice := st.session_state[advice_key]:
            # The sentences can quote resume text: escaped so they cannot render links, images or formatting.
            st.markdown(f"#### {md_escape(advice['recommendation'])}")
            for alt in advice["alternatives"]:
                st.markdown(f"- {md_escape(alt['sentence'])}")
            if advice["needs_review"]:
                st.warning(f"Review before deciding: possible prompt injection in {', '.join(advice['needs_review'])}.")
            st.caption("The recommended candidate is the highest score, chosen by the code; the AI only writes the "
                       "sentences" + (" (the AI did not answer, so they were written by simple rules)."
                                      if advice["llm_fallback"] else "."))
        with st.expander("Side-by-side details"):
            table = compare_table(cands, job, metas)
            st.dataframe(table, width="stretch", height=35 * (len(table) + 1) + 3)
            for tab, c in zip(st.tabs([f"#{c['rank']} {metas[c['id']]['name']}" for c in cands]), cands):
                with tab:
                    json_panel(c, "cmp")

    # -------------------------------------------------------------- candidate details (🔎)
    cand = next((c for c in ranking if c["id"] == st.session_state.get("view_cid")), None)
    if cand:
        st.divider()
        head, close = st.columns([6, 1])
        head.subheader(f"#{cand['rank']} {cand['name'] or cand['original_filename']}")
        if close.button("Close", key="close_details"):
            st.session_state.view_cid = None
            st.rerun()
        st.markdown(f"**Why this rank:** {cand['justification']}")
        b = cand["score_breakdown"]
        st.write(f"Required matched: {', '.join(b.get('required_matched', [])) or '-'}")
        st.write(f"Required missing: {', '.join(b.get('required_missing', [])) or '-'}")
        st.write(f"Nice to have: {', '.join(b.get('nice_matched', [])) or '-'}")
        st.write(f"Email: {cand['email'] or 'not found in the resume'}")
        if cand["injection_flags"]:
            st.warning("Possible prompt injection in this resume. Review it before any decision.")
            st.json(cand["injection_flags"])
        st.markdown("**Full record (JSON)**")
        json_panel(cand, "view")

# ------------------------------------------------------------------ add resumes / jobs
st.divider()
last_upload = st.session_state.pop("last_upload", None)
with st.expander("Add resumes to this job", expanded=bool(last_upload) or not ranking):
    if last_upload:
        ok = sum(r["accepted"] for r in last_upload)
        if ok:
            st.success(f"{ok} resume(s) added and ranked above.")
        refused = [r for r in last_upload if not r["accepted"]]
        if refused:
            st.warning("Refused: " + "; ".join(f"{r['filename']} ({r['error']})" for r in refused))
    files = st.file_uploader("PDF, Word, OpenDocument, RTF, HTML, text, JSON or a photo (JPG, PNG, WebP)",
                             accept_multiple_files=True, key=f"files{job['id']}")
    if files and st.button("Upload"):
        res = call("POST", f"/jobs/{job['id']}/resumes", files=[("files", (f.name, f.getvalue())) for f in files])
        if res:
            st.session_state.last_upload = res
            st.rerun()
create_job_form(expanded=False)
