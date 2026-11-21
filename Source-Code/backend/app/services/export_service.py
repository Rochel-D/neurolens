"""Builds PDF exports for a report and/or an MRI scan.

Explanations for the PDF go through the same safety path as chat answers: grounded facts →
explainer → verifier (one rewrite with feedback) → safe template if still failing → output
guardrails. They are cached per report so a second download is instant.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..agent import subagents
from ..config import get_settings
from ..models import Export, LabReport, MriScan, User
from . import guardrails, memory, pdf_export, translate, verifier
from .explainer import explain

QUESTIONS = {
    ("lab", "patient"): "Please explain my blood report in simple words.",
    ("lab", "doctor"): "Summarise this report for my doctor.",
    ("mri", "patient"): "What does this MRI result mean, in simple words?",
    ("mri", "doctor"): "Summarise this MRI classifier result for a clinician.",
}


class ExportError(ValueError):
    pass


def verified_text(kind: str, audience: str, finding: dict) -> tuple[str, str]:
    """Explain + verify (+1 retry) + template fallback. Returns (text, engine)."""
    question = QUESTIONS[(kind, audience)]
    template = finding.get("template_doctor") if audience == "doctor" and finding.get("template_doctor") else finding["template"]
    feedback = ""
    for attempt in range(2):
        e = explain(question, finding["facts"], audience, finding["sources"], feedback=feedback,
                    allow_local=attempt == 0, template=template)
        if e.engine.startswith("template"):
            return guardrails.check_output(e.text, add_disclaimer=False).text, e.engine
        checks = [verifier.verify_citations(e.text, len(finding["sources"]))]
        data = finding["data"]
        if kind == "lab":
            checks.append(verifier.verify_lab(e.text, data.get("results", []), finding["facts"]))
        elif data.get("prediction"):
            checks.append(verifier.verify_mri(e.text, data["prediction"], get_settings().mri_min_confidence))
        v = verifier.merge(*checks)
        if v.passed:
            return guardrails.check_output(e.text, add_disclaimer=False).text, e.engine
        feedback = "\n".join(v.issues)
    return guardrails.check_output(template, add_disclaimer=False).text, "template (verifier fallback)"


def _texts(db: Session, obj, kind: str, finding: dict, lang: str) -> dict:
    cache = dict(obj.explanations or {})
    changed = False
    for aud in ("patient", "doctor"):
        if f"{aud}:en" not in cache:
            cache[f"{aud}:en"], cache[f"engine:{aud}"] = verified_text(kind, aud, finding)
            changed = True
    if lang != "en" and f"patient:{lang}" not in cache:
        out, note = translate.translate(cache["patient:en"], lang)
        if out:
            cache[f"patient:{lang}"] = out
            changed = True
        else:
            cache[f"note:{lang}"] = note  # not cached as success → retried next time
    if changed:
        obj.explanations = {k: v for k, v in cache.items() if not k.startswith("note:")}
        db.commit()
    return cache


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name.rsplit(".", 1)[0]).strip("-")[:40] or "report"


def make_pdf(db: Session, user: User, report_id: int | None = None, mri_id: int | None = None, lang: str = "en") -> Export:
    if lang not in translate.LANGS:
        lang = "en"
    report = db.get(LabReport, report_id) if report_id else None
    scan = db.get(MriScan, mri_id) if mri_id else None
    if report is not None and report.user_id != user.id:
        report = None
    if scan is not None and scan.user_id != user.id:
        scan = None
    if report is None and scan is None:
        raise ExportError("Attach or upload a report or an MRI scan first, then ask for the PDF.")
    if report is not None and report.status == "needs_review":
        raise ExportError("Please check and confirm the values read from your photo first (Reports page → Confirm values).")

    texts_r, texts_m, sources = {}, {}, []
    rep_dict = scan_dict = None
    if report is not None:
        finding = subagents.lab_agent(db, user.id, report.id, QUESTIONS[("lab", "patient")])
        texts_r = _texts(db, report, "lab", finding, lang)
        sources += finding["sources"]
        rep_dict = {"filename": report.filename, "report_date": report.report_date, "source": report.source,
                    "summary": report.summary, "results": report.results}
    if scan is not None:
        cached = "patient:en" in (scan.explanations or {})
        finding = subagents.neuro_agent(db, user.id, scan.id, [], research=not cached)
        texts_m = _texts(db, scan, "mri", finding, lang) if scan.available else {}
        sources += finding["sources"]
        scan_dict = {"available": scan.available, "label": scan.label, "display": subagents.MRI_DISPLAY.get(scan.label, scan.label),
                     "confidence": scan.confidence, "probabilities": scan.probabilities, "heatmap_png": scan.heatmap_png,
                     "image_png": scan.image_png, "filename": scan.filename, "created": f"{scan.created_at:%d %b %Y}"}
    profile = memory.long_term(db, user.id)["profile"]
    seen, uniq = set(), []
    for s in sources:
        if s.get("title") not in seen:
            seen.add(s.get("title"))
            uniq.append(s)
    pdf = pdf_export.build_pdf(user_name=user.name, profile=profile, report=rep_dict, scan=scan_dict,
                               texts_report=texts_r, texts_mri=texts_m, lang=lang, sources=uniq)
    base = _slug(report.filename if report else scan.filename)
    fname = f"NeuroLens-{base}{'-' + lang if lang != 'en' else ''}.pdf"
    # housekeeping: drop expired exports
    cutoff = datetime.now(timezone.utc) - timedelta(hours=get_settings().export_ttl_hours)
    db.execute(delete(Export).where(Export.created_at < cutoff))
    exp = Export(token=secrets.token_urlsafe(18), user_id=user.id, filename=fname, pdf=pdf)
    db.add(exp)
    db.commit()
    return exp
