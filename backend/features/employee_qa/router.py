from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.core import injection
from backend.core.security import Principal, require
from backend.db import get_session
from backend.features.employee_qa.answerer import answer
from backend.features.employee_qa.indexer import reindex, search
from backend.schemas import AskRequest, AskResponse

router = APIRouter(prefix="/qa", tags=["employee_qa"])


@router.post("/reindex")
def do_reindex(p: Principal = Depends(require("qa:admin")), session: Session = Depends(get_session)):
    return {"chunks": reindex(session, p.tenant_id)}


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, p: Principal = Depends(require("qa:ask")), session: Session = Depends(get_session)):
    flags = injection.scan(body.question)
    hits = search(session, p.tenant_id, body.question)
    result, fallback = answer(body.question, hits)
    return AskResponse(answer=result.answer,
                       citations=[{"source": c.source, "heading": c.heading} for _, c in hits
                                  if c.source in result.cited_sources],
                       confidence=result.confidence, escalate_to_hr=result.escalate_to_hr,
                       flags=[f.to_dict() for f in flags], llm_fallback=fallback)
