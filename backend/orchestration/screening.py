"""Screening workflow: extract (parallel LLM) -> score (code) -> rank -> fairness audit.

Criteria (skills, years, weights) can come from a job, from the recruiter's own words (brief agent), or be edited
by hand; changing them re-scores instantly from the facts already extracted (no LLM). Decisions and learned
weights are only ever PROPOSED, and a different person must approve them (four-eyes).
"""
import json
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.agents import bias_auditor, brief, compare, drafter, ranker
from backend.agents.schemas import CandidateProfile
from backend.config import get_settings
from backend.data.models import Application, Candidate, FairnessReport, JobOffer, ProposedAction, Resume, utcnow
from backend.orchestration import learner, workflow
from backend.security import audit

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


def criteria(job: JobOffer) -> dict:
    c = json.loads(job.criteria)
    c.setdefault("weights", dict(ranker.DEFAULT_WEIGHTS))
    return c


def job_skills(c: dict) -> list[str]:
    return c.get("required_skills", []) + c.get("nice_to_have", [])


def verified_email(email: str, text: str) -> str:
    """Only keep an address that literally appears in the resume: the LLM never invents a recipient."""
    email = (email or "").strip().lower()
    return email if EMAIL_RE.match(email) and email in text.lower() else ""


def _rows(session: Session, job: JobOffer):
    q = (select(Application, Resume, Candidate)
         .join(Resume, Resume.id == Application.resume_id)
         .join(Candidate, Candidate.id == Application.candidate_id)
         .where(Application.job_id == job.id, Application.tenant_id == job.tenant_id))
    return session.exec(q).all()


def _apply_score(session: Session, app: Application, resume: Resume, crit: dict, profile: CandidateProfile) -> None:
    breakdown = ranker.score(profile, crit)
    app.match_score = breakdown["total"]
    app.criteria_justification = json.dumps({"breakdown": breakdown, **ranker.strengths_gaps(breakdown)})
    fair = bias_auditor.audit(resume.raw_text, crit, breakdown["total"])
    report = session.exec(select(FairnessReport).where(FairnessReport.application_id == app.id)).first() \
        or FairnessReport(tenant_id=app.tenant_id, application_id=app.id, original_score=0, counterfactual_score=0, rank_delta=0)
    report.original_score, report.counterfactual_score = fair["original_score"], fair["counterfactual_score"]
    report.rank_delta, report.counterfactuals = fair["rank_delta"], json.dumps(fair["counterfactuals"])
    session.add(app)
    session.add(report)


def screen(session: Session, job: JobOffer, actor: str, reparse: bool = False) -> int:
    """Reads new resumes (LLM, in parallel) and scores them against the job criteria."""
    crit = criteria(job)
    todo = [(a, r, c) for a, r, c in _rows(session, job) if reparse or a.match_score is None]
    # Only the LLM calls run in threads; the DB is only touched from this thread.
    with ThreadPoolExecutor(max_workers=max(1, get_settings().llm_concurrency)) as pool:
        extracted = list(pool.map(lambda row: ranker.extract_profile(row[1].raw_text, job_skills(crit)), todo))
    for (app, resume, cand), (profile, fallback) in zip(todo, extracted):
        cand.name = cand.name or profile.name
        cand.email = cand.email or verified_email(profile.email, resume.raw_text)
        resume.profile = profile.model_dump_json()
        app.llm_fallback = fallback
        if app.status == "new":
            app.status = "needs_review" if resume.risk_level in ("medium", "high") else "screened"
        if cand.stage == "new":
            cand.stage = "screened"
        _apply_score(session, app, resume, crit, profile)
        session.add(cand)
        session.add(resume)
    session.commit()
    rank(session, job)
    audit.log(session, actor=actor, event="screening.run", tenant_id=job.tenant_id, target=f"job:{job.id}",
              detail={"screened": len(todo)})
    return len(todo)


