"""Resume screening (Ghassen's page, on the 5-layer API): plain-language criteria, editable weights, decisions and
emails from the table, compare, learned weights, plus the bias check, hidden-text warnings and candidate chat."""
import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from api import call, can, is_admin, require_login
from ui import empty, esc, page_header

require_login()
page_header("Screening", "Describe who you are looking for: the AI turns it into criteria and ranks every resume. "
                         "Decisions go to Approvals, and someone else approves them.")

DECISIONS = ["shortlisted", "interview", "rejected"]
SOURCE_BADGE = {"upload": "⬆️ upload", "email": "✉️ email", "linkedin": "💼 linkedin", "drive": "📁 drive",
                "form": "📝 form", "job_board": "🌐 job board", "other": "🔗 other", "demo": "🧪 demo"}
# An approved "interview" decision gets an invitation with free slots from the HR calendar (see invite_dialog).
EMAIL_KIND = {"rejected": "rejection", "interview": "invitation"}


def risky(c: dict) -> bool:
    return c["risk_level"] in ("medium", "high")


def why(c: dict) -> str:
    return c["score_breakdown"].get("justification", "")


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
            _queue({"op": "decide", "aid": row["id"], "name": row["name"], "value": new, "blocked": row["decide_block"]})
    st.session_state.editor_rev = st.session_state.get("editor_rev", 0) + 1


def on_row_button(key: str, action: str, meta: list[dict]) -> None:
    if (click := st.session_state.get(key)) is not None:
        row = meta[click["row"]]
        _queue({"op": action, "aid": row["id"], "name": row["name"], "value": row["email_kind"],
                "blocked": row["draft_block"] if action == "draft" else None})


def run_ops() -> None:
    for op in st.session_state.pop("ops", []):
        if op["op"] == "view":
            st.session_state.view_aid = op["aid"]
        elif op["blocked"]:  # toasts: the user is down in the table, where a message at the top would go unseen
            st.toast(esc(op["blocked"]), icon=":material/info:")
        elif op["op"] == "decide":
            if call("POST", f"/applications/{op['aid']}/decision", json={"decision": op["value"]}):
                st.toast(f"Decision '{op['value']}' for {esc(op['name'])} sent to the approval queue. "
                         "Someone else must approve it.", icon=":material/fact_check:")
        elif op["op"] == "draft" and op["value"] == "invitation":
            st.session_state.invite = {"aid": op["aid"], "name": op["name"]}
        elif op["op"] == "draft":
            r = call("POST", f"/applications/{op['aid']}/email", json={"kind": op["value"]})
            if r:
                st.session_state.last_draft = {"name": op["name"], "kind": op["value"], **r}


def _close_invite() -> None:
    st.session_state.pop("invite", None)


@st.dialog("Invite to interview", width="medium", on_dismiss=_close_invite)
def invite_dialog() -> None:
    """Free slots from the interviewer's calendar, then an invitation that waits in Approvals: there the reviewer books
    one slot or lets the candidate pick one with a single-use link, and approving sends the email."""
    inv = st.session_state.invite
    st.markdown(f"**{esc(inv['name'])}** · the agent looks for free slots in the interviewer's calendar and drafts "
                "the invitation. Nothing is sent until someone approves it.")
    if not inv.get("done"):
        form = st.empty()  # replaced by the result once the invitation is drafted
        with form.container():
            who = st.selectbox("Interviewer", call("GET", "/scheduling/interviewers") or [])
            duration = st.select_slider("Duration", [30, 45, 60, 90], value=45, format_func=lambda m: f"{m} min")
            if st.button("Find free slots and draft the invitation", type="primary", icon=":material/calendar_month:",
                         disabled=not (who and can("scheduling:manage")), width="stretch"):
                inv["done"] = call("POST", "/scheduling/propose", json={
                    "application_id": inv["aid"], "interviewer": who, "duration_minutes": duration})
        if not inv.get("done"):
            return
        form.empty()
    st.success("Invitation drafted with these free slots. In Approvals, book one or let the candidate choose; "
               "approving sends the email.", icon=":material/event_available:")
    st.markdown("\n".join(f"- {_slot_label(s)}" for s in inv["done"]["slots"]))
    st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")


def _slot_label(s: dict) -> str:
    start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
    return f"{start:%A %d %B, %H:%M}–{end:%H:%M}"


