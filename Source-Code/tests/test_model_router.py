"""The 6-key pool must keep answering when keys or model names fail (v2: auto model detection)."""
import pytest

from backend.app.config import Settings
from backend.app.services.model_router import AllProvidersFailed, ModelRouter, rank_models

GEMINI_LIST = {"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]} for n in
                          ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.8-flash-tts", "gemini-flash-latest", "gemini-3.1-pro-preview",
                           "gemini-3.5-flash-lite", "gemma-4-31b-it", "nano-banana-pro-preview"]]}
GROQ_LIST = {"data": [{"id": n} for n in ["whisper-large-v3", "openai/gpt-oss-20b", "openai/gpt-oss-120b", "meta-llama/llama-prompt-guard-2-86m"]]}


def settings(**kw):
    base = dict(gemini_api_key_1="g1-aaaaaaaa", gemini_api_key_2="g2-aaaaaaaa", gemini_api_key_3="g3-aaaaaaaa",
                grok_api_key_1="gsk_aaaaaaaa", grok_api_key_2="xai-aaaaaaaa", grok_api_key_3="gsk_bbbbbbbb")
    base.update(kw)
    return Settings(_env_file=None, **base)


class Fake:
    """Answers model-list GETs; fails chat calls for keys in `fail`; `retired` model names return 404."""

    def __init__(self, fail=None, retired=(), text="hello", parts=None):
        self.fail, self.retired, self.text, self.parts = fail or {}, set(retired), text, parts
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        key = headers.get("x-goog-api-key") or headers["Authorization"].split()[-1]
        if body is None:  # list models
            return 200, GEMINI_LIST if "generativelanguage" in url else GROQ_LIST
        model = url.split("/models/")[1].split(":")[0] if "generativelanguage" in url else body["model"]
        self.calls.append((key, model))
        if key in self.fail:
            return self.fail[key], {"error": {"message": "nope"}}
        if model in self.retired:
            return 404, {"error": {"message": f"models/{model} is no longer available"}}
        if "generativelanguage" in url:
            return 200, {"candidates": [{"content": {"parts": self.parts or [{"text": self.text}]}, "finishReason": "STOP"}]}
        return 200, {"choices": [{"message": {"content": self.text}}]}


def test_ranking_prefers_current_flash_and_skips_tts_image_gemma():
    names = [m["name"] for m in GEMINI_LIST["models"]]
    ranked = rank_models("gemini", names)
    assert ranked[0] == "gemini-flash-latest" and ranked[1] == "gemini-3.8-flash"
    assert not any(x in " ".join(ranked) for x in ("tts", "gemma", "nano-banana"))
    assert rank_models("groq", [d["id"] for d in GROQ_LIST["data"]])[0] == "openai/gpt-oss-120b"


def test_six_slots_in_order_and_provider_detection():
    r = ModelRouter(settings(), Fake())
    assert [s.label for s in r.slots] == ["gemini#1", "gemini#2", "gemini#3", "groq#1", "grok#2", "groq#3"]
    assert r.slots[3].provider == "groq" and r.slots[4].provider == "xai"


def test_auto_detect_answers_without_any_model_name():
    f = Fake()
    res = ModelRouter(settings(), f).complete("sys", "hi")
    assert res.slot == "gemini#1" and res.model == "gemini-flash-latest"


def test_retired_model_name_heals_itself():
    """v1 bug: GEMINI_MODEL pointed at a retired model → every call 404. v2 falls through to a detected model."""
    f = Fake(retired={"gemini-2.0-flash", "gemini-flash-latest"})
    r = ModelRouter(settings(gemini_model="gemini-2.0-flash", gemini_fallback_model="gemini-2.0-flash"), f)
    res = r.complete("s", "hi")
    assert res.slot == "gemini#1" and res.model == "gemini-3.8-flash"
    assert r.slots[0].active_model == "gemini-3.8-flash"
    assert any(e.get("status") == 404 for e in r.slots[0].log)  # the admin page shows why


def test_thinking_parts_are_skipped_and_empty_answer_moves_on():
    f = Fake(parts=[{"text": "internal reasoning", "thought": True}, {"text": "visible answer"}])
    assert ModelRouter(settings(), f).complete("s", "hi").text == "visible answer"
    empty = Fake(parts=[{"text": "only thinking", "thought": True}])
    r = ModelRouter(settings(gemini_api_key_2="", gemini_api_key_3=""), empty)
    res = r.complete("s", "hi")
    assert res.slot == "groq#1"  # Gemini returned no visible text → next provider
    assert "empty answer" in r.slots[0].public(0)["last_error"]


def test_rate_limited_key_falls_through_and_cools_down():
    r = ModelRouter(settings(), Fake({"g1-aaaaaaaa": 429}))
    assert r.complete("s", "hi").slot == "gemini#2"
    assert r.status()[0]["status"].startswith("cooling")
    assert r.complete("s", "hi").slot == "gemini#2"


def test_invalid_key_400_gets_long_cooldown_and_grok_answers():
    class BadKey(Fake):
        def __call__(self, url, headers, body, timeout):
            if body is not None and "generativelanguage" in url:
                return 400, {"error": {"message": "API key not valid. Please pass a valid API key."}}
            return super().__call__(url, headers, body, timeout)

    r = ModelRouter(settings(), BadKey())
    assert r.complete("s", "hi").slot == "groq#1"
    assert all(s.cooldown_until > 0 for s in r.slots[:3])


