"""Translation of verified explanations into Hindi and Konkani (Romi).

The English answer is verified first; translation happens after. To keep the numbers safe, the
translation must contain every number of the English text (same digits). If it does not, or no
model is available, the English text is kept and the user is told.
"""
from __future__ import annotations

import re

from .model_router import AllProvidersFailed, get_router

LANGS = {
    "en": {"name": "English", "native": "English", "speech": "en-IN"},
    "hi": {"name": "Hindi", "native": "हिन्दी", "speech": "hi-IN"},
    "kok": {"name": "Konkani (Romi)", "native": "Konknni (Romi)", "speech": "en-IN"},
}

_RULES = {
    "hi": "Translate into simple, everyday Hindi in Devanagari script (the Hindi people speak, not formal Sanskritised Hindi).",
    "kok": ("Translate into Goan Konkani written in Roman script (Romi Konkani), the way it is written in Goa "
            "newspapers and church notices. Use simple everyday words."),
}
SYSTEM = (
    "You are a careful medical translator for patients. {rule}\n"
    "Rules: translate EVERYTHING, including headings, bold phrases and bullet labels — no English sentences may remain; "
    "keep every number exactly as written, using Western digits 0-9; keep units (mg/dL, g/dL, %, etc.) unchanged; "
    "after a medical test or condition name, keep the English name in brackets the first time, e.g. (HbA1c); keep markdown "
    "(**bold**, bullet points, tables, [1] citations) exactly; do not add, remove or soften any information; "
    "translate the disclaimer too. Output only the translation."
)

_NUM = re.compile(r"\d+(?:\.\d+)?")

DISCLAIMER = {
    "en": "NeuroLens gives general health education, not a diagnosis. Please discuss your results with a qualified doctor.",
    "hi": "NeuroLens सामान्य स्वास्थ्य जानकारी देता है, निदान नहीं। कृपया अपने परिणामों के बारे में किसी योग्य डॉक्टर से बात करें।",
}


def numbers(text: str) -> list[str]:
    return sorted(_NUM.findall(text or ""))


def translate(text: str, lang: str) -> tuple[str | None, str]:
    """Returns (translation or None, note). None means: keep the English."""
    if lang == "en" or lang not in _RULES or not (text or "").strip():
        return None, ""
    try:
        r = get_router().complete(SYSTEM.format(rule=_RULES[lang]), text, temperature=0.1, max_tokens=2500)
    except AllProvidersFailed:
        return None, f"Translation to {LANGS[lang]['name']} needs a working API key; showing English."
    out = r.text.strip()
    missing = [n for n in set(numbers(text)) if n not in set(numbers(out))]
    if missing:
        return None, f"The {LANGS[lang]['name']} translation changed some numbers ({', '.join(missing[:4])}), so the English version is shown."
    return out, f"translated by {r.slot}"
