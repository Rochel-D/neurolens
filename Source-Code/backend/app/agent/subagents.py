"""Specialist subagents. Each owns one kind of data and returns structured findings:

    {"facts": str, "sources": [..], "template": str, "data": {...}}

`facts` is the only thing the explainer may use; `template` is a safe offline answer built
from the same facts; `data` carries structured values for the verifier.
Roles are split by data and rules, not by convenience (same principle as KonkanBite).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LabReport, MriScan
from ..services import lab_parser, memory, rag, web_tools

STATUS_WORD = {"low": "LOW", "high": "HIGH", "critical_low": "CRITICALLY LOW", "critical_high": "CRITICALLY HIGH", "normal": "normal"}


def _rng(r: dict) -> str:
    lo, hi = r.get("ref_low"), r.get("ref_high")
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}"
    if hi is not None:
        return f"below {hi:g}"
    if lo is not None:
        return f"above {lo:g}"
    return "not given"


def _src(docs: list[rag.Doc]) -> list[dict]:
    return [{"title": d.title, "url": d.url, "source": "NeuroLens knowledge base", "id": d.id} for d in docs]


# ------------------------------------------------------------------ lab agent (general, all specialties)
def lab_agent(db: Session, user_id: int, report_id: int, question: str) -> dict:
    rep = db.get(LabReport, report_id)
    if rep is None or rep.user_id != user_id:
        return {"facts": "The requested report was not found.", "sources": [], "template": "I couldn't find that report. Please upload it again.", "data": {"results": []}}
    if rep.status == "needs_review":
        msg = ("The values in this report were read from a photo and have not been checked yet. Please open the Reports page, "
               "check the values against your paper report and press **Confirm values**. Then I can explain them.")
        return {"facts": msg, "sources": [], "template": msg, "data": {"results": []}, "direct": True}
    results = rep.results or []
    flagged = [r for r in results if r["status"] != "normal"]
    normal = [r for r in results if r["status"] == "normal"]
    # if the user asks about one specific test, focus on it
    q = question.lower()
    asked = [r for r in results if r["name"].lower() in q or any(a in q for a in lab_parser.by_id()[r["test_id"]]["aliases"] if len(a) > 2)]
    focus = asked or flagged

    lines, tmpl, docs = [], [], []
    for r in focus[:8]:
        d = lab_parser.describe(r["test_id"])
        meaning = d["high_means"] if "high" in r["status"] else d["low_means"] if "low" in r["status"] else ""
        lines.append(
            f"- {r['name']}: {r['value']:g} {r['unit']} (reference {_rng(r)}, {'from the report' if r['range_source'] == 'report' else 'standard adult range'}) → {STATUS_WORD[r['status']]}. "
            f"What it is: {d['what']} {('What this can mean: ' + meaning) if meaning else ''}"
        )
        tmpl.append(f"- **{r['name']}** is {STATUS_WORD[r['status']].lower()} at {r['value']:g} {r['unit']} (usual range {_rng(r)}). {d['what']} {meaning}")
        docs += rag.retrieve(f"{r['name']} {r['status']} {meaning}", k=1, module="general")
    if normal and not asked:
        lines.append("- Within range: " + ", ".join(f"{r['name']} {r['value']:g} {r['unit']}" for r in normal))
    crit = [r for r in results if r["status"].startswith("critical")]
    if crit:
        lines.append("- CRITICAL values present: " + ", ".join(r["name"] for r in crit) + ". The patient should contact a doctor today.")

    # one condition-level note that ties the flagged tests together (e.g. anaemia, prediabetes)
    if flagged:
        docs += rag.retrieve(" ".join(f"{r['name']} {r['status']}" for r in flagged), k=2, module="general")
    uniq = list({d.id: d for d in docs}.values())[:5]
    for d in uniq:
        if not d.id.startswith("lab:"):
            lines.append(f"- Background ({d.title}): {d.text}")

    head = f"Report '{rep.filename}': {len(results)} tests read, {len(flagged)} outside the reference range."
    if not results:
        head = "No recognisable lab tests were found in this report."
    template = head + "\n\n" + ("\n".join(tmpl) if tmpl else "All the tests I could read are within their reference ranges.")
    if crit:
        template += "\n\n**Some values are marked critical. Please contact your doctor today.**"
    # doctor-facing template: compact table of abnormal values + related notes
    doc = [f"**{rep.filename}** ({rep.report_date or 'date not printed'}): {len(results)} analytes, {len(flagged)} outside reference range."]
    if flagged:
        doc.append("\n| Test | Result | Reference | Flag |\n|---|---|---|---|")
        doc += [f"| {r['name']} | {r['value']:g} {r['unit']} | {_rng(r)} | {STATUS_WORD[r['status']]} |" for r in flagged]
    notes = [d.title for d in uniq if not d.id.startswith("lab:")]
    if notes:
        doc.append("\nPatterns to consider (from the knowledge base, not a diagnosis): " + ", ".join(notes) + ".")
    if crit:
        doc.append("\n**Critical values: " + ", ".join(r["name"] for r in crit) + ". Same-day clinical review advised.**")
    return {
        "facts": head + "\n" + "\n".join(lines),
        "sources": _src(uniq),
        "template": template,
        "template_doctor": "\n".join(doc),
        "data": {"results": results, "critical": [r["name"] for r in crit]},
    }


# ------------------------------------------------------------------ neuro agent (brain MRI only)
MRI_DISPLAY = {"glioma": "Glioma pattern", "meningioma": "Meningioma pattern", "notumor": "No tumour pattern", "pituitary": "Pituitary tumour pattern"}


def neuro_agent(db: Session, user_id: int, mri_id: int, trace: list, research: bool = True) -> dict:
    scan = db.get(MriScan, mri_id)
    if scan is None or scan.user_id != user_id:
        return {"facts": "MRI not found.", "sources": [], "template": "I couldn't find that scan. Please upload it again.", "data": {}}
    if not scan.available:
        msg = "The MRI classifier is not installed on this server, so no prediction was made."
        return {"facts": msg, "sources": [], "template": msg, "data": {"prediction": {}}}
    min_conf = get_settings().mri_min_confidence
    label_words = {"glioma": "glioma", "meningioma": "meningioma", "pituitary": "pituitary tumour", "notumor": "no tumour"}
    word = label_words[scan.label]
    probs = ", ".join(f"{label_words[k]} {v:.0%}" for k, v in sorted(scan.probabilities.items(), key=lambda kv: -kv[1]))
    lines = [
        f"- Classifier (EfficientNet-B0 trained on a public 4-class brain MRI dataset) predicted: {word} pattern, confidence {scan.confidence:.0%}.",
        f"- All class probabilities: {probs}.",
        "- A Grad-CAM heatmap shows which image regions drove the prediction.",
        "- This is a single 2D slice; a radiologist reviews the full scan with all sequences.",
    ]
    if scan.confidence < min_conf:
        lines.append(f"- Confidence is below {min_conf:.0%}: the result is uncertain and should not be relied on.")
    docs = [d for d in (rag.get(i) for i in ([scan.label if scan.label != "pituitary" else "pituitary_tumour", "gradcam", "brain_mri"] if scan.label != "notumor" else ["no_tumour", "gradcam", "brain_mri"])) if d]
    for d in docs:
        lines.append(f"- Background ({d.title}): {d.text}")
    papers = []
    if scan.label != "notumor" and research:
        try:
            papers = web_tools.search_pubmed(f"{word} MRI diagnosis", k=3, trace=trace)
        except Exception as e:  # noqa: BLE001
            trace.append({"tool": "pubmed", "error": type(e).__name__})
    for p in papers:
        lines.append(f"- Research: \"{p['title']}\" ({p['journal']}, {p['year']}).")
    template = (
        f"The model found a **{word} pattern** with {scan.confidence:.0%} confidence. "
        + ("This is **uncertain**, so please don't rely on it. " if scan.confidence < min_conf else "")
        + (docs[0].text if docs else "")
        + " Only a radiologist and neurologist can interpret your scan."
    )
    sources = _src(docs) + [{"title": p["title"], "url": p["url"], "source": "PubMed"} for p in papers]
    doctor = (
        f"**MRI classifier output** (EfficientNet-B0, single 2D slice, public 4-class dataset): **{word} pattern**, "
        f"p = {scan.confidence:.2f}.\n\n| Class | Probability |\n|---|---|\n"
        + "\n".join(f"| {label_words[k]} | {v:.0%} |" for k, v in sorted(scan.probabilities.items(), key=lambda kv: -kv[1]))
        + ("\n\n**Below the confidence threshold — treat as uncertain.**" if scan.confidence < min_conf else "")
        + "\n\nGrad-CAM overlay available. Not validated for clinical use; correlate with the full radiology study."
    )
    return {
        "facts": "\n".join(lines),
        "sources": sources,
        "template": template,
        "template_doctor": doctor,
        "data": {"prediction": {"label": scan.label, "confidence": scan.confidence}, "papers": papers},
    }


# ------------------------------------------------------------------ term agent (medical terms, any specialty)
def term_agent(question: str, terms: list[str], trace: list) -> dict:
    query = " ".join(terms) if terms else question
    docs = rag.retrieve(query, k=3)
    if docs:  # keep only documents that are clearly relevant and about as relevant as the best one
        docs = [d for d in docs if d.score >= 0.6 * docs[0].score and d.score >= 0.12]
    lines = [f"- {d.title}: {d.text}" for d in docs]
    sources = _src(docs)
    if not docs or docs[0].score < 0.18:  # not in our curated base → live, trusted sources
        try:
            for m in web_tools.search_medlineplus(query, trace=trace)[:2]:
                lines.append(f"- {m['title']} (MedlinePlus): {m['snippet']}")
                sources.append(m)
        except Exception as e:  # noqa: BLE001
            trace.append({"tool": "medlineplus", "error": type(e).__name__})
        if len(sources) < 2:
            try:
                for w in web_tools.serper_search(query + " medlineplus OR nhs OR mayoclinic", k=2, trace=trace):
                    lines.append(f"- {w['title']} (web): {w['snippet']}")
                    sources.append(w)
            except Exception as e:  # noqa: BLE001
                trace.append({"tool": "serper", "error": type(e).__name__})
    if not lines:
        lines = ["No trusted source was found for this term."]
    template = "\n\n".join(f"**{d.title}** — {d.text}" for d in docs) or "I couldn't find a trusted explanation for that term. Please ask your doctor."
    return {"facts": "\n".join(lines), "sources": sources, "template": template, "data": {}}


# ------------------------------------------------------------------ research agent
def research_agent(query: str, trace: list) -> dict:
    try:
        papers = web_tools.search_pubmed(query, k=5, trace=trace)
    except Exception as e:  # noqa: BLE001
        trace.append({"tool": "pubmed", "error": type(e).__name__})
        papers = []
    if not papers:
        return {"facts": "PubMed search returned no results (or was unreachable).", "sources": [], "template": "I couldn't reach PubMed right now. Please try again later.", "data": {"papers": []}}
    lines = [f"- [{i}] \"{p['title']}\" — {p['journal']} ({p['year']})" for i, p in enumerate(papers, 1)]
    return {
        "facts": "PubMed results for: " + query + "\n" + "\n".join(lines) + "\nOnly titles are known; do not invent findings beyond the titles.",
        "sources": [{"title": p["title"], "url": p["url"], "source": "PubMed"} for p in papers],
        "template": "Here are recent PubMed papers on this topic:\n" + "\n".join(f"- [{p['title']}]({p['url']}) — {p['journal']}, {p['year']}" for p in papers),
        "data": {"papers": papers},
    }


# ------------------------------------------------------------------ memory agent
def memory_agent(db: Session, user_id: int, fact: str) -> dict:
    item = memory.remember(db, user_id, "fact", fact)
    if item is None:
        msg = "I can't save that because memory is off. You can turn it on in the Memory page; it's your choice."
    else:
        msg = f"Saved to your memory: \"{fact}\". You can see or delete it any time in the Memory page."
    return {"facts": msg, "sources": [], "template": msg, "data": {"saved": item is not None}, "direct": True}
