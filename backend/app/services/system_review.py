"""System Review — deterministic health checks of the live database and configuration.
Not an AI model: the same state always gives the same pass/warn/fail results."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LabReport, MemoryItem, MriScan, Trace, User, UserProfile
from . import explainer, lab_parser, mri, rag
from .model_router import get_router


def _c(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def run(db: Session) -> dict:
    checks: list[dict] = []
    s = get_settings()

    users = db.scalars(select(User)).all()
    bad = [u.email for u in users if not u.password_hash.startswith("$2")]
    checks.append(_c("Password hashing", "fail" if bad else "pass", f"{len(bad)} non-bcrypt hashes" if bad else "all passwords are bcrypt hashes"))

    no_consent = select(UserProfile.user_id).where(UserProfile.memory_consent.is_(False))
    leaked = db.scalar(select(func.count()).select_from(MemoryItem).where(MemoryItem.user_id.in_(no_consent)))
    no_profile = db.scalar(select(func.count()).select_from(MemoryItem).where(MemoryItem.user_id.not_in(select(UserProfile.user_id))))
    total_leak = (leaked or 0) + (no_profile or 0)
    checks.append(_c("Memory consent respected", "fail" if total_leak else "pass",
                     f"{total_leak} memory items belong to users without consent" if total_leak else "long-term memory only for consenting users"))

    orphans = db.scalar(select(func.count()).select_from(LabReport).where(LabReport.user_id.not_in(select(User.id)))) or 0
    orphans += db.scalar(select(func.count()).select_from(MriScan).where(MriScan.user_id.not_in(select(User.id)))) or 0
    checks.append(_c("Ownership integrity", "fail" if orphans else "pass", f"{orphans} records without an owner" if orphans else "every report and scan has an owner"))

    cat = lab_parser.catalogue()
    broken = [t["id"] for t in cat if t.get("low") is not None and t.get("high") is not None and t["low"] >= t["high"]]
    checks.append(_c("Reference range table", "fail" if broken else "pass", f"invalid ranges: {broken}" if broken else f"{len(cat)} lab tests with valid ranges"))


    router = get_router()
    st = router.status()
    ready = sum(1 for x in st if x["status"] == "ready")
    # v2.1: "ready" only means "not cooling down". A key that has failed and never answered is not healthy.
    failing = [x["slot"] for x in st if x["failures"] and not x["healthy"]]
    answered = [x["slot"] for x in st if x["successes"]]
    level = "fail" if ready == 0 else "warn" if (ready == 1 or failing) else "pass"
    detail = f"{ready}/{len(st)} keys ready (Gemini×{len(s.gemini_keys)}, Grok/Groq×{len(s.grok_keys)})"
    if answered:
        detail += f"; answered: {', '.join(answered)}"
    if failing:
        detail += f"; failing: {', '.join(failing)} — use Test on the API keys page"
    checks.append(_c("LLM key pool", level, detail + ("" if st else " — app runs in offline template mode")))

    checks.append(_c("Serper web search", "pass" if s.serper_api_key else "warn", "key configured" if s.serper_api_key else "no SERPER_API_KEY — web fallback disabled"))

    ms = mri.model_status()
    checks.append(_c("MRI classifier", "pass" if ms["loaded"] else "warn", "EfficientNet-B0 weights loaded" if ms["loaded"] else f"not loaded: {ms['error']}"))

    ls = explainer.local_status()
    checks.append(_c("Fine-tuned explainer", "pass" if ls["mode"] == "local" and ls["sft_adapter"] else "warn",
                     f"local Qwen + adapters ({ls['sft_adapter']}, {ls['dpo_adapter']})" if ls["mode"] == "local" else "EXPLAINER_MODE=router — patient view uses the router LLM with the training prompt"))

    from pathlib import Path
    import shutil

    fonts = Path(__file__).resolve().parents[2] / "data" / "fonts"
    have_fonts = all((fonts / f).exists() for f in ("NotoSans-Regular.ttf", "NotoSansDevanagari-Regular.ttf"))
    try:
        import uharfbuzz  # noqa: F401
        shaping = True
    except ImportError:
        shaping = False
    checks.append(_c("PDF export", "pass" if have_fonts and shaping else "warn",
                     "Noto fonts + HarfBuzz shaping ready (Hindi renders correctly)" if have_fonts and shaping
                     else "missing fonts or uharfbuzz — Hindi text in PDFs may look broken (pip install uharfbuzz)"))
    engines = [e.strip() for e in s.ocr_engines.split(",") if e.strip()]
    ocr_ok = ("gemini" in engines and any(x["provider"] == "gemini" for x in st)) or ("tesseract" in engines and shutil.which("tesseract"))
    checks.append(_c("Photo reports (OCR)", "pass" if ocr_ok else "warn",
                     f"engines: {', '.join(engines)}; tesseract {'installed' if shutil.which('tesseract') else 'not installed'}"))
    from ..models import KBOverride

    edits = db.scalar(select(func.count()).select_from(KBOverride)) or 0
    checks.append(_c("Knowledge base", "pass", f"{len(lab_parser.catalogue())} lab tests, {len(rag.corpus())} RAG documents, {edits} admin edits"))

    since = datetime.now(timezone.utc) - timedelta(days=1)
    recent = db.scalars(select(Trace).where(Trace.created_at >= since)).all()
    errs = sum(1 for t in recent if t.status == "error")
    unverified = sum(1 for t in recent if not t.verified)
    rate = errs / len(recent) if recent else 0
    checks.append(_c("Agent error rate (24h)", "pass" if rate < 0.05 else "warn" if rate < 0.2 else "fail", f"{errs}/{len(recent)} runs errored"))
    checks.append(_c("Verifier fallbacks (24h)", "pass" if unverified == 0 else "warn", f"{unverified} answers needed the safe template"))

    blocked = sum(1 for t in recent if t.status == "blocked")
    checks.append(_c("Guardrail activity (24h)", "pass", f"{blocked} messages stopped by input guardrails"))

    checks.append(_c("JWT secret", "pass" if len(s.jwt_secret) >= 32 and not s.jwt_secret.startswith("dev-only") else "warn",
                     "strong secret set" if not s.jwt_secret.startswith("dev-only") else "using the development secret — set JWT_SECRET"))

    order = {"fail": 2, "warn": 1, "pass": 0}
    overall = max((c["status"] for c in checks), key=lambda x: order[x])
    return {"overall": overall, "checks": checks}
