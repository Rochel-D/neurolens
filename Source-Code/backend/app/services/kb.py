"""Knowledge base — lab tests and knowledge notes.

The JSON files in backend/data are the seed. Admin edits are stored as overrides in the database
(table kb_overrides) and layered on top, so "Reset to default" is always possible.

Public interface:
    lab_tests() -> list[dict]       notes() -> list[dict]
    save(db, kind, item)            delete(db, kind, item_id)        reset(db, kind, item_id)
    validate_lab(item) / validate_note(item)  -> cleaned dict or ValueError
Callers that cache derived data (parser regexes, RAG index) register with on_change().
"""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

DATA = Path(__file__).resolve().parents[2] / "data"
_lock = threading.Lock()
_cache: dict[str, list[dict]] = {}
_listeners: list[Callable[[], None]] = []
_SLUG = re.compile(r"^[a-z0-9_]{2,60}$")


def on_change(fn: Callable[[], None]) -> None:
    _listeners.append(fn)


def invalidate() -> None:
    with _lock:
        _cache.clear()
    for fn in _listeners:
        fn()


def _overrides(kind: str) -> list:
    try:
        from ..db import SessionLocal
        from ..models import KBOverride

        with SessionLocal() as db:
            return db.scalars(select(KBOverride).where(KBOverride.kind == kind).order_by(KBOverride.id)).all()
    except SQLAlchemyError:  # table not created yet (e.g. evals before app start)
        return []


def _merged(kind: str, seed_file: str) -> list[dict]:
    with _lock:
        if kind in _cache:
            return _cache[kind]
    items = {d["id"]: {**d, "_custom": False} for d in json.loads((DATA / seed_file).read_text(encoding="utf-8"))}
    for o in _overrides(kind):
        if o.deleted:
            items.pop(o.item_id, None)
        else:
            items[o.item_id] = {**o.data, "id": o.item_id, "_custom": True}
    out = list(items.values())
    with _lock:
        _cache[kind] = out
    return out


def lab_tests() -> list[dict]:
    return _merged("lab", "lab_tests.json")


def notes() -> list[dict]:
    return _merged("note", "knowledge.json")


def seed_ids(kind: str) -> set[str]:
    f = "lab_tests.json" if kind == "lab" else "knowledge.json"
    return {d["id"] for d in json.loads((DATA / f).read_text(encoding="utf-8"))}


# ------------------------------------------------------------------ validation
def _num(v, name: str, allow_none: bool = True):
    if v in (None, ""):
        if allow_none:
            return None
        raise ValueError(f"{name} is required")
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a number") from e


def validate_lab(d: dict) -> dict:
    tid = str(d.get("id", "")).strip().lower()
    if not _SLUG.match(tid):
        raise ValueError("id must be 2–60 lowercase letters, digits or _")
    name = str(d.get("name", "")).strip()
    if len(name) < 2:
        raise ValueError("name is required")
    aliases = [a.strip().lower() for a in (d.get("aliases") or []) if str(a).strip()]
    if not aliases:
        raise ValueError("at least one alias is required (the words printed on reports)")
    if any(len(a) < 2 for a in aliases):
        raise ValueError("aliases must be at least 2 characters")
    low, high = _num(d.get("low"), "low"), _num(d.get("high"), "high")
    if low is not None and high is not None and low >= high:
        raise ValueError("low must be smaller than high")
    sex = {}
    for k in ("male", "female"):
        v = (d.get("sex") or {}).get(k)
        if v:
            lo, hi = _num(v[0], f"{k} low", False), _num(v[1], f"{k} high", False)
            if lo >= hi:
                raise ValueError(f"{k} range: low must be smaller than high")
            sex[k] = [lo, hi]
    conv = {}
    for unit, f in (d.get("conversions") or {}).items():
        if str(unit).strip():
            conv[str(unit).strip().lower()] = _num(f, f"conversion for {unit}", False)
    out = {
        "id": tid, "name": name, "aliases": list(dict.fromkeys(aliases)), "unit": str(d.get("unit", "")).strip(),
        "low": low, "high": high, "critical_low": _num(d.get("critical_low"), "critical_low"),
        "critical_high": _num(d.get("critical_high"), "critical_high"), "category": str(d.get("category") or "Other").strip(),
        "conversions": conv,
    }
    if sex:
        out["sex"] = sex
    for k in ("what", "low_means", "high_means"):
        v = str(d.get(k, "")).strip()
        if len(v) < 10:
            raise ValueError(f"{k} needs a plain-language sentence")
        out[k] = v[:800]
    return out


def validate_note(d: dict) -> dict:
    nid = str(d.get("id", "")).strip().lower()
    if not _SLUG.match(nid):
        raise ValueError("id must be 2–60 lowercase letters, digits or _")
    title = str(d.get("title", "")).strip()
    text = str(d.get("text", "")).strip()
    module = d.get("module", "general")
    if len(title) < 3:
        raise ValueError("title is required")
    if len(text) < 40:
        raise ValueError("text must be at least 40 characters")
    if module not in ("neuro", "general"):
        raise ValueError("module must be neuro or general")
    return {"id": nid, "title": title[:120], "text": text[:3000], "module": module}


# ------------------------------------------------------------------ writes
def _row(db, kind: str, item_id: str):
    from ..models import KBOverride

    return db.scalar(select(KBOverride).where(KBOverride.kind == kind, KBOverride.item_id == item_id))


def save(db, kind: str, item: dict) -> dict:
    from ..models import KBOverride

    clean = validate_lab(item) if kind == "lab" else validate_note(item)
    if kind == "lab":  # an alias may belong to one test only
        for t in lab_tests():
            if t["id"] != clean["id"]:
                clash = set(clean["aliases"]) & set(t["aliases"])
                if clash:
                    raise ValueError(f"alias '{sorted(clash)[0]}' is already used by {t['name']}")
    row = _row(db, kind, clean["id"]) or KBOverride(kind=kind, item_id=clean["id"])
    row.data, row.deleted = clean, False
    db.add(row)
    db.commit()
    invalidate()
    return clean


def delete(db, kind: str, item_id: str) -> None:
    from ..models import KBOverride

    row = _row(db, kind, item_id)
    if item_id in seed_ids(kind):
        row = row or KBOverride(kind=kind, item_id=item_id, data={})
        row.deleted = True
        db.add(row)
    elif row is not None:
        db.delete(row)
    db.commit()
    invalidate()


def reset(db, kind: str, item_id: str) -> None:
    row = _row(db, kind, item_id)
    if row is not None:
        db.delete(row)
        db.commit()
    invalidate()