def row_meta(c: dict, pending_decision: dict, pending_email: set) -> dict:
    """What the Decision and Email cells show for one candidate, and why an action is refused."""
    name, aid = c["name"] or c["filename"], c["application_id"]
    if aid in pending_decision:
        decision, state = pending_decision[aid], "⏳ waiting for approval"
    elif c["status"] in DECISIONS:
        decision, state = c["status"], "✅ approved"
    else:
        decision, state = None, ""
    kind = EMAIL_KIND.get(c["status"], "acknowledgement")
    if not c["email"]:
        label, block = "— no email", f"No email address was found in {name}'s resume."
    elif c["candidate_id"] in pending_email:
        label, block = "📨 in Approvals", f"A draft for {name} is already waiting in Approvals."
    elif aid in pending_decision:
        label, block = "⏳ after approval", f"The decision for {name} is waiting for approval: draft the email once it is approved."
    elif c["status"] == "interview":
        label, block = "📅 Invite to interview", None
    elif c["status"] == "shortlisted":
        label, block = "— shortlisted", f"{name} is shortlisted. Set the decision to interview to invite them."
    else:
        label, block = f"✉️ Draft {kind}", None
    decide_block = (f"{name} already has a decision waiting for approval. Approve or reject it in Approvals first."
                    if aid in pending_decision else None)
    return {"id": aid, "name": name, "decision": decision, "state": state, "email_kind": kind, "email_label": label,
            "draft_block": block, "decide_block": decide_block}


def compare_table(cands: list[dict], crit: dict, metas: dict) -> pd.DataFrame:
    """One column per candidate, one row per criterion of the job (✅/❌), then the facts the score is built from."""
    asked = {s.lower() for s in crit["required_skills"] + crit["nice_to_have"]}
    years_label = f"Years of experience (asked {crit['min_years']:g})" if crit["min_years"] else "Years of experience"
    cols = {}
    for c in cands:
        b, p, m = c["score_breakdown"], c["profile"], metas[c["application_id"]]
        years = b.get("years_experience") or 0
        col = {"Rank": f"#{c['rank']}", "Score": f"{c['score'] or 0:g}"}
        col |= {f"{s} (required)": "✅" if s in b.get("required_matched", []) else "❌" for s in crit["required_skills"]}
        col |= {f"{s} (nice to have)": "✅" if s in b.get("nice_matched", []) else "—" for s in crit["nice_to_have"]}
        col |= {
            years_label: f"{years:g}" + ((" ✅" if years >= crit["min_years"] else " ❌") if crit["min_years"] else ""),
            "Current title": p.get("current_title") or "—",
            "Education": p.get("education") or "—",
            "Languages": ", ".join(p.get("languages", [])) or "—",
            "Other skills": ", ".join(s for s in p.get("skills", []) if s.lower() not in asked) or "—",
            "Why": why(c),
            "Bias check Δ": f"{(c['fairness'] or {}).get('rank_delta', 0):+g}",
            "Decision": " ".join(x for x in (m["decision"], m["state"]) if x) or "—",
            "Review": "⚠️ possible prompt injection" if risky(c) else "—",
        }
        cols[f"#{c['rank']} {m['name']}"] = col
    table = pd.DataFrame(cols)
    facts = ["Current title", "Education", "Languages", "Other skills"]
    return table.drop([r for r in facts if (table.loc[r] == "—").all()])  # nothing to compare on


def json_panel(cand: dict, where: str) -> None:
    """Everything the system holds on the candidate, as the API returns it."""
    st.json(cand, expanded=2)
    st.download_button("Download JSON", json.dumps(cand, indent=2, ensure_ascii=False),
                       file_name=f"candidate_{cand['candidate_id']}.json", mime="application/json",
                       key=f"json_{where}{cand['application_id']}")


