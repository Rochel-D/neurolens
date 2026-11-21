"""Reports (general module), MRI scans (neuro module), trends, PDF exports and the home dashboard.
Uploaded files are processed in memory and never written to disk."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import ChatMessage, Export, LabReport, MriScan, User
from ..security import current_user, rate_limit, sanitize
from ..services import export_service, lab_parser, memory, mri, ocr

router = APIRouter(prefix="/api", tags=["reports"])
PHOTO = (".png", ".jpg", ".jpeg", ".webp")


async def _read(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {get_settings().max_upload_mb} MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    return data


def _report_out(r: LabReport, full: bool = True) -> dict:
    out = {"id": r.id, "filename": r.filename, "summary": r.summary, "report_date": r.report_date or f"{r.created_at:%Y-%m-%d}",
           "date_printed": bool(r.report_date), "source": r.source or "text", "status": r.status or "final",
           "created_at": r.created_at.isoformat()}
    if full:
        out["results"] = r.results
    return out


def _own_report(db: Session, user: User, report_id: int) -> LabReport:
    r = db.get(LabReport, report_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Not found")  # 404, not 403, for other people's reports
    return r


def _own_scan(db: Session, user: User, mri_id: int) -> MriScan:
    s = db.get(MriScan, mri_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(404, "Not found")
    return s


# ------------------------------------------------------------------ reports
@router.post("/reports")
async def upload_report(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"upload:{user.id}", limit=15)
    data = await _read(file)
    name = (file.filename or "report").lower()
    s = get_settings()
    status, source = "final", "text"
    try:
        if name.endswith(PHOTO) and data[:5] != b"%PDF-":
            res = ocr.read_photo(data)
            text, source, status = res.text, f"photo:{res.engine}", "needs_review"
        else:
            text = lab_parser.extract_text(data, file.filename or "", s.max_upload_mb, s.max_pdf_pages)
            source = "pdf" if data[:5] == b"%PDF-" else "text"
    except (lab_parser.ReportError, ocr.OcrError) as e:
        raise HTTPException(422, str(e)) from e
    sex = (memory.long_term(db, user.id)["profile"] or {}).get("sex")
    results = lab_parser.parse_report(text, sex)
    if not results:
        raise HTTPException(422, "I couldn't find any lab tests I recognise in this file. Check that it is a blood or lab report.")
    summary = lab_parser.summarise(results)
    if summary["suspect_tests"]:
        status = "needs_review"  # an implausible value (e.g. a unit misread) must be checked before any explanation
    # v2.1: uploading the same report twice made every trend say "same". Return the existing one instead.
    date = lab_parser.report_date(text)
    sig = sorted((r.test_id, r.value) for r in results)
    for old in db.scalars(select(LabReport).where(LabReport.user_id == user.id, LabReport.report_date == date)).all():
        if sorted((r.get("test_id"), r.get("value")) for r in old.results or []) == sig:
            out = _report_out(old)
            out["duplicate"] = True
            return out
    rep = LabReport(user_id=user.id, filename=sanitize(file.filename or "report", 200), results=[r.dict() for r in results],
                    summary=summary, report_date=date, source=source, status=status, explanations={})
    db.add(rep)
    db.commit()
    if status == "final":
        flagged = ", ".join(summary["flagged_tests"][:8]) or "none"
        memory.remember(db, user.id, "report", f"Lab report '{rep.filename}' ({rep.report_date or f'{rep.created_at:%d %b %Y}'}): "
                                                f"{summary['total']} tests, flagged: {flagged}.")
    return _report_out(rep)


@router.get("/reports")
def list_reports(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(LabReport).where(LabReport.user_id == user.id).order_by(LabReport.id.desc()).limit(50)).all()
    return sorted([_report_out(r, False) for r in rows], key=lambda r: (r["report_date"], r["id"]), reverse=True)


@router.get("/reports/{report_id}")
def get_report(report_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _report_out(_own_report(db, user, report_id))


class EditRow(BaseModel):
    test_id: str = Field(max_length=60)
    value: float
    ref_low: float | None = None
    ref_high: float | None = None


class EditIn(BaseModel):
    results: list[EditRow] = Field(max_length=80)
    report_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    confirm: bool = True


@router.put("/reports/{report_id}/results")
def edit_results(report_id: int, body: EditIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Review step for photo reports (and corrections for any report). Status is recomputed on the server."""
    rep = _own_report(db, user, report_id)
    cat = lab_parser.by_id()
    old = {r["test_id"]: r for r in rep.results}
    out = []
    rows = {r.test_id: r for r in body.results}.values()  # one row per test (last edit wins)
    for row in rows:
        t = cat.get(row.test_id)
        if t is None:
            raise HTTPException(422, f"Unknown test '{row.test_id}'")
        if row.ref_low is not None and row.ref_high is not None and row.ref_low >= row.ref_high:
            raise HTTPException(422, f"{t['name']}: low must be smaller than high")
        prev = old.get(row.test_id, {})
        lo, hi = row.ref_low, row.ref_high
        src = "report" if (lo is not None or hi is not None) else "standard"
        if src == "standard":
            lo, hi = t.get("low"), t.get("high")
        out.append({"suspect": False, "test_id": row.test_id, "name": t["name"], "value": round(row.value, 4), "unit": t["unit"], "ref_low": lo,
                    "ref_high": hi, "range_source": src, "status": lab_parser.status_for(row.test_id, row.value, lo, hi),
                    "category": t["category"], "raw": prev.get("raw", "edited by user")})
    if not out:
        raise HTTPException(422, "Keep at least one test")
    was_review = rep.status == "needs_review"
    rep.results = out
    rep.summary = lab_parser.summarise([lab_parser.LabResult(**r) for r in out])
    rep.explanations = {}  # values changed → cached explanations are stale
    if body.report_date is not None:
        rep.report_date = body.report_date
    if body.confirm:
        rep.status = "final"
    db.commit()
    if was_review and rep.status == "final":
        memory.remember(db, user.id, "report", f"Lab report '{rep.filename}' ({rep.report_date or 'photo'}): "
                                                f"{rep.summary['total']} tests, flagged: {', '.join(rep.summary['flagged_tests'][:8]) or 'none'}.")
    return _report_out(rep)


