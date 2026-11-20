"""Photo reports (OCR) — turns a phone photo of a lab report into text the normal parser can read.

Engines, in order (OCR_ENGINES in .env):
  gemini     Gemini vision through the key pool. Best on tables; no install.
  tesseract  Local Tesseract (brew install tesseract; pip install pytesseract). Works offline.

OCR can misread digits, so photo reports are saved as "needs_review": the user checks and edits
the values before NeuroLens explains them or puts them in a PDF.
"""
from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import get_settings
from .model_router import AllProvidersFailed, get_router

OCR_PROMPT = (
    "You transcribe medical laboratory reports from photos. Output plain text only.\n"
    "First line: DATE: <the report or sample collection date exactly as printed, or 'unknown'>.\n"
    "Then one line per test result in this exact format:\n"
    "<test name as printed> | <result value exactly as printed> | <unit> | <reference range as printed>\n"
    "Rules: copy digits exactly (keep commas and decimal points as printed); never calculate, round, guess or "
    "fill in missing values; skip patient name, phone numbers, addresses and doctor names; if a value is "
    "unreadable write ??? for it. No commentary."
)


_TOKEN = re.compile(r"(?<![A-Za-z])[0-9OoIlS][0-9OoIlS.,]*[0-9OoIlS](?![A-Za-z])")
_FIX = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5"})


def normalize_digits(text: str) -> str:
    """Fix typical OCR digit confusions inside numbers only: '1O.4' → '10.4', 'l2.5' → '12.5'.
    Words (Iron, SGOT, LDL) are untouched because a token must contain a real digit."""
    def fix(m: re.Match) -> str:
        tok = m.group(0)
        return tok.translate(_FIX) if any(c.isdigit() for c in tok) else tok
    return _TOKEN.sub(fix, text)


class OcrError(ValueError):
    pass


@dataclass
class OcrResult:
    text: str
    engine: str
    notes: list[str]


def _prepare(data: bytes, max_side: int = 2000) -> tuple[bytes, str]:
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")  # phone photos are often rotated
    except (UnidentifiedImageError, OSError) as e:
        raise OcrError("Please upload a JPG, PNG or WEBP photo of the report") from e
    if min(img.size) < 300:
        raise OcrError("The photo is too small to read. Take it closer, with the whole table visible")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def _gemini(data: bytes, mime: str) -> tuple[str, str]:
    r = get_router().complete(OCR_PROMPT, "Transcribe this lab report photo.", image=(data, mime), providers=("gemini",),
                              temperature=0.0, max_tokens=2048)
    return r.text, r.slot


def _tesseract(data: bytes) -> str:
    try:
        import pytesseract
    except ImportError as e:
        raise OcrError("pytesseract is not installed") from e
    if not shutil.which("tesseract"):
        raise OcrError("Tesseract is not installed (Mac: brew install tesseract)")
    img = ImageOps.grayscale(Image.open(io.BytesIO(data)))
    return pytesseract.image_to_string(img, config="--psm 6")


def read_photo(data: bytes) -> OcrResult:
    jpeg, mime = _prepare(data)
    notes: list[str] = []
    for engine in [e.strip() for e in get_settings().ocr_engines.split(",") if e.strip()]:
        try:
            if engine == "gemini":
                text, slot = _gemini(jpeg, mime)
                engine = f"gemini ({slot})"
            elif engine == "tesseract":
                text = _tesseract(jpeg)
            else:
                continue
            if len(text.strip()) >= 15:
                return OcrResult(normalize_digits(text), engine, notes)
            notes.append(f"{engine}: no text found")
        except AllProvidersFailed:
            notes.append("gemini: no Gemini key answered")
        except OcrError as e:
            notes.append(f"{engine}: {e}")
    raise OcrError("Could not read this photo (" + "; ".join(notes) + "). Try a sharper photo, a PDF, or type the values.")
