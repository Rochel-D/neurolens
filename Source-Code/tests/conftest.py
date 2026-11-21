"""Tests run offline: temp SQLite DB, no real API keys, MCP off. LLM calls use a fake transport."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    USE_MCP="false",
    SERPER_API_KEY="",
    GEMINI_API_KEY_1="", GEMINI_API_KEY_2="", GEMINI_API_KEY_3="",
    GROK_API_KEY_1="", GROK_API_KEY_2="", GROK_API_KEY_3="",
    ADMIN_EMAIL="admin@test.local", ADMIN_PASSWORD="Admin12345",
    MRI_MODEL_PATH=f"{_tmp}/missing.pt",
    EXPLAINER_MODE="router",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.services import model_router  # noqa: E402


@pytest.fixture()
def client():
    from backend.app.main import app

    from backend.app import security

    model_router.set_router(None)
    security._hits.clear()  # rate-limit windows are per process; each test starts fresh
    with TestClient(app) as c:
        yield c


def auth(client, email="user@test.local", password="Passw0rd1", name="Test User"):
    r = client.post("/api/auth/register", json={"name": name, "email": email, "password": password})
    if r.status_code == 409:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def sample(name: str) -> bytes:
    return (ROOT / "sample_data" / "reports" / name).read_bytes()
