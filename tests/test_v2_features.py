"""Version 2 features: thread-owned attachments, PDF export (+Hindi), photo review step, trends,
knowledge-base editor, feedback, admin key controls, CSV export, v1 database migration."""
import io

from PIL import Image

from backend.app.config import Settings
from backend.app.services import model_router, ocr, pdf_export
from tests.conftest import auth, sample


def admin_headers(client):
    tok = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "Admin12345"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def upload(client, h, name):
    return client.post("/api/reports", headers=h, files={"file": (name, sample(name), "text/plain")}).json()


def test_new_chat_never_sees_previous_chats_report(client):
    """v1 bug: a new chat showed the document from the previous chat."""
    h = auth(client, "thread@test.local")
    rep = upload(client, h, "cbc_report.txt")
    r1 = client.post("/api/chat", headers=h, json={"message": "Please explain my blood report", "report_id": rep["id"]}).json()
    assert r1["attachments"]["report"]["id"] == rep["id"]
    # follow-up in the SAME chat still knows the report without re-attaching
    r2 = client.post("/api/chat", headers=h, json={"message": "What about my ferritin?", "thread_id": r1["thread_id"]}).json()
    assert r2["attachments"]["report"]["id"] == rep["id"] and "lab_report" in r2["intent"]
    # a NEW chat has no attachment
    r3 = client.post("/api/chat", headers=h, json={"message": "What is ferritin?"}).json()
    assert r3["attachments"] == {"report": None, "mri": None} and "lab_report" not in r3["intent"]
    t = client.get(f"/api/chat/threads/{r3['thread_id']}", headers=h).json()
    assert t["attachments"]["report"] is None
    t1 = client.get(f"/api/chat/threads/{r1['thread_id']}", headers=h).json()
    assert t1["attachments"]["report"]["filename"] == "cbc_report.txt"


def test_pdf_from_button_and_from_chat(client):
    h = auth(client, "pdf@test.local")
    rep = upload(client, h, "metabolic_report.txt")
    e = client.post("/api/exports", headers=h, json={"report_id": rep["id"]}).json()
    pdf = client.get(f"/api/exports/{e['token']}", headers=h)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and pdf.headers["content-type"] == "application/pdf"
    # chat request
    r = client.post("/api/chat", headers=h, json={"message": "Can you generate a PDF of this explanation?", "report_id": rep["id"]}).json()
    assert r["intent"] == ["export"] and r["export"]["filename"].endswith(".pdf")
    assert client.get(f"/api/exports/{r['export']['token']}", headers=h).content[:5] == b"%PDF-"
    # someone else cannot download it
    other = auth(client, "pdf2@test.local")
    assert client.get(f"/api/exports/{e['token']}", headers=other).status_code == 404
    # PDF without anything attached → helpful message, no crash
    r = client.post("/api/chat", headers=h, json={"message": "download pdf"}).json()
    assert "upload a report" in r["answer"].lower()


