"""Memory service — short-term and long-term memory behind one small interface.

Short-term  : last 10 messages of the current thread + the report/scan attached to this request.
Long-term   : profile (sex for sex-specific ranges, age group, preferred view, notes) and episodic
              summaries of past reports/scans. Written ONLY when the user has consented.
Procedural  : prompts and rules live in code (explainer.py, guardrails.py), not in the DB.
Deletion    : forget_everything() removes chat, reports, scans, memory and profile.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ChatMessage, LabReport, MemoryItem, MriScan, Trace, UserProfile

SHORT_TERM_TURNS = 10
PROFILE_FIELDS = {"sex", "age_group", "preferred_view", "notes", "preferred_language"}


class ConsentRequired(PermissionError):
    pass


def profile(db: Session, user_id: int) -> UserProfile:
    p = db.get(UserProfile, user_id)
    if p is None:
        p = UserProfile(user_id=user_id, memory_consent=False, data={})
        db.add(p)
        db.commit()
    return p


def set_consent(db: Session, user_id: int, consent: bool) -> UserProfile:
    p = profile(db, user_id)
    p.memory_consent = consent
    if not consent:  # withdrawing consent removes what long-term memory holds
        p.data = {}
        db.execute(delete(MemoryItem).where(MemoryItem.user_id == user_id))
    db.commit()
    return p


def update_profile(db: Session, user_id: int, data: dict) -> UserProfile:
    p = profile(db, user_id)
    if not p.memory_consent:
        raise ConsentRequired("Turn on memory first to save your profile")
    clean = {k: str(v)[:200] for k, v in data.items() if k in PROFILE_FIELDS and v not in (None, "")}
    p.data = {**(p.data or {}), **clean}
    db.commit()
    return p


def remember(db: Session, user_id: int, kind: str, content: str) -> MemoryItem | None:
    """Save an episodic memory. Silently does nothing without consent (the caller tells the user)."""
    if not profile(db, user_id).memory_consent:
        return None
    item = MemoryItem(user_id=user_id, kind=kind, content=content[:600])
    db.add(item)
    db.commit()
    return item


def long_term(db: Session, user_id: int, limit: int = 5) -> dict:
    p = profile(db, user_id)
    if not p.memory_consent:
        return {"consent": False, "profile": {}, "items": []}
    items = db.scalars(
        select(MemoryItem).where(MemoryItem.user_id == user_id).order_by(MemoryItem.id.desc()).limit(limit)
    ).all()
    return {
        "consent": True,
        "profile": p.data or {},
        "items": [{"id": i.id, "kind": i.kind, "content": i.content, "created_at": i.created_at.isoformat()} for i in items],
    }


def short_term(db: Session, user_id: int, thread_id: str) -> list[dict]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id, ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.id.desc())
        .limit(SHORT_TERM_TURNS)
    ).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def save_message(db: Session, user_id: int, thread_id: str, role: str, content: str, meta: dict | None = None) -> None:
    db.add(ChatMessage(user_id=user_id, thread_id=thread_id, role=role, content=content, meta=meta or {}))
    db.commit()


def forget_item(db: Session, user_id: int, item_id: int) -> bool:
    item = db.get(MemoryItem, item_id)
    if item is None or item.user_id != user_id:
        return False
    db.delete(item)
    db.commit()
    return True


def forget_everything(db: Session, user_id: int) -> None:
    from ..models import Export, Feedback

    for model in (ChatMessage, LabReport, MriScan, MemoryItem, Export, Feedback):
        db.execute(delete(model).where(model.user_id == user_id))
    db.execute(delete(Trace).where(Trace.user_id == user_id))
    db.execute(delete(UserProfile).where(UserProfile.user_id == user_id))
    db.commit()


def history_text(messages: list[dict], max_chars: int = 1500) -> str:
    lines = [f"{m['role']}: {m['content'][:300]}" for m in messages]
    return "\n".join(lines)[-max_chars:]
