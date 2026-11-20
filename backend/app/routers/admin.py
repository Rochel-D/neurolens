"""Admin: overview, API key controls, traces + feedback, knowledge-base editor, system review, MCP."""
import csv
import io
import statistics
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Feedback, LabReport, MriScan, Setting, Trace, User
from ..security import admin_user
from ..services import explainer, kb, lab_parser, mcp_client, mri, system_review
from ..services.model_router import get_router

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ------------------------------------------------------------------ persisted router settings
def apply_saved_settings(db: Session) -> None:
    """Called at startup: re-apply model choices and disabled keys saved from the admin page."""
    r = get_router()
    for s in db.scalars(select(Setting)).all():
        if s.key.startswith("models:"):
            r.set_models(s.key.split(":", 1)[1], s.value.get("models", []))
        elif s.key.startswith("slot:"):
            r.set_enabled(s.key.split(":", 1)[1], bool(s.value.get("enabled", True)))


def _save(db: Session, key: str, value: dict) -> None:
    row = db.get(Setting, key) or Setting(key=key)
    row.value = value
    db.add(row)
    db.commit()


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    traces = db.scalars(select(Trace).where(Trace.created_at >= since)).all()
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    per_day = {d: {"date": d, "runs": 0, "errors": 0, "blocked": 0, "fallbacks": 0} for d in days}
    for t in traces:
        d = per_day.get((t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=timezone.utc)).strftime("%Y-%m-%d"))
        if d:
            d["runs"] += 1
            d["errors"] += t.status == "error"
            d["blocked"] += t.status == "blocked"
            d["fallbacks"] += not t.verified
    lat = sorted(t.latency_ms for t in traces if t.status == "ok") or [0]
    fb = db.scalars(select(Feedback).where(Feedback.created_at >= since)).all()
    intents: dict[str, int] = {}
    for t in traces:
        for i in (t.intent or "other").split(","):
            intents[i or "other"] = intents.get(i or "other", 0) + 1
    engines: dict[str, int] = {}
    for t in traces:
        for p in t.providers or []:
            if p.startswith("explain:"):
                e = p.split(":", 1)[1].split(" · ")[0:2]
                k = " · ".join(e)
                engines[k] = engines.get(k, 0) + 1
    return {
        "totals": {"users": db.scalar(select(func.count()).select_from(User)) or 0,
                   "reports": db.scalar(select(func.count()).select_from(LabReport)) or 0,
                   "scans": db.scalar(select(func.count()).select_from(MriScan)) or 0,
                   "runs_7d": len(traces)},
        "latency": {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))], "mean": int(statistics.mean(lat))},
        "feedback": {"up": sum(1 for f in fb if f.rating > 0), "down": sum(1 for f in fb if f.rating < 0)},
        "per_day": list(per_day.values()),
        "intents": dict(sorted(intents.items(), key=lambda kv: -kv[1])),
        "engines": dict(sorted(engines.items(), key=lambda kv: -kv[1])[:8]),
    }


# ------------------------------------------------------------------ key pool controls
@router.get("/keys")
def keys(_: User = Depends(admin_user)):
    r = get_router()
    s = get_settings()
    return {"slots": r.status(), "config": {"gemini": [m for m in (s.gemini_model, s.gemini_fallback_model)],
                                            "groq": [s.groq_model], "xai": [s.xai_model]}}


@router.post("/keys/{slot}/test")
def test_key(slot: str, _: User = Depends(admin_user)):
    res = get_router().test_slot(slot)
    if res.get("error") == "unknown slot":
        raise HTTPException(404, "Unknown key slot")
    return res


@router.post("/keys/{slot}/reset")
def reset_key(slot: str, _: User = Depends(admin_user)):
    get_router().reset(None if slot == "all" else slot)
    return {"ok": True}


class EnabledIn(BaseModel):
    enabled: bool


@router.post("/keys/{slot}/enabled")
def set_enabled(slot: str, body: EnabledIn, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    r = get_router()
    if r.slot(slot) is None:
        raise HTTPException(404, "Unknown key slot")
    r.set_enabled(slot, body.enabled)
    _save(db, f"slot:{slot}", {"enabled": body.enabled})
    return {"ok": True}


class ModelsIn(BaseModel):
    provider: str = Field(pattern="^(gemini|groq|xai)$")
    models: list[str] = Field(default_factory=list, max_length=4)


@router.post("/models")
def set_models(body: ModelsIn, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    models = [m.strip() for m in body.models if m.strip() and m.strip() != "auto"]
    get_router().set_models(body.provider, models)
    _save(db, f"models:{body.provider}", {"models": models})
    return {"ok": True, "models": models or ["auto"]}


@router.get("/models")
def models(_: User = Depends(admin_user)):
    return {"mri": mri.model_status(), "explainer": explainer.local_status()}


# ------------------------------------------------------------------ traces + feedback
def _trace_query(db: Session, q: str, intent: str, status: str, verified: str, rating: str):
    stmt = select(Trace)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Trace.message.ilike(like), Trace.trace_id.ilike(like)))
    if intent:
        stmt = stmt.where(Trace.intent.ilike(f"%{intent}%"))
    if status:
        stmt = stmt.where(Trace.status == status)
    if verified in ("yes", "no"):
        stmt = stmt.where(Trace.verified.is_(verified == "yes"))
    if rating in ("up", "down"):
        sub = select(Feedback.trace_id).where(Feedback.rating == (1 if rating == "up" else -1))
        stmt = stmt.where(Trace.trace_id.in_(sub))
    return stmt.order_by(Trace.id.desc())


