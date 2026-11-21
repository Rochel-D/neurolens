"""End-to-end through FastAPI: auth, security, uploads, agent, memory consent, admin."""
import io

from PIL import Image

from backend.app.config import Settings
from backend.app.services import model_router
from tests.conftest import auth, sample


def test_register_login_and_bad_password(client):
    h = auth(client, "a@test.local")
    assert client.get("/api/auth/me", headers=h).json()["email"] == "a@test.local"
    r = client.post("/api/auth/login", json={"email": "a@test.local", "password": "wrong1234"})
    assert r.status_code == 401 and r.json()["detail"] == "Wrong email or password"
    r = client.post("/api/auth/login", json={"email": "nobody@test.local", "password": "wrong1234"})
    assert r.json()["detail"] == "Wrong email or password"  # same message: no account enumeration


def test_sql_injection_and_weak_password(client):
    r = client.post("/api/auth/login", json={"email": "' OR 1=1 --@x.com", "password": "x"})
    assert r.status_code in (401, 422)
    r = client.post("/api/auth/register", json={"name": "W", "email": "w@test.local", "password": "abcdefgh"})
    assert r.status_code == 422


def test_report_upload_ownership_404(client):
    h1, h2 = auth(client, "own1@test.local"), auth(client, "own2@test.local")
    rep = client.post("/api/reports", headers=h1, files={"file": ("cbc.txt", sample("cbc_report.txt"), "text/plain")}).json()
    assert rep["summary"]["flagged"] >= 6
    assert client.get(f"/api/reports/{rep['id']}", headers=h2).status_code == 404
    # another user cannot use it in chat either
    r = client.post("/api/chat", headers=h2, json={"message": "explain my report", "report_id": rep["id"]}).json()
    assert "couldn't find" in r["answer"].lower() or "not found" in r["answer"].lower()


def test_agent_offline_template_is_verified(client):
    h = auth(client, "offline@test.local")
    rep = client.post("/api/reports", headers=h, files={"file": ("m.txt", sample("metabolic_report.txt"), "text/plain")}).json()
    r = client.post("/api/chat", headers=h, json={"message": "Please explain my blood report", "report_id": rep["id"]}).json()
    assert r["status"] == "ok" and r["verify"]["passed"]
    assert "LDL cholesterol" in r["answer"] and "not a diagnosis" in r["answer"]
    assert [s["node"] for s in r["steps"]] == ["guard_input", "load_memory", "plan", "act", "explain", "verify", "guard_output", "translate"]


def test_agent_blocks_emergency_without_running_tools(client):
    h = auth(client, "emerg@test.local")
    r = client.post("/api/chat", headers=h, json={"message": "I have crushing chest pain right now"}).json()
    assert r["status"] == "blocked" and "112" in r["answer"] and len(r["steps"]) == 1


def test_agent_with_llm_and_verifier_retry(client):
    """First draft contains a wrong number → verifier rejects → second draft is correct."""
    drafts = iter([
        '{"intent": "lab_report", "terms": [], "research_query": "", "remember": ""}',
        "Your LDL cholesterol is 300 mg/dL.",  # wrong
        "Your LDL cholesterol is high at 154 mg/dL. Please talk to your doctor.",  # right
    ])

    def transport(url, headers, body, timeout):
        if body is None:  # model list
            return 200, {"models": [{"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]}]}
        return 200, {"candidates": [{"content": {"parts": [{"text": next(drafts)}]}}]}

    model_router.set_router(model_router.ModelRouter(Settings(_env_file=None, gemini_api_key_1="g1-test-key"), transport))
    try:
        h = auth(client, "retry@test.local")
        rep = client.post("/api/reports", headers=h, files={"file": ("m.txt", sample("metabolic_report.txt"), "text/plain")}).json()
        r = client.post("/api/chat", headers=h, json={"message": "What is wrong in my report?", "report_id": rep["id"]}).json()
        nodes = [s["node"] for s in r["steps"]]
        assert nodes.count("explain") == 2 and nodes.count("verify") == 2
        assert r["verify"]["passed"] and "300" not in r["answer"]
        assert any(p.startswith("planner:gemini#1") for p in r["providers"])
    finally:
        model_router.set_router(None)


def test_memory_requires_consent_and_delete_everything(client):
    h = auth(client, "mem@test.local")
    r = client.post("/api/chat", headers=h, json={"message": "Remember that I am vegetarian"}).json()
    assert "memory is off" in r["answer"].lower()
    assert client.post("/api/memory/profile", headers=h, json={"sex": "female"}).status_code == 409
    client.post("/api/memory/consent", headers=h, json={"consent": True})
    r = client.post("/api/chat", headers=h, json={"message": "Remember that I am vegetarian"}).json()
    assert "saved" in r["answer"].lower()
    client.post("/api/reports", headers=h, files={"file": ("cbc.txt", sample("cbc_report.txt"), "text/plain")})
    items = client.get("/api/memory", headers=h).json()["items"]
    assert {i["kind"] for i in items} == {"fact", "report"}
    # short-term memory: second message in the same thread sees the first
    r1 = client.post("/api/chat", headers=h, json={"message": "What is ferritin?"}).json()
    r2 = client.post("/api/chat", headers=h, json={"message": "And what if it is low?", "thread_id": r1["thread_id"]}).json()
    assert r2["steps"][1]["short_term"] == 2
    client.delete("/api/memory/all", headers=h)
    assert client.get("/api/memory", headers=h).json() == {"consent": False, "profile": {}, "items": []}
    assert client.get("/api/chat/threads", headers=h).json() == []


def test_mri_rejects_colour_photo_and_reports_missing_model(client):
    h = auth(client, "mri@test.local")
    buf = io.BytesIO()
    Image.new("RGB", (128, 128), (220, 40, 40)).save(buf, "PNG")
    assert client.post("/api/mri", headers=h, files={"file": ("photo.png", buf.getvalue(), "image/png")}).status_code == 422
    buf = io.BytesIO()
    Image.radial_gradient("L").convert("RGB").save(buf, "PNG")
    r = client.post("/api/mri", headers=h, files={"file": ("scan.png", buf.getvalue(), "image/png")}).json()
    assert r["available"] is False and "train" in r["message"].lower()


def test_xss_sanitised_and_admin_only_routes(client):
    h = auth(client, "xss@test.local")
    r = client.post("/api/chat", headers=h, json={"message": "<script>alert(1)</script>What is HDL?"}).json()
    thread = client.get(f"/api/chat/threads/{r['thread_id']}", headers=h).json()
    assert "<script>" not in thread["messages"][0]["content"]
    assert client.get("/api/admin/review", headers=h).status_code == 404
    ha = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"email": "admin@test.local", "password": "Admin12345"}).json()["token"]}
    rev = client.get("/api/admin/review", headers=ha).json()
    assert {c["check"] for c in rev["checks"]} >= {"Password hashing", "Memory consent respected", "LLM key pool"}
    assert next(c for c in rev["checks"] if c["check"] == "Memory consent respected")["status"] == "pass"


def test_rag_retrieval():
    from backend.app.services import rag

    assert rag.retrieve("what is a high TSH", k=3)[0].id in {"lab:tsh", "hypothyroidism"}
    assert rag.retrieve("tumour in the lining of the brain", k=1)[0].id == "meningioma"
    assert "meningioma" in [d.id for d in rag.retrieve("tumour of the meninges", k=2)]
