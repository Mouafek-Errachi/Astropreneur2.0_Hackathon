"""Screening workflow: extract (parallel) -> score -> rank -> justify -> fairness audit.
Decisions (shortlist / interview / reject) are only ever PROPOSED."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from backend.agents import bias_auditor, drafter, ranker
from backend.data.models import Application, Candidate, FairnessReport, JobOffer, ProposedAction, Resume, utcnow
from backend.orchestration import workflow
from backend.security import audit

MAX_WORKERS = 6  # parallel LLM calls; 100 resumes stay within a few minutes on a live model


def _rows(session: Session, job: JobOffer):
    q = (select(Application, Resume, Candidate)
         .join(Resume, Resume.id == Application.resume_id)
         .join(Candidate, Candidate.id == Application.candidate_id)
         .where(Application.job_id == job.id, Application.tenant_id == job.tenant_id))
    return session.exec(q).all()


def screen(session: Session, job: JobOffer, actor: str, rescore: bool = False) -> int:
    criteria = json.loads(job.criteria)
    todo = [(a, r, c) for a, r, c in _rows(session, job) if rescore or a.match_score is None]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:  # only LLM calls run in threads; DB stays here
        extracted = list(pool.map(lambda row: ranker.extract_profile(row[1].raw_text), todo))
    for (app, resume, cand), (profile, fallback) in zip(todo, extracted):
        breakdown = ranker.score(profile, criteria)
        justification, fb2 = ranker.justify(profile, breakdown)
        cand.name, cand.email = cand.name or profile.name, cand.email or profile.email.lower()
        resume.profile = profile.model_dump_json()
        app.match_score = breakdown["total"]
        app.criteria_justification = json.dumps({"breakdown": breakdown, **justification.model_dump()})
        app.llm_fallback = fallback or fb2
        if app.status == "new":
            app.status = "needs_review" if resume.risk_level in ("medium", "high") else "screened"
        if cand.stage == "new":
            cand.stage = "screened"
        fair = bias_auditor.audit(resume.raw_text, criteria, breakdown["total"])
        report = session.exec(select(FairnessReport).where(FairnessReport.application_id == app.id)).first() \
            or FairnessReport(tenant_id=app.tenant_id, application_id=app.id, original_score=0, counterfactual_score=0, rank_delta=0)
        report.original_score, report.counterfactual_score = fair["original_score"], fair["counterfactual_score"]
        report.rank_delta, report.counterfactuals = fair["rank_delta"], json.dumps(fair["counterfactuals"])
        for obj in (cand, resume, app, report):
            session.add(obj)
    session.commit()
    rank(session, job)
    audit.log(session, actor=actor, event="screening.run", tenant_id=job.tenant_id, target=f"job:{job.id}",
              detail={"screened": len(todo)})
    return len(todo)


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
        out.append({
            "application_id": str(app.id), "candidate_id": str(cand.id), "name": cand.name, "email": cand.email,
            "stage": cand.stage, "status": app.status, "score": app.match_score, "rank": app.rank,
            "bucket": ranker.bucket(app.match_score), "risk_level": resume.risk_level,
            "injection_score": resume.injection_score, "flags": json.loads(resume.flags),
            "hidden_text": resume.hidden_text[:500], "llm_fallback": app.llm_fallback,
            "justification": json.loads(app.criteria_justification), "profile": json.loads(resume.profile),
            "fairness": {"rank_delta": fair.rank_delta, "counterfactual_score": fair.counterfactual_score,
                         **json.loads(fair.counterfactuals)} if fair else None,
            "filename": resume.original_filename,
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
    cand = session.get(Candidate, app.candidate_id)
    job = session.get(JobOffer, app.job_id)
    if not cand.email:
        raise ValueError("candidate has no email address")
    draft = drafter.template(kind, name=cand.name.split()[0] if cand.name else "", job=job.title)
    return workflow.propose(
        session, tenant_id=app.tenant_id, type="send_email", title=f"{kind.title()} email to {cand.name or cand.email}",
        payload={"to": cand.email, "subject": draft.subject, "body": draft.body}, rationale=f"Template: {kind}",
        requested_by=requested_by, source_type="application", source_id=str(app.id))


@workflow.on_resume("application")
def _after_candidate_action(session: Session, action: ProposedAction) -> None:
    if action.status == "executed" and action.type == "send_email":
        app = session.get(Application, uuid.UUID(action.source_id))
        if app:
            cand = session.get(Candidate, app.candidate_id)
            cand.last_contact_at = utcnow()
            session.add(cand)
