from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..agent.graph import run_agent, thread_attachments
from ..db import get_db
from ..models import ChatMessage, Feedback, LabReport, MriScan, Trace, User
from ..schemas import ChatIn, FeedbackIn
from ..security import current_user, rate_limit, sanitize

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _attachments(db: Session, user_id: int, ids: dict) -> dict:
    out = {"report": None, "mri": None}
    if ids.get("report_id"):
        r = db.get(LabReport, ids["report_id"])
        if r and r.user_id == user_id:
            out["report"] = {"id": r.id, "filename": r.filename}
    if ids.get("mri_id"):
        s = db.get(MriScan, ids["mri_id"])
        if s and s.user_id == user_id:
            out["mri"] = {"id": s.id, "filename": s.filename}
    return out


@router.post("")
def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"chat:{user.id}")
    out = run_agent(db, user.id, sanitize(body.message, 3000), body.thread_id, body.audience, body.report_id, body.mri_id, body.language)
    out["attachments"] = _attachments(db, user.id, out.get("attachments") or {})
    return out


@router.get("/threads")
def threads(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ChatMessage.thread_id, func.min(ChatMessage.id), func.max(ChatMessage.created_at))
        .where(ChatMessage.user_id == user.id)
        .group_by(ChatMessage.thread_id)
        .order_by(func.max(ChatMessage.id).desc())
        .limit(30)
    ).all()
    out = []
    for tid, first_id, last in rows:
        first = db.get(ChatMessage, first_id)
        out.append({"thread_id": tid, "title": (first.content if first else "")[:60], "updated_at": last.isoformat(),
                    "has_attachment": bool(first and ((first.meta or {}).get("report_id") or (first.meta or {}).get("mri_id")))})
    return out


@router.get("/threads/{thread_id}")
def thread(thread_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.thread_id == thread_id).order_by(ChatMessage.id)
    ).all()
    return {"messages": [{"role": r.role, "content": r.content, "meta": r.meta} for r in rows],
            "attachments": _attachments(db, user.id, thread_attachments(db, user.id, thread_id))}


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.thread_id == thread_id))
    db.commit()
    return {"ok": True}


@router.post("/feedback")
def feedback(body: FeedbackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"fb:{user.id}", limit=60)
    tr = db.scalar(select(Trace).where(Trace.trace_id == body.trace_id))
    if tr is None or tr.user_id != user.id:
        raise HTTPException(404, "Not found")
    fb = db.scalar(select(Feedback).where(Feedback.trace_id == body.trace_id, Feedback.user_id == user.id))
    if body.rating == 0:
        if fb:
            db.delete(fb)
        db.commit()
        return {"ok": True}
    fb = fb or Feedback(user_id=user.id, trace_id=body.trace_id, rating=body.rating)
    fb.rating, fb.comment = body.rating, sanitize(body.comment, 500)
    db.add(fb)
    db.commit()
    return {"ok": True}