def rescore(session: Session, job: JobOffer) -> None:
    """Re-ranks already-read resumes with the job's current criteria. Pure code, no LLM call: instant."""
    crit = criteria(job)
    for app, resume, _ in _rows(session, job):
        if app.match_score is not None:
            _apply_score(session, app, resume, crit, CandidateProfile.model_validate_json(resume.profile))
    session.commit()
    rank(session, job)


def set_criteria(session: Session, job: JobOffer, new: dict, actor: str, reparse: bool = False) -> None:
    """Changes what the ranking is based on, then re-ranks. reparse=True reads every resume again with the new
    skill list (LLM); otherwise only the scoring is recomputed."""
    before = criteria(job)
    job.criteria = json.dumps(new)
    session.add(job)
    session.commit()
    if reparse:
        screen(session, job, actor, reparse=True)
    else:
        rescore(session, job)
    audit.log(session, actor=actor, event="job.criteria_changed", tenant_id=job.tenant_id, target=f"job:{job.id}",
              detail={"before": before, "after": new, "reparse": reparse})


def criteria_from_text(session: Session, job: JobOffer, text: str) -> tuple[dict, bool]:
    """The recruiter's request in their own words -> criteria. Nothing is saved: the recruiter checks them and
    applies them with set_criteria."""
    found = Counter(s.lower() for _, r, _ in _rows(session, job) for s in json.loads(r.profile).get("skills", []))
    crit, fallback = brief.parse_brief(text, job_skills(criteria(job)) + [s for s, _ in found.most_common(60)])
    return crit.model_dump(), fallback


def compare_applications(session: Session, job: JobOffer, app_ids: list[uuid.UUID]) -> dict:
    rows = {a.id: (a, r, c) for a, r, c in _rows(session, job)}
    picked = [rows[i] for i in dict.fromkeys(app_ids) if i in rows]
    if len(picked) < 2 or any(a.match_score is None for a, _, _ in picked):
        raise HTTPException(400, "Pick at least two screened candidates of this job")
    asked = {s.lower() for s in job_skills(criteria(job))}
    return compare.advise([
        compare.facts(str(a.id), c.name or r.original_filename, a.match_score, a.rank,
                      json.loads(a.criteria_justification).get("breakdown", {}), json.loads(r.profile), asked,
                      r.risk_level in ("medium", "high"))
        for a, r, c in picked])


def propose_learned_weights(session: Session, job: JobOffer, csv_bytes: bytes, requested_by: str) -> dict:
    """Learns the criteria weights from a CSV of past hires and proposes them for approval. The rows are not
    stored: only the aggregate report goes into the approval queue and the audit log."""
    crit = criteria(job)
    try:
        report = learner.learn(learner.read_csv(csv_bytes, crit.get("required_skills", []), crit.get("nice_to_have", []),
                                                float(crit.get("min_years", 0))), crit["weights"])
    except learner.LearnError as e:
        raise HTTPException(422, str(e))
    fairness = report["fairness"]
    audit.log(session, actor=requested_by, event="screening.weights_learned", tenant_id=job.tenant_id,
              target=f"job:{job.id}", detail={k: report[k] for k in ("weights", "learned_from", "accuracy", "fairness")})
    if fairness["checked"] and not fairness["passed"]:
        raise HTTPException(422, {"message": f"Blocked by the bias check: impact ratio {fairness['impact_ratio']} is "
                                             "below 0.8 (four-fifths rule). The weights were not proposed.",
                                  "report": report})
    w = report["weights"]
    action = workflow.propose(
        session, tenant_id=job.tenant_id, type="apply_weights", created_by_agent="weight_learner",
        title=f"Use learned weights for {job.title}: required {w['required']}, nice {w['nice']}, experience {w['experience']}",
        payload={"job_id": str(job.id), "weights": w, "report": report},
        rationale=f"Learned from {report['learned_from']} past hires ({report['good_outcomes']} good outcomes); "
                  + ("bias check passed" if fairness["checked"] else "no bias check: no group column"),
        input_risk="low" if fairness["checked"] else "high", requested_by=requested_by, source_type="job",
        source_id=str(job.id))
    return {"action_id": str(action.id), "status": action.status, "report": report}