@router.delete("/reports/{report_id}")
def delete_report(report_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_report(db, user, report_id))
    db.commit()
    return {"ok": True}


@router.get("/catalogue")
def catalogue(user: User = Depends(current_user)):
    """Test list for the 'Add a test' picker in the review step."""
    return [{"id": t["id"], "name": t["name"], "unit": t["unit"], "category": t["category"]} for t in lab_parser.catalogue()]


# ------------------------------------------------------------------ trends + compare
@router.get("/trends")
def trends(user: User = Depends(current_user), db: Session = Depends(get_db)):
    reps = db.scalars(select(LabReport).where(LabReport.user_id == user.id, LabReport.status == "final")).all()
    series: dict[str, dict] = {}
    for r in reps:
        date = r.report_date or f"{r.created_at:%Y-%m-%d}"
        for x in r.results:
            s = series.setdefault(x["test_id"], {"test_id": x["test_id"], "name": x["name"], "unit": x["unit"],
                                                 "category": x["category"], "points": []})
            s["points"].append({"date": date, "value": x["value"], "status": x["status"], "ref_low": x["ref_low"],
                                "ref_high": x["ref_high"], "report_id": r.id, "filename": r.filename})
    out = []
    for s in series.values():
        s["points"].sort(key=lambda p: (p["date"], p["report_id"]))
        if len(s["points"]) >= 2:
            a, b = s["points"][-2], s["points"][-1]
            s["change"] = round(b["value"] - a["value"], 4)
            mid = None
            if b["ref_low"] is not None and b["ref_high"] is not None:
                mid = (b["ref_low"] + b["ref_high"]) / 2
            elif b["ref_high"] is not None:
                mid = b["ref_high"] * 0.5
            elif b["ref_low"] is not None:
                mid = b["ref_low"] * 2
            s["direction"] = "same" if b["value"] == a["value"] else (
                "better" if mid is not None and abs(b["value"] - mid) < abs(a["value"] - mid) else "worse" if mid is not None else "changed")
        out.append(s)
    # changed values first, then abnormal ones, then by number of results
    out.sort(key=lambda s: (s.get("direction") in (None, "same"), s["points"][-1]["status"] == "normal", -len(s["points"]), s["name"]))
    return out


# ------------------------------------------------------------------ MRI
def _scan_out(s: MriScan, full: bool = True) -> dict:
    from ..agent.subagents import MRI_DISPLAY

    out = {"id": s.id, "filename": s.filename, "available": s.available, "label": s.label, "display": MRI_DISPLAY.get(s.label, ""),
           "confidence": s.confidence, "created_at": s.created_at.isoformat()}
    if full:
        out.update(probabilities=s.probabilities, heatmap_png=s.heatmap_png, image_png=s.image_png,
                   message="" if s.available else "The MRI classifier was not installed when this scan was uploaded.")
    return out


