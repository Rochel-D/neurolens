"""Central configuration. Every secret comes from environment variables / .env — never from code."""
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Local runs read .env. On Cloud Run the whole .env is one Secret Manager secret mounted as a file.
    model_config = SettingsConfigDict(
        env_file=(str(ROOT / ".env"), os.getenv("NEUROLENS_ENV_FILE", "/secrets/neurolens.env")), extra="ignore")

    env: str = "dev"  # dev | production
    jwt_secret: str = "dev-only-secret-change-me-dev-only-secret-change-me"
    jwt_minutes: int = 120
    database_url: str = f"sqlite:///{ROOT / 'neurolens.db'}"
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    admin_email: str = "admin@neurolens.local"
    admin_password: str = "Admin12345"

    # --- LLM key pool: 3 Gemini + 3 Grok/Groq keys. Empty keys are skipped. ---
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    # "auto" = ask the key which models it can use and pick the best (recommended)
    gemini_model: str = "auto"
    gemini_fallback_model: str = "auto"

    grok_api_key_1: str = ""
    grok_api_key_2: str = ""
    grok_api_key_3: str = ""
    # Keys starting "gsk_" are Groq (api.groq.com), keys starting "xai-" are xAI Grok (api.x.ai)
    groq_model: str = "auto"
    xai_model: str = "auto"

    llm_timeout_s: float = 45.0
    llm_cooldown_s: int = 60  # rate limit / 5xx cooldown per key
    llm_bad_key_cooldown_s: int = 3600  # 401/403 cooldown per key

    serper_api_key: str = ""

    # --- MCP (existing, official server — no custom server) ---
    use_mcp: bool = True
    mcp_timeout_s: float = 25.0

    # --- Fine-tuned patient-friendly LLM (from the LLM Engineering project) ---
    explainer_mode: str = "router"  # router | local (Qwen + SFT + DPO adapters via peft)
    base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    sft_adapter: str = ""  # local folder or HF Hub repo, e.g. models/sft_r32_a64
    dpo_adapter: str = ""  # e.g. models/dpo_lr1e-5_b0.3

    # --- MRI classifier (neuro module) ---
    mri_model_path: str = str(ROOT / "models" / "mri_classifier.pt")
    mri_min_confidence: float = 0.60

    # Photo reports: gemini (vision) first, then local Tesseract if installed
    ocr_engines: str = "gemini,tesseract"
    export_ttl_hours: int = 24

    max_upload_mb: int = 10
    max_pdf_pages: int = 30
    rate_limit_per_min: int = 30

    @property
    def gemini_keys(self) -> list[str]:
        return [k.strip() for k in (self.gemini_api_key_1, self.gemini_api_key_2, self.gemini_api_key_3) if k.strip()]

    @property
    def grok_keys(self) -> list[str]:
        return [k.strip() for k in (self.grok_api_key_1, self.grok_api_key_2, self.grok_api_key_3) if k.strip()]

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (len(s.jwt_secret) < 32 or s.jwt_secret.startswith("dev-only")):
        raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters in production")
    return s