def rank(session: Session, job: JobOffer) -> None:
    apps = [a for a, _, _ in _rows(session, job)]
    ordered = sorted(apps, key=lambda a: (a.match_score is None, -(a.match_score or 0), str(a.id)))
    for i, a in enumerate(ordered, 1):
        a.rank = i if a.match_score is not None else None
        session.add(a)
    session.commit()


def ranking(session: Session, job: JobOffer) -> list[dict]:
    out = []
    for app, resume, cand in _rows(session, job):
        fair = session.exec(select(FairnessReport).where(FairnessReport.application_id == app.id)).first()
        just = json.loads(app.criteria_justification)
        out.append({
            "application_id": str(app.id), "candidate_id": str(cand.id), "name": cand.name, "email": cand.email,
            "stage": cand.stage, "status": app.status, "score": app.match_score, "rank": app.rank,
            "bucket": ranker.bucket(app.match_score), "risk_level": resume.risk_level,
            "injection_score": resume.injection_score, "flags": json.loads(resume.flags),
            "hidden_text": resume.hidden_text[:500], "llm_fallback": app.llm_fallback,
            "justification": just, "score_breakdown": just.get("breakdown", {}), "profile": json.loads(resume.profile),
            "fairness": {"rank_delta": fair.rank_delta, "counterfactual_score": fair.counterfactual_score,
                         **json.loads(fair.counterfactuals)} if fair else None,
            "filename": resume.original_filename, "source": resume.source,
        })
    return sorted(out, key=lambda r: (r["rank"] is None, r["rank"] or 0))


def propose_decision(session: Session, app: Application, decision: str, note: str, requested_by: str) -> ProposedAction:
    cand = session.get(Candidate, app.candidate_id)
    resume = session.get(Resume, app.resume_id)
    return workflow.propose(
        session, tenant_id=app.tenant_id, type="record_decision", title=f"Mark {cand.name or 'candidate'} as {decision}",
        payload={"application_id": str(app.id), "decision": decision},
        rationale=note or f"Score {app.match_score}", input_risk=resume.risk_level,
        input_flags=json.loads(resume.flags), requested_by=requested_by, source_type="application", source_id=str(app.id))


def propose_candidate_email(session: Session, app: Application, kind: str, requested_by: str) -> ProposedAction:
    """Drafted from the candidate's own file. A rejection needs an approved rejection decision first; invitations
    go through scheduling (they carry the booking link)."""
    cand = session.get(Candidate, app.candidate_id)
    job = session.get(JobOffer, app.job_id)
    if not cand.email:
        raise HTTPException(422, "The candidate's resume contains no email address")
    if kind == "rejection" and app.status != "rejected":
        raise HTTPException(409, "Record an approved rejection decision before drafting the rejection email")
    if kind == "reminder":
        draft = drafter.template(kind, name=drafter.first_name(cand.name), job=job.title)
    else:
        resume = session.get(Resume, app.resume_id)
        draft, _ = drafter.draft_candidate_email(kind, job.title, json.loads(resume.profile))
    return workflow.propose(
        session, tenant_id=app.tenant_id, type="send_reminder" if kind == "reminder" else "send_email",
        title=f"{kind.title()} email to {cand.name or cand.email}",
        payload={"to": cand.email, "subject": draft.subject, "body": draft.body, "candidate_id": str(cand.id)},
        rationale=f"Template: {kind}", requested_by=requested_by, source_type="application", source_id=str(app.id))


@workflow.on_resume("application")
def _after_candidate_action(session: Session, action: ProposedAction) -> None:
    if action.status == "executed" and action.type in ("send_email", "send_reminder"):
        app = session.get(Application, uuid.UUID(action.source_id))
        if app:
            cand = session.get(Candidate, app.candidate_id)
            cand.last_contact_at = utcnow()
            session.add(cand)


@workflow.on_resume("job")
def _after_weights(session: Session, action: ProposedAction) -> None:
    if action.status == "executed" and action.type == "apply_weights":
        job = session.get(JobOffer, uuid.UUID(action.source_id))
        if job:
            rescore(session, job)
