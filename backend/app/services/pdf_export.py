"""PDF export — a full report summary a patient can print or take to the doctor.

Contents: patient and report details, summary tiles, critical banner, all values with reference
ranges and status, the explanation for the patient and for the doctor, an optional bilingual copy
(Hindi / Konkani Romi), the MRI slice with its Grad-CAM heatmap and class probabilities, sources and
the disclaimer on every page.

Uses fpdf2 with Noto Sans + Noto Sans Devanagari and HarfBuzz text shaping (uharfbuzz) so Hindi
conjuncts render correctly.
"""
from __future__ import annotations

import base64
import io
import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

FONTS = Path(__file__).resolve().parents[2] / "data" / "fonts"
TEAL = (14, 124, 123)
TEAL_SOFT = (223, 240, 238)
INK = (20, 32, 46)
MUTED = (95, 105, 115)
LINE = (225, 221, 213)
STATUS = {
    "normal": ("Normal", (47, 122, 69), (226, 243, 230)),
    "low": ("Low", (47, 109, 181), (227, 237, 249)),
    "high": ("High", (184, 101, 12), (251, 238, 219)),
    "critical_low": ("Critical low", (180, 35, 24), (253, 230, 227)),
    "critical_high": ("Critical high", (180, 35, 24), (253, 230, 227)),
}
LANG_TITLE = {"hi": "Hindi (हिन्दी)", "kok": "Konkani (Romi)"}
DISCLAIMER = "NeuroLens gives general health education, not a diagnosis. Please discuss these results with a qualified doctor. In an emergency call 112."
_EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]")


def clean(text: str) -> str:
    text = _EMOJI.sub("", text or "")
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)  # links → text
    text = text.replace("【", "[").replace("】", "]")
    text = text.replace("--", "–")  # fpdf2 markdown uses -- for underline
    text = re.sub(r"(?<![*\w])\*(?![*\s])([^*\n]+?)(?<![*\s])\*(?![*\w])", r"__\1__", text)  # *italic* → fpdf2 __italic__
    return text