def test_hindi_pdf_and_translation_number_check(client):
    def transport(url, headers, body, timeout):
        if body is None:
            return 200, {"models": [{"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]}]}
        system = body["systemInstruction"]["parts"][0]["text"]
        user = body["contents"][-1]["parts"][-1]["text"]
        if "translator" in system:
            nums = " ".join(sorted(set(__import__("re").findall(r"\d+(?:\.\d+)?", user))))
            return 200, {"candidates": [{"content": {"parts": [{"text": f"आपकी रिपोर्ट के मान: {nums}। कृपया डॉक्टर से बात करें।"}]}}]}
        if "JSON" in system:
            return 200, {"candidates": [{"content": {"parts": [{"text": '{"intent": "lab_report"}'}]}}]}
        return 200, {"candidates": [{"content": {"parts": [{"text": "Your LDL cholesterol is high at 154 mg/dL. Please talk to your doctor."}]}}]}

    model_router.set_router(model_router.ModelRouter(Settings(_env_file=None, gemini_api_key_1="g1-test-key"), transport))
    try:
        h = auth(client, "hindi@test.local")
        rep = upload(client, h, "metabolic_report.txt")
        r = client.post("/api/chat", headers=h, json={"message": "Explain my blood report", "report_id": rep["id"], "language": "hi"}).json()
        assert "डॉक्टर" in r["answer"] and r["english"] and "154" in r["answer"]
        e = client.post("/api/exports", headers=h, json={"report_id": rep["id"], "language": "hi"}).json()
        assert e["filename"].endswith("-hi.pdf")
        assert client.get(f"/api/exports/{e['token']}", headers=h).content[:5] == b"%PDF-"
    finally:
        model_router.set_router(None)


def test_translation_that_changes_numbers_is_rejected():
    from backend.app.services import translate

    def transport(url, headers, body, timeout):
        if body is None:
            return 200, {"models": [{"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]}]}
        return 200, {"candidates": [{"content": {"parts": [{"text": "आपका LDL 145 है"}]}}]}

    model_router.set_router(model_router.ModelRouter(Settings(_env_file=None, gemini_api_key_1="g1-test-key"), transport))
    try:
        out, note = translate.translate("Your LDL is 154 mg/dL.", "hi")
        assert out is None and "154" in note
    finally:
        model_router.set_router(None)


def test_photo_report_review_then_confirm(client, monkeypatch):
    raw = "DATE: 02-11-2026\nHaemoglobin | 1O.4 | g/dL | 12.0 - 15.5\nPlatelet Count | 2,50,000 | /cumm | 1,50,000 - 4,10,000\nTSH | 6.l | uIU/mL | 0.4 - 4.5"
    monkeypatch.setattr(ocr, "read_photo", lambda data: ocr.OcrResult(ocr.normalize_digits(raw), "gemini (gemini#1)", []))
    h = auth(client, "photo@test.local")
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (250, 250, 250)).save(buf, "JPEG")
    rep = client.post("/api/reports", headers=h, files={"file": ("photo.jpg", buf.getvalue(), "image/jpeg")}).json()
    assert rep["status"] == "needs_review" and rep["source"].startswith("photo:gemini") and rep["report_date"] == "2026-11-02"
    got = {r["test_id"]: r["value"] for r in rep["results"]}
    assert got == {"hemoglobin": 10.4, "platelets": 250, "tsh": 6.1}  # OCR confusions 1O.4 and 6.l fixed
    # explanation and PDF are blocked until the user confirms
    r = client.post("/api/chat", headers=h, json={"message": "Explain my report", "report_id": rep["id"]}).json()
    assert "confirm values" in r["answer"].lower()
    assert client.post("/api/exports", headers=h, json={"report_id": rep["id"]}).status_code == 422
    rows = [{"test_id": x["test_id"], "value": x["value"], "ref_low": x["ref_low"], "ref_high": x["ref_high"]} for x in rep["results"]]
    rows.append({"test_id": "ferritin", "value": 8, "ref_low": 15, "ref_high": 150})  # user adds a missed test
    fixed = client.put(f"/api/reports/{rep['id']}/results", headers=h, json={"results": rows}).json()
    hb = next(x for x in fixed["results"] if x["test_id"] == "hemoglobin")
    assert fixed["status"] == "final" and hb["status"] == "low" and fixed["summary"]["total"] == 4
    bad = client.put(f"/api/reports/{rep['id']}/results", headers=h, json={"results": [{"test_id": "tsh", "value": 2, "ref_low": 5, "ref_high": 1}]})
    assert bad.status_code == 422


def test_trends_across_reports(client):
    h = auth(client, "trend@test.local")
    a = upload(client, h, "metabolic_report.txt")
    text = sample("metabolic_report.txt").decode().replace("27/10/2026", "27/01/2027").replace("HbA1c                        6.2", "HbA1c                        5.9")
    b = client.post("/api/reports", headers=h, files={"file": ("later.txt", text.encode(), "text/plain")}).json()
    assert a["report_date"] == "2026-10-27" and b["report_date"] == "2027-01-27"
    tr = {t["test_id"]: t for t in client.get("/api/trends", headers=h).json()}
    assert [p["value"] for p in tr["hba1c"]["points"]] == [6.2, 5.9] and tr["hba1c"]["direction"] == "better"
    hist = client.get(f"/api/reports/{b['id']}/history", headers=h).json()
    assert hist[0]["id"] == a["id"]
    dash = client.get("/api/dashboard", headers=h).json()
    assert dash["counts"]["reports"] == 2 and dash["trends"]


def test_kb_editor_adds_test_and_parser_uses_it(client):
    ha = admin_headers(client)
    new = {"id": "d_dimer", "name": "D-dimer", "aliases": ["d-dimer", "d dimer"], "unit": "ng/mL", "low": 0, "high": 500,
           "critical_low": None, "critical_high": None, "category": "Coagulation", "conversions": {},
           "what": "D-dimer is a protein fragment made when a blood clot breaks down.",
           "low_means": "A low value is normal.", "high_means": "A high value can occur with clots, infection or after surgery."}
    assert client.put("/api/admin/kb/lab", headers=ha, json=new).status_code == 200
    p = client.post("/api/admin/kb/test-parse", headers=ha, json={"text": "D-Dimer 820 ng/mL 0 - 500\nRandom line 42"}).json()
    assert p["results"][0]["test_id"] == "d_dimer" and p["results"][0]["status"] == "high" and p["unmatched"] == ["Random line 42"]
    clash = dict(new, id="other_test", aliases=["d dimer"])
    assert "already used" in client.put("/api/admin/kb/lab", headers=ha, json=clash).json()["detail"]
    assert client.put("/api/admin/kb/lab", headers=ha, json=dict(new, low=600)).status_code == 422
    note = {"id": "dvt", "title": "Deep vein thrombosis", "module": "general",
            "text": "A deep vein thrombosis is a blood clot in a deep vein, usually in the leg, that can cause swelling and pain."}
    assert client.put("/api/admin/kb/note", headers=ha, json=note).status_code == 200
    from backend.app.services import rag

    assert rag.retrieve("blood clot in the leg", k=1)[0].id == "dvt"
    client.delete("/api/admin/kb/lab/d_dimer", headers=ha)
    client.post("/api/admin/kb/note/dvt/reset", headers=ha)
    assert client.post("/api/admin/kb/test-parse", headers=ha, json={"text": "D-Dimer 820 ng/mL"}).json()["results"] == []
    # editing a seed test and resetting it
    ldl = next(t for t in client.get("/api/admin/kb/lab", headers=ha).json() if t["id"] == "ldl")
    client.put("/api/admin/kb/lab", headers=ha, json=dict(ldl, high=129))
    assert client.post("/api/admin/kb/test-parse", headers=ha, json={"text": "LDL cholesterol 120 mg/dL"}).json()["results"][0]["status"] == "normal"
    client.post("/api/admin/kb/lab/ldl/reset", headers=ha)
    assert client.post("/api/admin/kb/test-parse", headers=ha, json={"text": "LDL cholesterol 120 mg/dL"}).json()["results"][0]["status"] == "high"


def test_feedback_traces_filter_csv_and_keys(client):
    h = auth(client, "fb@test.local")
    r = client.post("/api/chat", headers=h, json={"message": "What is =HYPERLINK(1) ferritin?"}).json()
    assert client.post("/api/chat/feedback", headers=h, json={"trace_id": r["trace_id"], "rating": -1, "comment": "too long"}).json()["ok"]
    other = auth(client, "fb2@test.local")
    assert client.post("/api/chat/feedback", headers=other, json={"trace_id": r["trace_id"], "rating": 1}).status_code == 404
    ha = admin_headers(client)
    t = client.get("/api/admin/traces?rating=down", headers=ha).json()
    assert t["total"] >= 1 and t["items"][0]["comment"] == "too long"
    assert client.get("/api/admin/traces?q=ferritin", headers=ha).json()["total"] >= 1
    csv_text = client.get("/api/admin/traces.csv", headers=ha).text
    assert "too long" in csv_text.splitlines()[1] or "too long" in csv_text
    fb = client.get("/api/admin/feedback", headers=ha).json()
    assert fb["down"] >= 1
    ov = client.get("/api/admin/overview", headers=ha).json()
    assert len(ov["per_day"]) == 7 and ov["feedback"]["down"] >= 1
    assert client.get("/api/admin/keys", headers=ha).json()["slots"] == []  # tests run with no keys
    assert client.post("/api/admin/models", headers=ha, json={"provider": "gemini", "models": ["gemini-3.8-flash"]}).json()["ok"]
    assert client.get("/api/admin/overview", headers=h).status_code == 404  # users cannot see admin


def test_pdf_builder_renders_hindi_mri_and_tables():
    import base64

    buf = io.BytesIO()
    Image.radial_gradient("L").resize((224, 224)).save(buf, "PNG")
    img = base64.b64encode(buf.getvalue()).decode()
    pdf = pdf_export.build_pdf(
        user_name="Rochel", profile={"sex": "female"},
        report={"filename": "cbc.pdf", "report_date": "2026-10-28", "source": "pdf",
                "summary": {"total": 1, "flagged": 1, "critical": 1, "critical_tests": ["Platelet count"]},
                "results": [{"name": "Platelet count", "value": 40, "unit": "×10³/µL", "ref_low": 150, "ref_high": 410, "status": "critical_low",
                             "category": "Blood count (CBC)", "range_source": "report"}]},
        scan={"available": True, "label": "glioma", "display": "Glioma pattern", "confidence": 0.55,
              "probabilities": {"glioma": 0.55, "meningioma": 0.3, "notumor": 0.1, "pituitary": 0.05}, "heatmap_png": img, "image_png": img,
              "filename": "scan.png", "created": "01 Nov 2026"},
        texts_report={"patient:en": "⚠️ **Platelets** are low.\n- Bleeding risk\n\n| Test | Result |\n|---|---|\n| PLT | 40 |",
                      "doctor:en": "Severe thrombocytopenia.", "patient:hi": "**प्लेटलेट्स** कम हैं।"},
        texts_mri={"patient:en": "Uncertain glioma pattern."}, lang="hi", sources=[{"title": "Platelets", "url": "https://x.org", "source": "KB"}])
    assert pdf[:5] == b"%PDF-" and len(pdf) > 20000


def test_v1_database_is_migrated(tmp_path):
    """Starting v2 on a version-1 database adds the new columns instead of crashing."""
    import sqlite3

    from sqlalchemy import create_engine, inspect

    from backend.app import db as dbmod

    path = tmp_path / "v1.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE lab_reports (id INTEGER PRIMARY KEY, user_id INTEGER, filename VARCHAR(200), results JSON, summary JSON, created_at DATETIME)")
    con.execute("INSERT INTO lab_reports VALUES (1, 1, 'old.txt', '[]', '{}', '2026-10-30 10:00:00')")
    con.commit()
    con.close()
    old = dbmod.engine
    dbmod.engine = create_engine(f"sqlite:///{path}")
    try:
        dbmod._add_missing_columns()
        cols = {c["name"] for c in inspect(dbmod.engine).get_columns("lab_reports")}
        assert {"report_date", "source", "status", "explanations"} <= cols
        with dbmod.engine.connect() as c:
            assert c.exec_driver_sql("SELECT status, source FROM lab_reports").fetchone() == ("final", "text")
    finally:
        dbmod.engine = old


def test_suspect_value_sends_report_to_review(client):
    h = auth(client, "suspect@test.local")
    text = b"Haemoglobin 13.1 g/dL 12 - 15.5\nPlatelet count 2 /cumm 150000-410000\n"
    rep = client.post("/api/reports", headers=h, files={"file": ("odd.txt", text, "text/plain")}).json()
    assert rep["status"] == "needs_review" and rep["summary"]["suspect_tests"] == ["Platelet count"]


def test_same_report_uploaded_twice_is_not_duplicated(client):
    """v2.1 broken window: uploading the same file twice made every trend read 'same'."""
    txt = b"Report date: 01/03/2026\nHaemoglobin 11.2 g/dL 12-15\nGlucose fasting 132 mg/dL 70-100\n"
    h = auth(client, "dup@test.local")
    a = client.post("/api/reports", files={"file": ("r.txt", txt, "text/plain")}, headers=h).json()
    b = client.post("/api/reports", files={"file": ("r copy.txt", txt, "text/plain")}, headers=h).json()
    assert b["id"] == a["id"] and b.get("duplicate")
