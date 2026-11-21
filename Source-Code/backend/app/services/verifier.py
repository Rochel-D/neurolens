"""Verifier — independent PASS/FAIL check of an explanation before it is shown.

It never trusts the generator. It compares the answer with the structured facts the tools produced:
* Lab: every number that looks like a result must match a parsed value or range; a test called
  "high"/"low"/"normal" in the answer must have that status in the parsed results.
* MRI: the class named must be the predicted class; low confidence must be stated as uncertain.
* Sources: cited [n] markers must exist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lab_parser


@dataclass
class VerifyResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    checks: int = 0

    def dict(self) -> dict:
        return {"passed": self.passed, "issues": self.issues, "checks": self.checks}


_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.]*%?\s*of)")
_WORDS = {
    "high": ("high", "critical_high"),
    "raised": ("high", "critical_high"),
    "elevated": ("high", "critical_high"),
    "above": ("high", "critical_high"),
    "low": ("low", "critical_low"),
    "below": ("low", "critical_low"),
    "normal": ("normal",),
    "within": ("normal",),
}
# numbers that legitimately appear in explanations without being lab values
_ALLOWED_NUMBERS = {"1", "2", "3", "4", "5", "6", "7", "8", "10", "12", "24", "95", "100", "112", "14416", "2026"}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.051, abs(b) * 0.01)


def verify_lab(answer: str, results: list[dict], facts: str = "") -> VerifyResult:
    """Every number in the answer must come from the parsed results or the grounded facts."""
    issues: list[str] = []
    checks = 0
    known: list[float] = []
    for r in results:
        known.append(r["value"])
        for k in ("ref_low", "ref_high"):
            if r.get(k) is not None:
                known.append(r[k])
    # clinical thresholds from the knowledge base are allowed too (e.g. HbA1c 6.5, glucose 126)
    kb_text = " ".join([t["low_means"] + " " + t["high_means"] for t in lab_parser.catalogue()]) + " " + facts
    kb_numbers = {float(n) for n in _NUM.findall(kb_text)} | {float(len(results))}

    for m in _NUM.finditer(answer):
        s = m.group(1)
        if s in _ALLOWED_NUMBERS:
            continue
        x = float(s)
        checks += 1
        if not any(_close(x, k) for k in known) and x not in kb_numbers:
            issues.append(f"Number {s} does not match any parsed value or reference range")

    by_name = {}
    for r in results:
        t = lab_parser.by_id().get(r["test_id"], {})
        for alias in [r["name"], *t.get("aliases", [])]:
            if len(alias) >= 3:
                by_name[alias.lower()] = r
    for sentence in re.split(r"(?<=[.!?\n])\s+", answer):
        low = sentence.lower()
        for alias, r in sorted(by_name.items(), key=lambda kv: -len(kv[0])):
            if not re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", low):
                continue
            # look at status words in the few words right after the test name
            after = low.split(alias, 1)[1][:60]
            # the first number right after a test name must be THAT test's value, range or its own thresholds
            t = lab_parser.by_id()[r["test_id"]]
            own = [r["value"], *(x for x in (r.get("ref_low"), r.get("ref_high")) if x is not None)]
            own += [float(n) for n in _NUM.findall(t["low_means"] + " " + t["high_means"])]
            nm = _NUM.search(after[:40])
            if nm and nm.group(1) not in _ALLOWED_NUMBERS:
                checks += 1
                if not any(_close(float(nm.group(1)), k) for k in own):
                    issues.append(f"{r['name']} given as {nm.group(1)} but the report says {r['value']:g}")
            for word, statuses in _WORDS.items():
                if re.search(rf"\b(is|are|was|looks|reads|slightly|quite|very)?\s*{word}\b", after[:35]):
                    checks += 1
                    if r["status"] not in statuses:
                        issues.append(f"{r['name']} described as '{word}' but parsed status is {r['status']}")
                    break
            break
    return VerifyResult(not issues, issues, checks)


def verify_mri(answer: str, prediction: dict, min_conf: float) -> VerifyResult:
    issues: list[str] = []
    low = answer.lower()
    label = (prediction.get("label") or "").lower()
    names = {"glioma": "glioma", "meningioma": "meningioma", "pituitary": "pituitary", "notumor": "no tumour"}
    checks = 0
    for key, word in names.items():
        if key == "notumor":
            continue
        if re.search(rf"\b(likely|suggests?|consistent with|shows?|predicted)\b[^.]{{0,40}}\b{word}", low):
            checks += 1
            if label != key:
                issues.append(f"Answer points to {word} but the classifier predicted {label}")
    conf = float(prediction.get("confidence", 0))
    if conf < min_conf:
        checks += 1
        if not re.search(r"uncertain|not confident|low confidence|unsure|cannot be sure|not sure", low):
            issues.append(f"Confidence {conf:.0%} is below {min_conf:.0%} but the answer does not say it is uncertain")
    return VerifyResult(not issues, issues, checks)


def verify_citations(answer: str, n_sources: int) -> VerifyResult:
    issues = []
    cited = {int(x) for x in re.findall(r"[\[【](\d{1,2})[\]】]", answer)}
    for c in cited:
        if c < 1 or c > n_sources:
            issues.append(f"Citation [{c}] does not match any retrieved source")
    return VerifyResult(not issues, issues, len(cited))


def merge(*results: VerifyResult) -> VerifyResult:
    issues = [i for r in results for i in r.issues]
    return VerifyResult(not issues, issues, sum(r.checks for r in results))