@router.post("/mri")
async def upload_mri(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"upload:{user.id}", limit=15)
    data = await _read(file)
    try:
        res = mri.analyse(data, file.filename or "")
    except mri.MriError as e:
        raise HTTPException(422, str(e)) from e
    scan = MriScan(user_id=user.id, filename=sanitize(file.filename or "scan", 200), available=res.available, label=res.label,
                   confidence=res.confidence, probabilities=res.probabilities, heatmap_png=res.heatmap_png,
                   image_png=res.image_png, explanations={})
    db.add(scan)
    db.commit()
    if res.available:
        memory.remember(db, user.id, "mri", f"Brain MRI '{scan.filename}' on {scan.created_at:%d %b %Y}: model suggested {res.display} ({res.confidence:.0%}).")
    return {**_scan_out(scan), "message": res.message}


@router.get("/mri")
def list_scans(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(MriScan).where(MriScan.user_id == user.id).order_by(MriScan.id.desc()).limit(30)).all()
    return [_scan_out(s, False) for s in rows]


@router.get("/mri/{mri_id}")
def get_scan(mri_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _scan_out(_own_scan(db, user, mri_id))


@router.delete("/mri/{mri_id}")
def delete_scan(mri_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_scan(db, user, mri_id))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ PDF export
class ExportIn(BaseModel):
    report_id: int | None = None
    mri_id: int | None = None
    language: str = Field(default="en", pattern="^(en|hi|kok)$")


@router.post("/exports")
def create_export(body: ExportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"export:{user.id}", limit=10)
    if body.report_id:
        _own_report(db, user, body.report_id)
    if body.mri_id:
        _own_scan(db, user, body.mri_id)
    try:
        exp = export_service.make_pdf(db, user, body.report_id, body.mri_id, body.language)
    except export_service.ExportError as e:
        raise HTTPException(422, str(e)) from e
    return {"token": exp.token, "filename": exp.filename, "language": body.language}


@router.get("/exports/{token}")
def download_export(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    exp = db.scalar(select(Export).where(Export.token == token))
    ttl = timedelta(hours=get_settings().export_ttl_hours)
    if exp is None or exp.user_id != user.id:
        raise HTTPException(404, "Not found")
    created = exp.created_at if exp.created_at.tzinfo else exp.created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > ttl:
        raise HTTPException(410, "This PDF link has expired. Please generate it again.")
    return Response(exp.pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{exp.filename}"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ home dashboard
@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    reps = db.scalars(select(LabReport).where(LabReport.user_id == user.id).order_by(LabReport.id.desc())).all()
    scans = db.scalars(select(MriScan).where(MriScan.user_id == user.id).order_by(MriScan.id.desc()).limit(3)).all()
    chats = db.scalar(select(func.count(func.distinct(ChatMessage.thread_id))).where(ChatMessage.user_id == user.id)) or 0
    lt = memory.long_term(db, user.id)
    latest = next((r for r in reps if r.status == "final"), None)
    tr = [t for t in trends(user, db) if len(t["points"]) >= 2][:4]
    return {
        "counts": {"reports": len(reps), "scans": db.scalar(select(func.count()).select_from(MriScan).where(MriScan.user_id == user.id)) or 0,
                   "chats": chats, "needs_review": sum(1 for r in reps if r.status == "needs_review")},
        "latest_report": _report_out(latest) if latest else None,
        "recent_reports": [_report_out(r, False) for r in reps[:5]],
        "recent_scans": [_scan_out(s, False) for s in scans],
        "trends": tr,
        "memory_on": lt["consent"],
    }


@router.get("/reports/{report_id}/history")
def report_history(report_id: int, before: int = Query(default=1, ge=1, le=5), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The previous report(s) for side-by-side comparison."""
    cur = _own_report(db, user, report_id)
    others = db.scalars(select(LabReport).where(LabReport.user_id == user.id, LabReport.id != cur.id, LabReport.status == "final")).all()
    key = cur.report_date or f"{cur.created_at:%Y-%m-%d}"
    mine = {x["test_id"] for x in cur.results}
    older = sorted([o for o in others if (o.report_date or f"{o.created_at:%Y-%m-%d}") <= key],
                   key=lambda o: (o.report_date or f"{o.created_at:%Y-%m-%d}", o.id), reverse=True)
    # prefer the latest earlier report that measured the same tests (a CBC is not compared with a lipid panel)
    shared = [o for o in older if mine & {x["test_id"] for x in o.results}]
    return [_report_out(o) for o in (shared or older)[:before]]