def test_admin_controls_disable_slot_and_override_models():
    f = Fake()
    r = ModelRouter(settings(), f)
    r.set_enabled("gemini#1", False)
    r.set_models("gemini", ["gemini-2.5-flash"])
    res = r.complete("s", "hi")
    assert res.slot == "gemini#2" and res.model == "gemini-2.5-flash"
    assert r.status()[0]["status"] == "disabled"


def test_test_slot_reports_model_and_errors():
    r = ModelRouter(settings(), Fake(retired={"gemini-flash-latest"}))
    out = r.test_slot("gemini#1")
    assert out["ok"] and out["model"] == "gemini-3.8-flash" and any("404" in e for e in out["errors"])


def test_invalid_json_counts_as_failure_in_json_mode():
    class JsonFake(Fake):
        def __call__(self, url, headers, body, timeout):
            if body is None:
                return super().__call__(url, headers, body, timeout)
            if "generativelanguage" in url:
                return 200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
            return 200, {"choices": [{"message": {"content": '{"intent": "term"}'}}]}

    res = ModelRouter(settings(), JsonFake()).complete("s", "hi", json_mode=True)
    assert res.slot == "groq#1" and res.json()["intent"] == "term"


def test_everything_down_raises():
    keys = ["g1-aaaaaaaa", "g2-aaaaaaaa", "g3-aaaaaaaa", "gsk_aaaaaaaa", "xai-aaaaaaaa", "gsk_bbbbbbbb"]
    with pytest.raises(AllProvidersFailed):
        ModelRouter(settings(), Fake({k: 500 for k in keys})).complete("s", "hi")


def test_no_keys_configured_raises():
    empty = Settings(_env_file=None, gemini_api_key_1="", gemini_api_key_2="", gemini_api_key_3="",
                     grok_api_key_1="", grok_api_key_2="", grok_api_key_3="")
    with pytest.raises(AllProvidersFailed):
        ModelRouter(empty, Fake()).complete("s", "hi")


# ---------------------------------------------------------------- v2.1: Gemini timeouts and per-model quota
import httpx  # noqa: E402

from backend.app.services.model_router import thinking_config  # noqa: E402


def test_thinking_is_kept_low_so_answers_arrive_in_time():
    assert thinking_config("gemini-flash-latest") == {"thinkingLevel": "low"}
    assert thinking_config("gemini-3.8-flash") == {"thinkingLevel": "low"}
    assert thinking_config("gemini-2.5-flash") == {"thinkingBudget": 0}
    assert thinking_config("openai/gpt-oss-120b") is None
    f = Fake()
    ModelRouter(settings(), f).complete("s", "hi")
    seen = {}

    class Spy(Fake):
        def __call__(self, url, headers, body, timeout):
            if body is not None and "generativelanguage" in url:
                seen.update(body["generationConfig"])
            return super().__call__(url, headers, body, timeout)

    ModelRouter(settings(), Spy()).complete("s", "hi")
    assert seen["thinkingConfig"] == {"thinkingLevel": "low"}


def test_model_rejecting_thinking_setting_is_retried_without_it():
    class NoThink(Fake):
        def __call__(self, url, headers, body, timeout):
            if body is not None and "thinkingConfig" in body.get("generationConfig", {}):
                return 400, {"error": {"message": "Thinking level is not supported for this model."}}
            return super().__call__(url, headers, body, timeout)

    res = ModelRouter(settings(), NoThink()).complete("s", "hi")
    assert res.slot == "gemini#1" and res.text == "hello"


def test_quota_429_tries_another_model_on_the_same_key():
    """Screenshot bug: gemini#2 '429 You exceeded your current quota' on one model. Free-tier quota is per model."""
    class Quota(Fake):
        def __call__(self, url, headers, body, timeout):
            if body is not None and "generativelanguage" in url and "flash-latest" in url:
                return 429, {"error": {"message": "You exceeded your current quota, please check your plan."}}
            return super().__call__(url, headers, body, timeout)

    r = ModelRouter(settings(), Quota())
    res = r.complete("s", "hi")
    assert res.slot == "gemini#1" and res.model == "gemini-3.8-flash"
    assert r.status()[0]["status"] == "ready" and "gemini-flash-latest" in r.status()[0]["models_cooling"]
    assert r.complete("s", "hi").model == "gemini-3.8-flash"  # cooled model is not retried first


def test_timeout_moves_on_quickly_and_is_not_repeated_on_every_key():
    """Screenshot bug: gemini#1 and #3 'gemini-flash-latest · ReadTimeout' → each answer waited ~3 timeouts."""
    class Slow(Fake):
        def __call__(self, url, headers, body, timeout):
            if body is not None and "generativelanguage" in url:
                self.calls.append(("gem", url.split("/models/")[1].split(":")[0]))
                raise httpx.ReadTimeout("read timed out")
            return super().__call__(url, headers, body, timeout)

    f = Slow()
    r = ModelRouter(settings(), f)
    res = r.complete("s", "hi")
    assert res.slot == "groq#1"
    gem_calls = [m for k, m in f.calls if k == "gem"]
    assert len(gem_calls) == len(set(gem_calls)) <= 6  # no model timed out twice in one request
    assert "timed out" in r.status()[0]["last_error"]


def test_success_clears_cooldown_and_review_flags_failing_keys():
    r = ModelRouter(settings(), Fake({"g1-aaaaaaaa": 500}))
    r.complete("s", "hi")
    st = r.status()
    assert st[0]["failures"] and not st[0]["healthy"] and st[1]["healthy"]