class _PDF(FPDF):
    title_text = "NeuroLens report"

    def header(self):
        self.set_fill_color(*TEAL)
        self.rect(0, 0, self.w, 18, "F")
        self.set_xy(14, 5)
        self.set_text_color(255, 255, 255)
        self.set_font("Noto", "B", 15)
        self.cell(60, 8, "NeuroLens")
        self.set_font("Noto", "", 9)
        self.set_xy(self.w - 104, 5)
        self.cell(90, 8, self.title_text, align="R")
        self.set_text_color(*INK)
        self.set_y(24)

    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*LINE)
        self.line(14, self.get_y(), self.w - 14, self.get_y())
        self.set_font("Noto", "", 7.5)
        self.set_text_color(*MUTED)
        self.set_y(-12)
        self.multi_cell(self.w - 50, 3.6, DISCLAIMER, align="L", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_xy(self.w - 34, -12)
        self.cell(20, 4, f"Page {self.page_no()}/{{nb}}", align="R")
        self.set_text_color(*INK)


def _new_pdf(title: str) -> _PDF:
    pdf = _PDF(format="A4")
    pdf.title_text = title
    pdf.set_margins(14, 24, 14)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
    pdf.add_font("Noto", "I", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Deva", "", str(FONTS / "NotoSansDevanagari-Regular.ttf"))
    pdf.add_font("Deva", "B", str(FONTS / "NotoSansDevanagari-Bold.ttf"))
    pdf.add_font("Deva", "I", str(FONTS / "NotoSansDevanagari-Regular.ttf"))
    pdf.set_fallback_fonts(["Deva"])
    try:
        pdf.set_text_shaping(True)  # needs uharfbuzz; without it Hindi conjuncts look broken
    except Exception:  # noqa: BLE001
        pass
    pdf.set_title(title)
    pdf.set_author("NeuroLens")
    pdf.set_creator("NeuroLens v2")
    pdf.alias_nb_pages()
    pdf.add_page()
    return pdf


def _h1(pdf: FPDF, text: str) -> None:
    pdf.set_font("Noto", "B", 17)
    pdf.multi_cell(0, 8, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def _h2(pdf: FPDF, text: str) -> None:
    if pdf.get_y() > pdf.h - 45:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Noto", "B", 12.5)
    pdf.set_text_color(*TEAL)
    pdf.multi_cell(0, 7, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*TEAL_SOFT)
    pdf.set_line_width(0.6)
    pdf.line(14, pdf.get_y(), pdf.w - 14, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.set_text_color(*INK)
    pdf.ln(2)


def _kv(pdf: FPDF, rows: list[tuple[str, str]]) -> None:
    pdf.set_font("Noto", "", 9.5)
    col = (pdf.w - 28) / 2
    for i in range(0, len(rows), 2):
        y = pdf.get_y()
        for j, (k, v) in enumerate(rows[i:i + 2]):
            pdf.set_xy(14 + j * col, y)
            pdf.set_text_color(*MUTED)
            pdf.cell(32, 5.5, k)
            pdf.set_text_color(*INK)
            pdf.cell(col - 34, 5.5, clean(v)[:60])
        pdf.ln(6)


def _tiles(pdf: FPDF, tiles: list[tuple[str, str, tuple]]) -> None:
    w = (pdf.w - 28 - 2 * 4) / 3
    y = pdf.get_y() + 1
    for i, (num, label, color) in enumerate(tiles):
        x = 14 + i * (w + 4)
        pdf.set_fill_color(244, 242, 237)
        pdf.rect(x, y, w, 17, "F", round_corners=True, corner_radius=2)
        pdf.set_xy(x + 4, y + 2)
        pdf.set_font("Noto", "B", 15)
        pdf.set_text_color(*color)
        pdf.cell(w - 8, 7, num)
        pdf.set_xy(x + 4, y + 9.5)
        pdf.set_font("Noto", "", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 8, 5, label)
    pdf.set_text_color(*INK)
    pdf.set_y(y + 21)


def _banner(pdf: FPDF, text: str, fg=(180, 35, 24), bg=(253, 230, 227)) -> None:
    pdf.set_fill_color(*bg)
    pdf.set_text_color(*fg)
    pdf.set_font("Noto", "B", 9.5)
    pdf.multi_cell(0, 6, clean(text), fill=True, padding=(2, 3), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    pdf.ln(2)


def _rng(r: dict) -> str:
    lo, hi = r.get("ref_low"), r.get("ref_high")
    if lo is not None and hi is not None:
        return f"{lo:g} – {hi:g}"
    if hi is not None:
        return f"< {hi:g}"
    if lo is not None:
        return f"> {lo:g}"
    return "—"


def _results_table(pdf: FPDF, results: list[dict]) -> None:
    widths = (62, 24, 30, 34, 32)
    heads = ("Test", "Result", "Unit", "Reference", "Status")
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r.get("category") or "Other", []).append(r)

    def head():
        pdf.set_font("Noto", "B", 8.5)
        pdf.set_fill_color(*TEAL_SOFT)
        for w, h in zip(widths, heads):
            pdf.cell(w, 7, h, fill=True)
        pdf.ln(7)

    head()
    for cat, rows in by_cat.items():
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
            head()
        pdf.set_font("Noto", "B", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6.5, cat.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*INK)
        for r in rows:
            if pdf.get_y() > pdf.h - 24:
                pdf.add_page()
                head()
            label, fg, bg = STATUS.get(r["status"], STATUS["normal"])
            flagged = r["status"] != "normal"
            pdf.set_fill_color(*(bg if flagged else (255, 255, 255)))
            pdf.set_font("Noto", "B" if flagged else "", 9)
            pdf.cell(widths[0], 6.5, clean(r["name"])[:38], fill=flagged)
            pdf.cell(widths[1], 6.5, f"{r['value']:g}", fill=flagged)
            pdf.set_font("Noto", "", 8.5)
            pdf.cell(widths[2], 6.5, r.get("unit", ""), fill=flagged)
            pdf.cell(widths[3], 6.5, _rng(r) + ("" if r.get("range_source") == "report" else " *"), fill=flagged)
            pdf.set_text_color(*fg)
            pdf.set_font("Noto", "B", 8.5)
            pdf.cell(widths[4], 6.5, label, fill=flagged)
            pdf.set_text_color(*INK)
            pdf.ln(6.5)
            pdf.set_draw_color(*LINE)
            pdf.line(14, pdf.get_y(), pdf.w - 14, pdf.get_y())
    if any(r.get("range_source") != "report" for r in results):
        pdf.set_font("Noto", "", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, "* standard adult range (the report did not print one)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*INK)


def _markdown(pdf: FPDF, text: str) -> None:
    """Paragraphs, bullets, **bold**, headings and simple pipe tables."""
    text = clean(text)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if re.match(r"^\s*\|.*\|\s*$", line):  # table block
            rows = []
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                if not re.match(r"^\s*\|[\s:|\-–—]+\|\s*$", lines[i]):
                    rows.append([c.strip().replace("**", "") for c in lines[i].strip().strip("|").split("|")])
                i += 1
            if rows:
                pdf.set_font("Noto", "", 8.5)
                n = max(len(r) for r in rows)
                rows = [r + [""] * (n - len(r)) for r in rows]
                with pdf.table(text_align="LEFT", line_height=4.6, padding=1.2, borders_layout="HORIZONTAL_LINES",
                               headings_style=FontFace(emphasis="BOLD", fill_color=TEAL_SOFT)) as t:
                    for r in rows:
                        row = t.row()
                        for c in r:
                            row.cell(c)
                pdf.ln(2)
            continue
        i += 1
        if not line.strip():
            pdf.ln(1.5)
            continue
        h = re.match(r"^#{1,4}\s+(.*)", line)
        if h:
            pdf.set_font("Noto", "B", 10.5)
            pdf.multi_cell(0, 6, h.group(1), markdown=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue
        b = re.match(r"^\s*(?:[-*•]|\d+\.)\s+(.*)", line)
        pdf.set_font("Noto", "", 9.8)
        if b:
            pdf.set_x(18)
            pdf.cell(4, 5.6, "•")
            pdf.multi_cell(0, 5.6, b.group(1), markdown=True, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.multi_cell(0, 5.6, line, markdown=True, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _b64_image(b64: str) -> io.BytesIO | None:
    if not b64:
        return None
    try:
        return io.BytesIO(base64.b64decode(b64))
    except ValueError:
        return None


def _mri_section(pdf: FPDF, scan: dict, texts: dict, lang: str) -> None:
    _h2(pdf, "Brain MRI (neuro module)")
    if not scan.get("available"):
        _banner(pdf, "The MRI classifier was not installed when this scan was uploaded, so no prediction was made.",
                (154, 106, 0), (253, 243, 212))
        return
    if pdf.get_y() > pdf.h - 110:
        pdf.add_page()
    y = pdf.get_y()
    size = 62
    img, heat = _b64_image(scan.get("image_png", "")), _b64_image(scan.get("heatmap_png", ""))
    if img:
        pdf.image(img, x=14, y=y, w=size, h=size)
    if heat:
        pdf.image(heat, x=14 + size + 4, y=y, w=size, h=size)
    pdf.set_font("Noto", "", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.set_xy(14, y + size + 1)
    pdf.cell(size, 4, "MRI slice", align="C")
    pdf.cell(4, 4, "")
    pdf.cell(size, 4, "Grad-CAM: where the model looked", align="C")
    # prediction panel on the right
    x0 = 14 + 2 * size + 10
    pdf.set_xy(x0, y)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Noto", "", 8.5)
    pdf.cell(0, 5, "Model suggests", new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    pdf.set_font("Noto", "B", 13)
    pdf.set_x(x0)
    pdf.multi_cell(pdf.w - 14 - x0, 7, scan.get("display", ""), new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font("Noto", "", 8.5)
    names = {"glioma": "Glioma", "meningioma": "Meningioma", "notumor": "No tumour", "pituitary": "Pituitary"}
    barw = pdf.w - 14 - x0 - 34
    for k, p in sorted((scan.get("probabilities") or {}).items(), key=lambda kv: -kv[1]):
        pdf.set_x(x0)
        pdf.cell(22, 6, names.get(k, k))
        yy = pdf.get_y() + 2
        pdf.set_fill_color(236, 233, 226)
        pdf.rect(pdf.get_x(), yy, barw, 2.6, "F")
        pdf.set_fill_color(*(TEAL if k == scan.get("label") else (150, 156, 163)))
        pdf.rect(pdf.get_x(), yy, barw * p, 2.6, "F")
        pdf.set_x(pdf.get_x() + barw + 2)
        pdf.cell(10, 6, f"{p:.0%}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    pdf.set_y(y + size + 8)
    if scan.get("confidence", 1) < 0.6:
        _banner(pdf, "Low confidence: the model is not sure about this image. Do not rely on this result.", (154, 106, 0), (253, 243, 212))
    _banner(pdf, "Research classifier trained on a public dataset of 2D slices. It cannot diagnose. A radiologist must read the full scan.",
            (95, 105, 115), (244, 242, 237))
    _explanations(pdf, texts, lang, prefix="MRI: ")


def _explanations(pdf: FPDF, texts: dict, lang: str, prefix: str = "") -> None:
    if texts.get("patient:en"):
        _h2(pdf, prefix + "Explained simply")
        _markdown(pdf, texts["patient:en"])
    if texts.get("doctor:en"):
        _h2(pdf, prefix + "Summary for your doctor")
        _markdown(pdf, texts["doctor:en"])
    if lang != "en" and texts.get(f"patient:{lang}"):
        _h2(pdf, prefix + "Explained simply — " + LANG_TITLE.get(lang, lang))
        _markdown(pdf, texts[f"patient:{lang}"])
    note = texts.get(f"note:{lang}")
    if lang != "en" and note and not texts.get(f"patient:{lang}"):
        _banner(pdf, note, (154, 106, 0), (253, 243, 212))


def build_pdf(*, user_name: str, profile: dict, report: dict | None, scan: dict | None, texts_report: dict,
              texts_mri: dict, lang: str = "en", sources: list[dict] | None = None) -> bytes:
    kind = "Lab report summary" if report else "Brain MRI summary"
    if report and scan:
        kind = "Lab report + MRI summary"
    pdf = _new_pdf(kind)
    _h1(pdf, kind)
    rows = [("Prepared for", user_name), ("Generated", datetime.now().strftime("%d %b %Y, %H:%M"))]
    if report:
        rows += [("Report", report["filename"]), ("Report date", report.get("report_date") or "not printed")]
        rows += [("Read from", report.get("source", "text")), ("Values", "checked by you" if report.get("source", "").startswith("photo") else "parsed automatically")]
    if scan:
        rows += [("MRI image", scan.get("filename", "")), ("MRI uploaded", scan.get("created", ""))]
    if profile.get("sex") or profile.get("age_group"):
        rows += [("Sex", profile.get("sex", "—")), ("Age group", profile.get("age_group", "—"))]
    if lang != "en":
        rows += [("Language", "English + " + LANG_TITLE.get(lang, lang))]
    _kv(pdf, rows)
    pdf.ln(2)
    if report:
        s = report["summary"]
        _tiles(pdf, [(str(s["total"]), "tests read", INK), (str(s["flagged"]), "outside the range", (184, 101, 12) if s["flagged"] else INK),
                     (str(s["critical"]), "critical", (180, 35, 24) if s["critical"] else INK)])
        if s["critical"]:
            _banner(pdf, f"Critical value(s): {', '.join(s['critical_tests'])}. Please contact your doctor today.")
        _h2(pdf, "Your results")
        _results_table(pdf, report["results"])
        _explanations(pdf, texts_report, lang)
    if scan:
        _mri_section(pdf, scan, texts_mri, lang)
    if sources:
        _h2(pdf, "Sources")
        pdf.set_font("Noto", "", 8.5)
        for i, src in enumerate(sources[:10], 1):
            host = re.sub(r"^https?://([^/]+).*", r"\1", src.get("url", ""))
            pdf.multi_cell(0, 4.8, f"[{i}] {clean(src.get('title', ''))} — {src.get('source', '')}" + (f" ({host})" if host else ""),
                           align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())
