"""Lab report parser — deep module.

Public interface:
    extract_text(data: bytes, filename: str) -> str
    parse_report(text: str, sex: str | None = None) -> list[LabResult]
    summarise(results) -> dict

Hidden inside: alias matching, number/range extraction, unit conversion,
report-printed vs standard ranges, sex-specific ranges, critical-value detection.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache

from . import kb
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "lab_tests.json"


class ReportError(ValueError):
    pass


@dataclass
class LabResult:
    test_id: str
    name: str
    value: float
    unit: str
    ref_low: float | None
    ref_high: float | None
    range_source: str  # report | standard
    status: str  # low | normal | high | critical_low | critical_high
    category: str
    raw: str
    suspect: bool = False  # value is >50× outside its own printed range → probably a unit/reading error

    def dict(self) -> dict:
        return asdict(self)


def catalogue() -> list[dict]:
    """Seed tests + admin edits (see services/kb.py)."""
    return kb.lab_tests()


@lru_cache
def by_id() -> dict[str, dict]:
    return {t["id"]: t for t in catalogue()}


@lru_cache
def _alias_patterns() -> list[tuple[re.Pattern, str]]:
    pairs = [(a, t["id"]) for t in catalogue() for a in t["aliases"]]
    pairs.sort(key=lambda p: -len(p[0]))  # longest alias first: "ldl cholesterol" before "cholesterol"
    return [(re.compile(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", re.I), tid) for a, tid in pairs]


# A number is either digit-grouped ("2,50,000" Indian / "250,000" Western) or a plain/decimal number ("4.5", "4,5").
_GROUPED = r"\d{1,3}(?:,\d{2,3})*,\d{3}(?!\d)(?:\.\d+)?"
_NUM = rf"({_GROUPED}|\d+(?:[.,]\d+)?)"
_COUNT_TESTS = {"wbc", "platelets"}  # reported either as ×10³/µL or as raw cells per cumm
_RANGE = re.compile(rf"{_NUM}\s*(?:-|–|to)\s*{_NUM}")
_LT = re.compile(rf"(?:<|less than|upto|up to)\s*{_NUM}", re.I)
_GT = re.compile(rf"(?:>|more than|greater than)\s*{_NUM}", re.I)
_VALUE = re.compile(rf"(?<![\w.]){_NUM}")
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def _f(s: str) -> float:
    if re.fullmatch(_GROUPED, s):  # thousands separators → drop them
        return float(s.replace(",", ""))
    return float(s.replace(",", "."))


# ---------------------------------------------------------------- text extraction
def extract_text(data: bytes, filename: str, max_mb: int = 10, max_pages: int = 30) -> str:
    if len(data) > max_mb * 1024 * 1024:
        raise ReportError(f"File is larger than {max_mb} MB")
    name = (filename or "").lower()
    if data[:5] == b"%PDF-":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(io.BytesIO(data))
        except PdfReadError as e:
            raise ReportError("This PDF could not be read") from e
        if reader.is_encrypted:
            raise ReportError("Password-protected PDFs are not supported")
        if len(reader.pages) > max_pages:
            raise ReportError(f"PDF has more than {max_pages} pages")
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        if len(text.strip()) < 20:
            raise ReportError("No text found in the PDF. It may be a scanned image — try a text PDF or paste the values")
        return text
    if name.endswith(".pdf"):
        raise ReportError("File has a .pdf name but is not a valid PDF")
    if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        try:
            import pytesseract
            from PIL import Image

            return pytesseract.image_to_string(Image.open(io.BytesIO(data)))
        except ImportError as e:
            raise ReportError("Photo reports need OCR (pip install pytesseract + Tesseract). Upload a PDF or text file instead") from e
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# ---------------------------------------------------------------- parsing
def _match_test(line: str) -> tuple[str, int] | None:
    for pat, tid in _alias_patterns():
        m = pat.search(line)
        if m:
            return tid, m.end()
    return None


def _unit_factor(test: dict, line: str) -> tuple[float, str]:
    low = re.sub(r"\s*/\s*", "/", line.lower().replace("μ", "µ"))  # "lakhs / cumm" → "lakhs/cumm"
    for unit, factor in sorted(test.get("conversions", {}).items(), key=lambda kv: -len(kv[0])):
        if unit in low and test["unit"].lower() not in low:
            return factor, unit
    return 1.0, test["unit"]


def _standard_range(test: dict, sex: str | None) -> tuple[float | None, float | None]:
    if sex and sex in test.get("sex", {}):
        lo, hi = test["sex"][sex]
        return lo, hi
    return test.get("low"), test.get("high")


def _status(value: float, lo: float | None, hi: float | None, test: dict) -> str:
    cl, ch = test.get("critical_low"), test.get("critical_high")
    if cl is not None and value < cl:
        return "critical_low"
    if ch is not None and value > ch:
        return "critical_high"
    if lo is not None and value < lo:
        return "low"
    if hi is not None and value > hi:
        return "high"
    return "normal"


def parse_line(line: str, sex: str | None = None) -> LabResult | None:
    hit = _match_test(line)
    if not hit:
        return None
    tid, end = hit
    test = by_id()[tid]
    if "ratio" in line.lower():
        return None
    rest = line[end:]
    rest_wo_range = _RANGE.sub(" ", _PARENS.sub(" ", rest))
    rest_wo_range = _LT.sub(" ", rest_wo_range)
    rest_wo_range = _GT.sub(" ", rest_wo_range)
    vm = _VALUE.search(rest_wo_range)
    if not vm:
        return None
    raw_value = _f(vm.group(1))
    factor, unit_seen = _unit_factor(test, rest)
    if tid in _COUNT_TESTS:
        # Blood counts are printed three ways on Indian reports: ×10³/µL (7.8, 260), cells/cumm (7800, 2,50,000)
        # and lakh/cumm (2.5). Value and range can even use different ones on the same line, so each number is
        # scaled on its own: ≥1000 → cells/µL; "lakh" on the line and < 50 → lakhs; otherwise already ×10³/µL.
        lakh = "lakh" in rest.lower()
        factor_for = lambda n: 0.001 if n >= 1000 else (100.0 if lakh and n < 50 else 1.0)  # noqa: E731
    else:
        factor_for = lambda n: factor  # noqa: E731
    value = round(raw_value * factor_for(raw_value), 3)

    rm = _RANGE.search(rest)
    lt = _LT.search(rest)
    gt = _GT.search(rest)
    if rm:
        a, b = _f(rm.group(1)), _f(rm.group(2))
        lo, hi = a * factor_for(a), b * factor_for(b)
        src = "report"
    elif lt:
        a = _f(lt.group(1))
        lo, hi, src = None, a * factor_for(a), "report"
    elif gt:
        a = _f(gt.group(1))
        lo, hi, src = a * factor_for(a), None, "report"
    else:
        lo, hi = _standard_range(test, sex)
        src = "standard"
    if lo is not None and hi is not None and lo >= hi:  # nonsense printed range → ignore it
        lo, hi = _standard_range(test, sex)
        src = "standard"
    lo = round(lo, 3) if lo is not None else None
    hi = round(hi, 3) if hi is not None else None
    suspect = bool(value > 0 and ((lo and value < lo / 50) or (hi and value > hi * 50)))
    return LabResult(
        suspect=suspect,
        test_id=tid,
        name=test["name"],
        value=value,
        unit=test["unit"],
        ref_low=lo,
        ref_high=hi,
        range_source=src,
        status=_status(value, lo, hi, test),
        category=test["category"],
        raw=line.strip()[:160],
    )


def parse_report(text: str, sex: str | None = None) -> list[LabResult]:
    sex = (sex or "").lower() or None
    sex = sex if sex in ("male", "female") else None
    seen: dict[str, LabResult] = {}
    for line in text.splitlines():
        if len(line.strip()) < 3:
            continue
        r = parse_line(line, sex)
        if r and r.test_id not in seen:  # first occurrence wins
            seen[r.test_id] = r
    return list(seen.values())


def summarise(results: list[LabResult]) -> dict:
    flagged = [r for r in results if r.status != "normal"]
    critical = [r for r in results if r.status.startswith("critical")]
    return {
        "total": len(results),
        "normal": len(results) - len(flagged),
        "flagged": len(flagged),
        "critical": len(critical),
        "flagged_tests": [r.name for r in flagged],
        "suspect_tests": [r.name for r in results if getattr(r, "suspect", False)],
        "critical_tests": [r.name for r in critical],
        "categories": sorted({r.category for r in results}),
    }


def describe(test_id: str) -> dict:
    t = by_id()[test_id]
    return {k: t[k] for k in ("name", "unit", "what", "low_means", "high_means", "category")}


def _clear_caches() -> None:
    by_id.cache_clear()
    _alias_patterns.cache_clear()


kb.on_change(_clear_caches)


def status_for(test_id: str, value: float, lo: float | None, hi: float | None) -> str:
    """Status of an edited value (used by the photo-report review step)."""
    return _status(value, lo, hi, by_id()[test_id])


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[\s-]+([A-Za-z]{3})[a-z]*[\s,-]+(\d{4})\b"), "dMy"),
]
_DATE_HINT = re.compile(r"date|collected|collection|reported|sample|received|registered", re.I)


def report_date(text: str) -> str:
    """Best guess of the report date (YYYY-MM-DD) from text; lines that mention 'date/collected' win."""
    from datetime import date

    def scan(lines):
        for line in lines:
            for pat, kind in _DATE_PATTERNS:
                for m in pat.finditer(line):
                    try:
                        if kind == "dmy":
                            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        elif kind == "ymd":
                            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        else:
                            d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2)[:3].lower(), 0), int(m.group(3))
                        if 1990 <= y <= 2100:
                            return date(y, mo, d).isoformat()
                    except ValueError:
                        continue
        return ""

    lines = (text or "").splitlines()
    return scan([ln for ln in lines if _DATE_HINT.search(ln)]) or scan(lines)