def _trace_out(t: Trace, ratings: dict) -> dict:
    fb = ratings.get(t.trace_id)
    return {"trace_id": t.trace_id, "intent": t.intent, "status": t.status, "verified": t.verified, "latency_ms": t.latency_ms,
            "providers": t.providers, "guard_flags": t.guard_flags, "steps": t.steps, "language": t.language or "en",
            "message": t.message or "", "created_at": t.created_at.isoformat(),
            "rating": fb.rating if fb else 0, "comment": fb.comment if fb else ""}


@router.get("/traces")
def traces(q: str = Query("", max_length=100), intent: str = Query("", max_length=30), status: str = Query("", max_length=20),
           verified: str = Query("", max_length=3), rating: str = Query("", max_length=4), limit: int = Query(50, ge=1, le=200),
           offset: int = Query(0, ge=0), _: User = Depends(admin_user), db: Session = Depends(get_db)):
    stmt = _trace_query(db, q, intent, status, verified, rating)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    ratings = {f.trace_id: f for f in db.scalars(select(Feedback).where(Feedback.trace_id.in_([r.trace_id for r in rows]))).all()}
    return {"total": total, "items": [_trace_out(t, ratings) for t in rows]}


@router.get("/traces.csv")
def traces_csv(q: str = "", intent: str = "", status: str = "", verified: str = "", rating: str = "",
               _: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(_trace_query(db, q[:100], intent[:30], status[:20], verified[:3], rating[:4]).limit(5000)).all()
    ratings = {f.trace_id: f for f in db.scalars(select(Feedback)).all()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "trace_id", "intent", "status", "verified", "latency_ms", "language", "models", "guard_flags", "rating", "comment", "message"])
    for t in rows:
        fb = ratings.get(t.trace_id)
        msg = (t.message or "").replace("\n", " ")
        if msg[:1] in ("=", "+", "-", "@"):  # CSV formula injection guard
            msg = "'" + msg
        w.writerow([t.created_at.isoformat(), t.trace_id, t.intent, t.status, t.verified, t.latency_ms, t.language or "en",
                    " → ".join(t.providers or []), ";".join(t.guard_flags or []), fb.rating if fb else "", fb.comment if fb else "", msg])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="neurolens-traces.csv"'})


@router.get("/feedback")
def feedback(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Feedback, Trace).join(Trace, Trace.trace_id == Feedback.trace_id).order_by(Feedback.id.desc()).limit(100)).all()
    up = sum(1 for f, _t in rows if f.rating > 0)
    return {"up": up, "down": len(rows) - up,
            "items": [{"rating": f.rating, "comment": f.comment, "created_at": f.created_at.isoformat(), "trace_id": t.trace_id,
                       "intent": t.intent, "message": t.message, "engine": next((p for p in (t.providers or []) if p.startswith("explain:")), ""),
                       "verified": t.verified} for f, t in rows]}


# ------------------------------------------------------------------ knowledge base editor
def _kind(kind: str) -> str:
    if kind not in ("lab", "note"):
        raise HTTPException(404, "Not found")
    return kind


@router.get("/kb/{kind}")
def kb_list(kind: str, _: User = Depends(admin_user)):
    items = kb.lab_tests() if _kind(kind) == "lab" else kb.notes()
    seeds = kb.seed_ids(kind)
    return [{**i, "_seed": i["id"] in seeds} for i in sorted(items, key=lambda x: (x.get("category") or x.get("module", ""), x.get("name") or x.get("title", "")))]


@router.put("/kb/{kind}")
def kb_save(kind: str, item: dict, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    try:
        return kb.save(db, _kind(kind), {k: v for k, v in item.items() if not k.startswith("_")})
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/kb/{kind}/{item_id}")
def kb_delete(kind: str, item_id: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    kb.delete(db, _kind(kind), item_id)
    return {"ok": True}


@router.post("/kb/{kind}/{item_id}/reset")
def kb_reset(kind: str, item_id: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    kb.reset(db, _kind(kind), item_id)
    return {"ok": True}


class ParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    sex: str | None = Field(default=None, pattern="^(male|female|)$")


@router.post("/kb/test-parse")
def kb_test_parse(body: ParseIn, _: User = Depends(admin_user)):
    """Paste report lines and see exactly what the parser reads — handy after adding aliases."""
    res = lab_parser.parse_report(body.text, body.sex or None)
    unmatched = [ln.strip() for ln in body.text.splitlines() if ln.strip() and lab_parser.parse_line(ln) is None and any(c.isdigit() for c in ln)]
    return {"results": [r.dict() for r in res], "unmatched": unmatched[:30], "report_date": lab_parser.report_date(body.text)}


# ------------------------------------------------------------------ system
@router.get("/review")
def review(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return system_review.run(db)


@router.get("/mcp")
def mcp(_: User = Depends(admin_user)):
    if not get_settings().use_mcp:
        return {"ok": False, "error": "USE_MCP=false"}
    return mcp_client.status()