def criteria_line(required_skills: list, nice_to_have: list, min_years: float, **_) -> str:
    parts = [f"must have **{', '.join(map(esc, required_skills))}**" if required_skills else "",
             f"nice to have **{', '.join(map(esc, nice_to_have))}**" if nice_to_have else "",
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
    with st.spinner("The AI is re-reading every resume..." if reread else "Re-ranking..."):
        ranked = call("PUT", f"/jobs/{job['id']}/criteria", params={"reparse": str(reread).lower()}, timeout=1800,
                      json={**crit, "weights": weights})
    if ranked is not None:
        st.session_state.brief_result = {"criteria": crit, "fallback": r["llm_fallback"]}
        st.rerun()


def create_job_form() -> None:
    with st.form("job", border=False):
        title = st.text_input("Title", placeholder="e.g. Backend Engineer")
        desc = st.text_area("Description", placeholder="What the person will do")
        req = st.text_input("Required skills (comma separated)", placeholder="e.g. python, sql")
        nice = st.text_input("Nice to have (comma separated)", placeholder="e.g. docker, aws")
        years = st.number_input("Minimum years of experience", 0.0, 40.0, 2.0)
        if st.form_submit_button("Create the job", type="primary", icon=":material/add:"):
            if not title.strip():
                st.warning("Give the job a title.")
                return
            split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
            r = call("POST", "/jobs", json={"title": title, "description": desc, "required_skills": split(req),
                                            "nice_to_have": split(nice), "min_years": years})
            if r:
                st.session_state.job_id = r["id"]
                st.rerun()


def load_demo_button() -> None:
    """The demo resumes go to the 5 demo jobs (not to the job on screen): say so, and open the first demo job."""
    if is_admin() and st.button("Load demo data into the 5 demo jobs (100+ fictional resumes, 43 emails)"):
        with st.spinner("Loading..."):
            r = call("POST", "/admin/demo/load", params={"bulk": "true"})
        if r:
            added: dict[str, int] = {}
            for x in r["resumes"]:
                if x["accepted"]:
                    added[x["job"]] = added.get(x["job"], 0) + 1
            st.session_state.demo_result = added or None
            target = next((j for j in call("GET", "/jobs") or [] if j["title"] == "Backend Engineer"), None)
            if target:
                st.session_state.job_id = target["id"]
            st.rerun()


SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "employees"
SAMPLES = {"past_hires_backend.csv": "Fair history: the bias check passes",
           "past_hires_biased.csv": "Biased history: the bias check blocks it"}
WEIGHT_NAMES = {"required": "Required skills", "nice": "Nice to have", "experience": "Experience"}
HIRE_COLUMNS = [
    ("skills", "Skills the person had when hired, separated by ; (e.g. python; sql; docker)"),
    ("years_experience", "Years of experience when hired"),
    ("jira_done", "Jira tickets completed since"),
    ("performance", "Last performance review, out of 100"),
    ("group", "Optional, for the bias check only (e.g. A / B): never used to score anyone"),
]


def learning_report(rep: dict) -> None:
    """What the learner found: how good the prediction is, the old and new weights, and the bias check by group."""
    fair = rep["fairness"]
    c = st.columns(3)
    c[0].metric("Learned from", f"{rep['learned_from']} hires", border=True,
                help=f"{rep['good_outcomes']} of them count as a good outcome (the better half).")
    c[1].metric("Predicts who did well", f"{rep['accuracy']:.0%}", border=True,
                delta=f"{100 * (rep['accuracy'] - rep['baseline_accuracy']):+.0f} pts vs guessing",
                help=f"Always guessing the most common outcome would be right {rep['baseline_accuracy']:.0%} of the time.")
    c[2].metric("Bias check", ("Passed" if fair["passed"] else "Failed") if fair["checked"] else "Not run", border=True,
                help="Four-fifths rule: no group may be shortlisted at under 80% of the best group's rate.")
    rows = [{"Criterion": WEIGHT_NAMES.get(k, k), "Now": rep["previous_weights"][k], "Learned": rep["weights"][k],
             "Change": f"{rep['weights'][k] - rep['previous_weights'][k]:+d}"} for k in WEIGHT_NAMES]
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        "Now": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
        "Learned": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%")})
    rule = rep["outcome_rule"]
    st.caption(f"A hire did well when their success ({rule['performance_share']}% performance review + "
               f"{rule['jira_share']}% Jira tickets, ranked among colleagues) was at least "
               f"{rule['good_if_success_at_least']} out of 100.")
    if fair["checked"]:
        st.markdown("**Shortlisted (score 70 or more), by group**")
        st.dataframe([{"Group": g, "People": v["n"], "With the current weights": f"{v['before']:.0%}",
                       "With the learned weights": f"{v['after']:.0%}"} for g, v in fair["rates"].items()],
                     hide_index=True, width="stretch")
        st.caption(f"Lowest group rate ÷ highest: {fair['impact_ratio_before']} now → {fair['impact_ratio']} learned "
                   "(must stay at 0.8 or more).")
    else:
        st.warning(f"No bias check. {fair['reason']}.", icon=":material/gpp_maybe:")


def learn_weights(job: dict) -> None:
    st.markdown("**Let your past hires set the weights.** Upload one row per person you hired for this kind of job: "
                "the system finds which criteria predicted who did well, and proposes new weights. Someone else "
                "approves them before the ranking changes. The rows are not stored.")
    st.caption("Current weights: " + " · ".join(f"{WEIGHT_NAMES[k]} {v}%" for k, v in job["criteria"]["weights"].items()
                                                 if k in WEIGHT_NAMES))
    with st.expander("What the file must contain, and sample files", icon=":material/table:"):
        st.markdown("\n".join(f"- `{c}`: {d}" for c, d in HIRE_COLUMNS)
                    + "\n\nAny other column (name, age, gender…) is refused, so no personal data reaches the model. "
                      "At least 30 rows.")
        cols = st.columns(len(SAMPLES))
        for col, (name, what) in zip(cols, SAMPLES.items()):
            if (SAMPLE_DIR / name).exists():
                col.download_button(name, (SAMPLE_DIR / name).read_bytes(), file_name=name, mime="text/csv",
                                    icon=":material/download:", help=what, width="stretch")
                col.caption(what)
    hires = st.file_uploader("Past hires (CSV)", type="csv", key=f"hires{job['id']}")
    if hires:
        try:
            preview = pd.read_csv(io.BytesIO(hires.getvalue()))
            st.caption(f"{len(preview)} hires · columns: {', '.join(map(str, preview.columns))}")
            st.dataframe(preview.head(5), hide_index=True, width="stretch")
        except (ValueError, UnicodeDecodeError):
            st.warning("This file could not be read as a CSV.")
    result_key = f"learned{job['id']}"
    if st.button("Learn the weights", type="primary", icon=":material/model_training:",
                 disabled=not (hires and can("jobs:write"))):
        with st.spinner("Learning from your past hires..."):
            st.session_state[result_key] = call("POST", f"/jobs/{job['id']}/learn-weights", refused="return",
                                                files={"file": (hires.name, hires.getvalue())})
    if not (r := st.session_state.get(result_key)):
        return
    if "refused" in r:
        st.error(esc(r["refused"]), icon=":material/block:")
        if isinstance(r["detail"], dict) and r["detail"].get("report"):
            learning_report(r["detail"]["report"])
        return
    st.success("New weights proposed. They wait in Approvals: someone else must approve them before the ranking "
               "changes.", icon=":material/fact_check:")
    st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")
    learning_report(r["report"])


if "demo_result" in st.session_state:
    if added := st.session_state.pop("demo_result"):
        st.success("Demo data loaded: " + ", ".join(f"{n} resumes into {t}" for t, n in added.items())
                   + ". Showing Backend Engineer; pick another demo job in the list above the table.")
    else:
        st.info("The demo data was already loaded: every demo resume is already in its demo job (duplicates are "
                "skipped). Pick Backend Engineer, Data Engineer, DevOps Engineer, Frontend Developer or HR Generalist.")


jobs = call("GET", "/jobs") or []
if not jobs:
    empty("No job yet.", "Create one below" + (", or load the demo data." if is_admin() else "."))
    load_demo_button()
    if can("jobs:write"):
        with st.container(border=True):
            st.subheader("Create a job")
            create_job_form()
    st.stop()

by_id = {j["id"]: j for j in jobs}
ids = list(by_id)
current = st.session_state.get("job_id")
pick, new = st.columns([5, 1], vertical_alignment="bottom")
# Select by id and look the job up again: the selectbox would hand back the job as it was when first picked.
job = by_id[pick.selectbox("Job", ids, index=ids.index(current) if current in ids else 0,
                           format_func=lambda i: f"{by_id[i]['title']} ({by_id[i]['applications']} resumes)")]
if can("jobs:write"):
    with new.popover("New job", icon=":material/add:", width="stretch"):
        create_job_form()
st.session_state.job_id = job["id"]
crit = job["criteria"]
run_ops()
if "invite" in st.session_state:
    invite_dialog()

ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
if unread := [c for c in ranking if c["score"] is None]:
    with st.spinner(f"The AI is reading {len(unread)} new resume(s)..."):
        call("POST", f"/jobs/{job['id']}/screen", timeout=1800)
    ranking = call("GET", f"/jobs/{job['id']}/ranking") or []
if fallback := sum(c["llm_fallback"] for c in ranking):
    st.warning(f"{fallback} resume(s) were read by the simple rules because the AI did not answer (check the API key "
               "or the rate limit). Tick 'Re-read the resumes with the AI' below to retry.")

# ------------------------------------------------------------------ criteria
known = {s.lower() for c in ranking for s in c["profile"].get("skills", [])}
current_req = [s.lower() for s in crit["required_skills"]]
current_nice = [s.lower() for s in crit["nice_to_have"]]
pool = sorted(known | set(current_req) | set(current_nice))
w = crit["weights"]

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
    st.caption(f"Ranked on: {criteria_line(current_req, current_nice, crit['min_years'])}.")

# Keyed on the saved criteria: when they change, the form starts again from the new values.
ver = f"{job['id']}_{hash((tuple(current_req), tuple(current_nice), crit['min_years'], tuple(w.values())))}"
with st.expander("Edit the criteria by hand", icon=":material/tune:"), st.form(f"criteria{job['id']}"):
    left, right = st.columns([3, 2], gap="large")
    with left:
        req = st.multiselect("Required skills", pool, default=current_req, accept_new_options=True, key=f"req{ver}",
                             placeholder="Pick from the skills found in the resumes, or type one")
        nice = st.multiselect("Nice to have", pool, default=current_nice, accept_new_options=True, key=f"nice{ver}",
                              placeholder="Optional")
        years = st.number_input("Minimum years of experience", 0.0, 40.0, float(crit["min_years"]), 0.5,
                                key=f"years{ver}")
    with right:
        st.caption("How much each criterion counts in the score")
        wr = st.slider("Required skills", 0, 100, w["required"], 5, key=f"wr{ver}")
        wn = st.slider("Nice to have", 0, 100, w["nice"], 5, key=f"wn{ver}")
        we = st.slider("Experience", 0, 100, w["experience"], 5, key=f"we{ver}")
        reparse = st.checkbox("Re-read the resumes with the AI (slower, also catches synonyms)")
    if st.form_submit_button("Apply and re-rank", type="primary"):
        with st.spinner("The AI is re-reading every resume..." if reparse else "Re-ranking..."):
            r = call("PUT", f"/jobs/{job['id']}/criteria", params={"reparse": str(reparse).lower()}, timeout=1800,
                     json={"required_skills": req, "nice_to_have": nice, "min_years": years,
                           "weights": {"required": wr, "nice": wn, "experience": we}})
        if r is not None:
            st.rerun()

# ------------------------------------------------------------------ ranking
if not ranking:
    empty("No resumes for this job yet.",
          "Add some in the 'Add resumes' tab below" + (", or load the demo data." if is_admin() else "."))
    load_demo_button()
else:
    head = st.empty()  # the title shows the count, known once the filters below are read
    f1, f2, f3 = st.columns([2, 2, 1], vertical_alignment="bottom")
    min_score = f1.slider("Show scores from", 0, 100, 0, 5)
    all_required = f2.checkbox("Only candidates with every required skill")
    hide_rejected = f3.checkbox("Hide rejected")
    shown = [c for c in ranking
             if (c["score"] or 0) >= min_score
             and not (all_required and c["score_breakdown"].get("required_missing"))
             and not (hide_rejected and c["status"] == "rejected")]

    head.subheader(f"Ranking: {len(shown)} of {len(ranking)} resumes")
    if weights := ranking[0]["score_breakdown"].get("weights") or {}:
        names = {"required": "required skills", "nice": "nice-to-have", "experience": "experience"}
        st.caption("Score = " + " + ".join(f"{v}% {names[k]}" for k, v in weights.items()) + ".")

    pending = call("GET", "/approvals", params={"status": "pending"}) or []
    pending_decision = {a["payload"]["application_id"]: a["payload"]["decision"] for a in pending
                        if a["type"] == "record_decision"}
    pending_email = {a["payload"].get("candidate_id") for a in pending
                     if a["type"] in ("send_email", "send_reminder", "schedule_interview")}
    metas = {c["application_id"]: row_meta(c, pending_decision, pending_email) for c in ranking}
    meta = [metas[c["application_id"]] for c in shown]

    picked_key = f"picked{job['id']}"
    picked = st.session_state[picked_key] = [a for a in st.session_state.get(picked_key, []) if a in metas]
    b1, b2, hint, how = st.columns([1, 1, 2.5, 1.2], vertical_alignment="center")
    if b1.button(f"Compare ({len(picked)})", type="primary", disabled=len(picked) < 2, width="stretch"):
        st.session_state.compare_job = job["id"]
    if b2.button("Clear selection", disabled=not picked, width="stretch"):
        st.session_state[picked_key], st.session_state.compare_job = [], None
        st.rerun()
    hint.caption("Tick two or more candidates in the **Compare** column, then click Compare." if len(picked) < 2
                 else "Selected: " + ", ".join(esc(metas[a]["name"]) for a in picked))
    with how.popover("How it works", icon=":material/help:", width="stretch"):
        st.markdown("- **Decision**: pick one in the table. It goes to Approvals, and someone else approves it.\n"
                    "- **Email**: drafts the message that matches the approved decision. It is sent only after "
                    "approval.\n"
                    "- **🔎**: the details, the bias check and questions about the candidate.\n"
                    "- **⚠️**: possible prompt injection or hidden text. Review before any decision.\n"
                    "- **Compare**: tick two or more candidates, then click Compare.")

    df = pd.DataFrame([{  # the actions sit next to the name, so they are visible without scrolling sideways
        "Compare": c["application_id"] in picked,
        "Rank": c["rank"],
        "Candidate": m["name"],
        "Details": "🔎",
        "Score": c["score"],
        "⚠️": "⚠️" if risky(c) else "",
        "Decision": m["decision"],
        "Decision status": m["state"],
        "Email": m["email_label"],
        "Years": c["score_breakdown"].get("years_experience"),
        "Required skills": ", ".join(c["score_breakdown"].get("required_matched", [])),
        "Missing": ", ".join(c["score_breakdown"].get("required_missing", [])),
        "Source": SOURCE_BADGE.get(c.get("source", "upload"), c.get("source", "upload")),
        "Why": why(c),
    } for c, m in zip(shown, meta)])
    editor_key = f"editor{job['id']}_{st.session_state.get('editor_rev', 0)}"
    email_key, view_key = f"email{job['id']}", f"view{job['id']}"
    st.data_editor(
        df, key=editor_key, hide_index=True, width="stretch", height=min(38 * (len(df) + 1) + 3, 600), placeholder="—",
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
                width="small", type="tertiary", key=view_key, on_click=on_row_button, args=(view_key, "view", meta),
                pinned=True, help="Why this rank, the bias check, hidden text, and questions about the candidate."),
        })

    if draft := st.session_state.pop("last_draft", None):
        st.success(f"{draft['kind'].capitalize()} email for {esc(draft['name'])} drafted and waiting in the approval "
                   "queue. Edit and send it on the Approvals page.", icon=":material/mail:")
        st.text(f"Subject: {draft['payload']['subject']}\n\n{draft['payload']['body']}")
        st.page_link("pages/3_Approvals.py", label="Open Approvals", icon=":material/arrow_forward:")

    # -------------------------------------------------------------- comparison
    if st.session_state.get("compare_job") == job["id"] and len(picked) >= 2:
        cands = [c for c in ranking if c["application_id"] in picked]  # in ranking order
        st.divider()
        head, close = st.columns([6, 1])
        head.subheader(f"Comparing {len(cands)} candidates")
        if close.button("Close", key="close_compare"):
            st.session_state.compare_job = None
            st.rerun()
        # Asked once per selection and scores: Streamlit reruns the page on every click.
        advice_key = ("advice", job["id"], tuple((c["application_id"], c["score"]) for c in cands))
        if not st.session_state.get(advice_key):
            with st.spinner("Writing the recommendation..."):
                st.session_state[advice_key] = call("POST", f"/jobs/{job['id']}/compare",
                                                    json={"application_ids": [c["application_id"] for c in cands]})
        if advice := st.session_state[advice_key]:
            # The sentences can quote resume text: escaped so they cannot render links, images or formatting.
            st.markdown(f"#### {esc(advice['recommendation'])}")
            for alt in advice["alternatives"]:
                st.markdown(f"- {esc(alt['sentence'])}")
            if advice["needs_review"]:
                st.warning("Review before deciding: possible prompt injection in "
                           f"{', '.join(map(esc, advice['needs_review']))}.", icon=":material/gpp_maybe:")
            st.caption("The recommended candidate is the highest score, chosen by the code; the AI only writes the "
                       "sentences" + (" (the AI did not answer, so they were written by simple rules)."
                                      if advice["llm_fallback"] else "."))
        with st.expander("Side-by-side details"):
            table = compare_table(cands, crit, metas)
            st.dataframe(table, width="stretch", height=35 * (len(table) + 1) + 3)
            for tab, c in zip(st.tabs([f"#{c['rank']} {esc(metas[c['application_id']]['name'])}" for c in cands]),
                              cands):
                with tab:
                    json_panel(c, "cmp")

    # -------------------------------------------------------------- candidate details (🔎)
    if cand := next((c for c in ranking if c["application_id"] == st.session_state.get("view_aid")), None):
        st.divider()
        head, close = st.columns([6, 1])
        head.subheader(f"#{cand['rank']} {esc(cand['name'] or cand['filename'])}")
        if close.button("Close", key="close_details"):
            st.session_state.view_aid = None
            st.rerun()
        left, right = st.columns(2)
        with left:
            st.markdown(f"**Why this rank:** {esc(why(cand))}")
            for s in cand["justification"].get("strengths", []):
                st.markdown(f"✅ {esc(s)}")
            for g in cand["justification"].get("gaps", []):
                st.markdown(f"➖ {esc(g)}")
            st.markdown(f"Email: {esc(cand['email']) if cand['email'] else 'not found in the resume'}")
            if f := cand["fairness"]:
                st.caption(f"Bias check: score with identity masked = {f['counterfactual_score']} (Δ {f['rank_delta']:+g}); "
                           f"masked: {', '.join(f.get('masked', []))}")
            if cand["flags"]:
                st.warning("Possible prompt injection in this resume. Review it before any decision: "
                           + "; ".join(f"{x['type']} ({x['severity']})" for x in cand["flags"][:5]))
            if cand["hidden_text"]:
                st.error(f"Hidden text removed before the AI saw it: “{esc(cand['hidden_text'][:300])}”")
        with right:
            st.markdown("**Ask about this candidate** (answered from this resume only)")
            q = st.text_input("Question", key=f"q{cand['application_id']}", label_visibility="collapsed",
                              placeholder="e.g. Has this person used Docker in production?")
            if q and st.button("Ask", key=f"ask{cand['application_id']}"):
                if r := call("POST", f"/candidates/{cand['candidate_id']}/chat", json={"question": q}):
                    st.markdown(esc(r["answer"]))  # answered from the resume: untrusted text
                    for ev in r["evidence"]:
                        st.caption(f"“{esc(ev)}”")
        with st.expander("Full record (JSON)", icon=":material/data_object:"):
            json_panel(cand, "view")

# ------------------------------------------------------------------ add resumes, learn weights
st.divider()
last_upload = st.session_state.pop("last_upload", None)
add_tab, learn_tab = st.tabs(["Add resumes", "Learn the weights from past hires"])
with add_tab:
    if last_upload:
        if ok := sum(r["accepted"] for r in last_upload):
            st.success(f"{ok} resume(s) added and ranked above.")
        if refused := [r for r in last_upload if not r["accepted"]]:
            st.warning("Refused: " + "; ".join(f"{esc(r['filename'])} ({esc(r['error'])})" for r in refused))
    files = st.file_uploader("PDF, Word, OpenDocument, RTF, HTML, text, JSON or a photo (JPG, PNG, WebP)",
                             accept_multiple_files=True, key=f"files{job['id']}")
    if files and st.button("Upload", type="primary", icon=":material/upload:", disabled=not can("resumes:upload")):
        res = call("POST", f"/jobs/{job['id']}/resumes", files=[("files", (f.name, f.getvalue())) for f in files])
        if res:
            st.session_state.last_upload = res
            st.rerun()
with learn_tab:
    learn_weights(job)
