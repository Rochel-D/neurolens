"""Model router v2 — one public call, `complete()`, hiding a 6-key failover pool.

Order: Gemini key 1 → 2 → 3 → Grok/Groq key 1 → 2 → 3 (admin can disable slots or change models).

What changed in v2 (why Gemini did not work in v1):
* Model names are no longer hard-coded. Each key asks its provider which models it can use
  (ListModels), ranks them, and remembers the one that worked. Retired names (404) heal themselves.
* "Thinking" models (Gemini 2.5+/3.x, gpt-oss) spend output tokens on hidden reasoning. v1 capped
  output at 300–900 tokens, so answers could come back empty. v2 uses a high cap and skips thought parts.
* Every attempt is logged per key (model, HTTP status, message) so the admin page shows the real cause.

Failure handling (v2.1 — Gemini timeouts and per-model quota):
* Gemini is asked to think little (thinkingLevel=low for 3.x/"latest", thinkingBudget=0 for 2.5 Flash);
  if a model rejects that setting (400 mentioning thinking) the call is repeated once without it.
* 429 → that model cools down on that key; the next model on the same key is tried (free-tier quota
  is per model). Only when every model of the key is rate-limited does the whole key cool down.
* timeout → that model cools down on that key, one other model is tried, and the model is skipped on
  the remaining keys for this request (no more three 30-second waits in a row).
* 5xx → key cools down (LLM_COOLDOWN_S), next key. A success clears the key's cooldown.
* 401 / 403 / "API key not valid" → key cools down for an hour, next key.
* 404 model not found → next model for the same key (auto-detected list).
* empty answer / invalid JSON / other 400 → next key, no cooldown.
* All keys fail → AllProvidersFailed; callers fall back to deterministic answers.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from ..config import Settings, get_settings

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_BASE = "https://api.groq.com/openai/v1"
XAI_BASE = "https://api.x.ai/v1"
OUTPUT_CAP = 8192  # room for hidden reasoning tokens + the visible answer
# Used only when a key's model list cannot be fetched (network/proxy) and no model is configured.
FALLBACK_MODELS = {"gemini": ["gemini-flash-latest", "gemini-2.5-flash"], "groq": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"],
                   "xai": ["grok-3-mini"]}


class AllProvidersFailed(RuntimeError):
    pass


class ProviderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


Transport = Callable[[str, dict, dict | None, float], tuple[int, dict]]


def _http(url: str, headers: dict, body: dict | None, timeout: float) -> tuple[int, dict]:
    r = httpx.post(url, headers=headers, json=body, timeout=timeout) if body is not None else httpx.get(url, headers=headers, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = {"error": {"message": r.text[:200]}}
    return r.status_code, data


def parse_json(text: str) -> dict:
    """Parse model JSON, tolerating ```json fences or text around the object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


# ------------------------------------------------------------------ model ranking
_GEMINI_SKIP = ("tts", "image", "embedding", "aqa", "live", "audio", "transcribe", "robotics", "computer-use",
                "omni", "nano-banana", "lyria", "veo", "imagen", "deep-research", "antigravity", "customtools", "gemma", "learnlm")
_OPENAI_SKIP = ("whisper", "guard", "safeguard", "orpheus", "tts", "playai", "distil", "embedding", "image", "vision-preview", "allam")


def _version(name: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def thinking_config(model: str) -> dict | None:
    """Keep hidden reasoning short so answers arrive within the timeout."""
    m = model.lower()
    if not m.startswith("gemini"):
        return None
    if "latest" in m or _version(m) >= 3:
        return {"thinkingLevel": "low"}
    if "2.5" in m:
        return {"thinkingBudget": 128 if "pro" in m else 0}  # 2.5 Pro cannot switch thinking off
    return None


def rank_models(provider: str, names: list[str]) -> list[str]:
    names = list(dict.fromkeys(n.removeprefix("models/") for n in names))  # Groq ids keep "openai/…"
    if provider == "gemini":
        ok = [n for n in names if n.startswith("gemini") and not any(s in n for s in _GEMINI_SKIP)]

        def key(n: str):
            return (
                0 if n == "gemini-flash-latest" else 1,
                0 if "flash" in n and "lite" not in n else 1 if "flash" in n else 2,  # flash > flash-lite > pro
                1 if ("preview" in n or "exp" in n) else 0,  # stable before preview
                -_version(n),  # newest first
            )
    else:
        ok = [n for n in names if not any(s in n.lower() for s in _OPENAI_SKIP)]
        pref = ["gpt-oss-120b", "llama-4-maverick", "llama-3.3-70b", "qwen", "kimi", "llama-4-scout", "gpt-oss-20b", "grok-4", "grok-3", "mini", "fast"]

        def key(n: str):
            low = n.lower()
            return (next((i for i, p in enumerate(pref) if p in low), len(pref)), -_version(low))

    return sorted(ok, key=key)


# ------------------------------------------------------------------ slots
@dataclass
class KeySlot:
    label: str  # gemini#1 / groq#2 / grok#3
    provider: str  # gemini | groq | xai
    key: str
    base_url: str
    preferred: list[str]  # from .env / admin; may be empty or ["auto"]
    enabled: bool = True
    cooldown_until: float = 0.0
    successes: int = 0
    failures: int = 0
    active_model: str = ""  # last model that answered
    discovered: list[str] = field(default_factory=list)
    discovered_at: float = 0.0
    log: deque = field(default_factory=lambda: deque(maxlen=12))
    model_cooldown: dict = field(default_factory=dict)  # model -> monotonic time it may be retried
    last_ok_at: float = 0.0
    last_fail_at: float = 0.0

    @property
    def kind(self) -> str:
        return "gemini" if self.provider == "gemini" else "openai"

    def available(self, t: float) -> bool:
        return self.enabled and t >= self.cooldown_until

    def public(self, t: float) -> dict:
        if not self.enabled:
            status = "disabled"
        elif t < self.cooldown_until:
            status = f"cooling {int(self.cooldown_until - t)}s"
        else:
            status = "ready"
        last_err = next((e for e in reversed(self.log) if not e.get("ok")), None)
        return {
            "slot": self.label,
            "provider": self.provider,
            "host": self.base_url.split("//")[1].split("/")[0],
            "key": self.key[:4] + "…" + self.key[-3:] if len(self.key) > 8 else "set",
            "preferred": self.preferred,
            "active_model": self.active_model,
            "status": status,
            "enabled": self.enabled,
            "successes": self.successes,
            "failures": self.failures,
            "last_error": f"{last_err['model']}: {last_err['error']}" if last_err else "",
            "log": list(self.log),
            "discovered": self.discovered[:25],
            "models_cooling": sorted(m for m, u in self.model_cooldown.items() if u > t),
            "healthy": self.successes > 0 and self.last_ok_at >= self.last_fail_at,
        }


@dataclass
class LLMResult:
    text: str
    slot: str
    model: str
    latency_ms: int
    attempts: list[dict] = field(default_factory=list)

    def json(self) -> dict:
        return parse_json(self.text)


class ModelRouter:
    def __init__(self, settings: Settings | None = None, transport: Transport | None = None):
        self.s = settings or get_settings()
        self.transport = transport or _http
        self._lock = threading.Lock()
        self.slots: list[KeySlot] = []
        gem_pref = [m for m in (self.s.gemini_model, self.s.gemini_fallback_model) if m and m != "auto"]
        for i, k in enumerate(self.s.gemini_keys, 1):
            self.slots.append(KeySlot(f"gemini#{i}", "gemini", k, GEMINI_BASE, list(gem_pref)))
        for i, k in enumerate(self.s.grok_keys, 1):
            if k.startswith("xai-"):
                pref = [m for m in (self.s.xai_model,) if m and m != "auto"]
                self.slots.append(KeySlot(f"grok#{i}", "xai", k, XAI_BASE, pref))
            else:  # gsk_… and unknown prefixes → Groq
                pref = [m for m in (self.s.groq_model,) if m and m != "auto"]
                self.slots.append(KeySlot(f"groq#{i}", "groq", k, GROQ_BASE, pref))

    # ---------------- public ----------------
    @property
    def configured(self) -> bool:
        return bool(self.slots)

    def slot(self, label: str) -> KeySlot | None:
        return next((s for s in self.slots if s.label == label), None)

    def status(self) -> list[dict]:
        t = time.monotonic()
        return [s.public(t) for s in self.slots]

    def set_models(self, provider: str, models: list[str]) -> None:
        """Admin override: preferred model names for every key of one provider (empty = auto-detect)."""
        for s in self.slots:
            if s.provider == provider:
                s.preferred = [m for m in models if m and m != "auto"]
                s.active_model = ""

    def set_enabled(self, label: str, enabled: bool) -> None:
        s = self.slot(label)
        if s:
            s.enabled = enabled

    def reset(self, label: str | None = None) -> None:
        for s in self.slots:
            if label in (None, s.label):
                s.cooldown_until = 0.0
                s.active_model = ""
                s.model_cooldown.clear()

    def discover(self, slot: KeySlot, force: bool = False) -> list[str]:
        """Ask the provider which models this key can use (cached for an hour)."""
        if not force and slot.discovered_at and time.monotonic() - slot.discovered_at < (3600 if slot.discovered else 60):
            return slot.discovered  # cached list, or a recent failure (retry after a minute)
        try:
            if slot.provider == "gemini":
                status, data = self.transport(f"{slot.base_url}/models?pageSize=200", {"x-goog-api-key": slot.key}, None, self.s.llm_timeout_s)
                names = [m["name"] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
            else:
                status, data = self.transport(f"{slot.base_url}/models", {"Authorization": f"Bearer {slot.key}"}, None, self.s.llm_timeout_s)
                names = [m["id"] for m in data.get("data", []) if m.get("active", True)]
            if status != 200:
                raise ProviderError(status, str(data.get("error", {}).get("message", ""))[:160])
            slot.discovered = rank_models(slot.provider, names)
            slot.discovered_at = time.monotonic()
        except (httpx.HTTPError, ProviderError, KeyError, ValueError, TypeError) as e:
            slot.discovered_at = time.monotonic()
            slot.log.append({"t": time.strftime("%H:%M:%S"), "model": "(list models)", "ok": False, "error": str(e)[:160]})
        return slot.discovered

    def candidates(self, slot: KeySlot, need_vision: bool = False, skip: set | None = None) -> list[str]:
        order = [slot.active_model] if slot.active_model else []
        order += slot.preferred
        order += self.discover(slot)[:4] or FALLBACK_MODELS.get(slot.provider, [])
        now = time.monotonic()
        seen, out = set(skip or ()), []
        for m in order:
            if m and m not in seen and slot.model_cooldown.get(m, 0) <= now:
                seen.add(m)
                out.append(m)
        return out[:6]

    def complete(self, system: str, messages: list[dict] | str, *, json_mode: bool = False, temperature: float = 0.3,
                 max_tokens: int = 1024, image: tuple[bytes, str] | None = None, providers: tuple[str, ...] | None = None) -> LLMResult:
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        attempts: list[dict] = []
        if not self.slots:
            raise AllProvidersFailed("No API keys configured (set GEMINI_API_KEY_1.. / GROK_API_KEY_1.. in .env)")
        timed_out: set[str] = set()  # models that timed out in this request are skipped on later keys
        for slot in self.slots:
            if providers and slot.provider not in providers:
                continue
            if not slot.available(time.monotonic()):
                attempts.append({"slot": slot.label, "skipped": "disabled" if not slot.enabled else "cooling down"})
                continue
            tried_404, rate_limited, timeouts, tried = False, 0, 0, 0
            for model in self.candidates(slot, skip=timed_out):
                tried += 1
                start = time.perf_counter()
                try:
                    text = self._call(slot, model, system, messages, json_mode, temperature, max_tokens, image)
                    if json_mode:
                        parse_json(text)
                    ms = int((time.perf_counter() - start) * 1000)
                    with self._lock:
                        slot.successes += 1
                        slot.active_model = model
                        slot.cooldown_until = 0.0
                        slot.model_cooldown.pop(model, None)
                        slot.last_ok_at = time.monotonic()
                    slot.log.append({"t": time.strftime("%H:%M:%S"), "model": model, "ok": True, "ms": ms})
                    attempts.append({"slot": slot.label, "model": model, "ok": True, "ms": ms})
                    return LLMResult(text, slot.label, model, ms, attempts)
                except ProviderError as e:
                    err = e
                except httpx.TimeoutException as e:
                    err = ProviderError(408, f"{type(e).__name__}: timed out after {self.s.llm_timeout_s:.0f}s")
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
                    err = ProviderError(-1 if isinstance(e, (ValueError, KeyError, IndexError, TypeError)) else 0, f"{type(e).__name__}: {e}"[:160])
                self._record(slot, model, err, attempts)
                if err.status == 404:
                    tried_404 = True
                    if slot.active_model == model:
                        slot.active_model = ""
                    continue  # next model on the same key
                if err.status == 429:
                    rate_limited += 1
                    slot.model_cooldown[model] = time.monotonic() + self.s.llm_cooldown_s
                    if slot.active_model == model:
                        slot.active_model = ""
                    if rate_limited < 3 and not re.search(r"billing|project", err.message, re.I):
                        continue  # free-tier quota is per model: try another model on this key
                    with self._lock:
                        slot.cooldown_until = time.monotonic() + self.s.llm_cooldown_s
                if err.status == 408:
                    timeouts += 1
                    timed_out.add(model)
                    slot.model_cooldown[model] = time.monotonic() + 5 * self.s.llm_cooldown_s
                    if slot.active_model == model:
                        slot.active_model = ""
                    if timeouts < 2:
                        continue  # one other model on this key, then move on
                    with self._lock:
                        slot.cooldown_until = time.monotonic() + self.s.llm_cooldown_s
                break
            else:
                if not tried:
                    attempts.append({"slot": slot.label, "skipped": "every model resting (quota/timeout)"})
                if rate_limited and rate_limited == tried:
                    with self._lock:  # every model of this key is rate-limited
                        slot.cooldown_until = time.monotonic() + self.s.llm_cooldown_s
                if tried_404 and not slot.discovered:
                    self.discover(slot, force=True)
        raise AllProvidersFailed(f"All {len(self.slots)} keys failed or cooling down: {attempts}")

    def test_slot(self, label: str) -> dict:
        """Admin 'Test key': refresh the model list and send a tiny prompt, ignoring cooldown."""
        slot = self.slot(label)
        if slot is None:
            return {"ok": False, "error": "unknown slot"}
        saved = (slot.cooldown_until, slot.enabled)
        slot.cooldown_until, slot.enabled = 0.0, True
        self.discover(slot, force=True)
        result: dict = {"slot": label, "discovered": slot.discovered[:15]}
        try:
            slot.active_model = ""
            for model in self.candidates(slot):
                try:
                    text = self._call(slot, model, "Reply with exactly one word.", [{"role": "user", "content": "Say: ready"}], False, 0.0, 50, None)
                    slot.active_model = model
                    slot.successes += 1
                    slot.last_ok_at = time.monotonic()
                    slot.log.append({"t": time.strftime("%H:%M:%S"), "model": model, "ok": True, "ms": 0})
                    result.update(ok=True, model=model, reply=text.strip()[:60])
                    break
                except ProviderError as e:
                    slot.log.append({"t": time.strftime("%H:%M:%S"), "model": model, "ok": False, "error": e.message[:160], "status": e.status})
                    result.setdefault("errors", []).append(f"{model}: {e.status} {e.message[:120]}")
                    if e.status not in (404, 429):
                        break
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
                    msg = f"{type(e).__name__}: {str(e)[:100]}"
                    slot.log.append({"t": time.strftime("%H:%M:%S"), "model": model, "ok": False, "error": msg})
                    result.setdefault("errors", []).append(f"{model}: {msg}")
                    if not isinstance(e, httpx.TimeoutException):
                        break
            result.setdefault("ok", False)
            if not result["ok"] and not result.get("errors"):
                last = next((e for e in reversed(slot.log) if not e.get("ok")), None)
                result["errors"] = [f"{last['model']}: {last['error']}" if last else "no model could be tried"]
        finally:
            if not result.get("ok"):
                slot.cooldown_until, slot.enabled = saved
            else:
                slot.enabled = saved[1]
        return result

    # ---------------- internals ----------------
    def _record(self, slot: KeySlot, model: str, e: ProviderError, attempts: list) -> None:
        msg = e.message
        bad_key = e.status in (401, 403) or (e.status == 400 and re.search(r"api key|API_KEY_INVALID|credential", msg, re.I))
        with self._lock:
            slot.failures += 1
            slot.last_fail_at = time.monotonic()
            if bad_key:
                slot.cooldown_until = time.monotonic() + self.s.llm_bad_key_cooldown_s
            elif e.status == 0 or e.status >= 500:
                slot.cooldown_until = time.monotonic() + self.s.llm_cooldown_s
            # 408 timeouts and 429 quota errors are handled per model in complete()
        slot.log.append({"t": time.strftime("%H:%M:%S"), "model": model, "ok": False, "status": e.status, "error": msg[:160]})
        attempts.append({"slot": slot.label, "model": model, "error": e.status, "message": msg[:100]})

    def _call(self, slot, model, system, messages, json_mode, temperature, max_tokens, image) -> str:
        cap = max(OUTPUT_CAP, max_tokens)
        if slot.kind == "gemini":
            url = f"{slot.base_url}/models/{model}:generateContent"
            headers = {"x-goog-api-key": slot.key, "Content-Type": "application/json"}
            cfg = {"temperature": temperature, "maxOutputTokens": cap}
            if json_mode:
                cfg["responseMimeType"] = "application/json"
            contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages]
            if image is not None:
                data, mime = image
                contents[-1]["parts"].insert(0, {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
            think = thinking_config(model)
            if think:
                cfg["thinkingConfig"] = think
            body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": cfg}
            status, data = self.transport(url, headers, body, self.s.llm_timeout_s * (2 if image else 1))
            if status == 400 and think and re.search(r"think", str(data.get("error", {}).get("message", "")), re.I):
                cfg.pop("thinkingConfig")  # this model does not accept the setting: ask again without it
                status, data = self.transport(url, headers, body, self.s.llm_timeout_s * (2 if image else 1))
            if status != 200:
                raise ProviderError(status, str(data.get("error", {}).get("message", ""))[:200])
            cands = data.get("candidates") or []
            if not cands:
                reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
                raise ProviderError(-1, f"empty answer ({reason})")
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if not text.strip():
                raise ProviderError(-1, f"empty answer (finishReason={cands[0].get('finishReason', '?')})")
            return text
        if image is not None:
            raise ProviderError(-1, "vision not supported on this provider")
        url = f"{slot.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {slot.key}", "Content-Type": "application/json"}
        body = {"model": model, "messages": [{"role": "system", "content": system}, *messages],
                "temperature": temperature, "max_tokens": min(cap, 8192)}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        status, data = self.transport(url, headers, body, self.s.llm_timeout_s)
        if status != 200:
            raise ProviderError(status, str(data.get("error", {}).get("message", ""))[:200])
        text = data["choices"][0]["message"].get("content") or ""
        if not text.strip():
            raise ProviderError(-1, f"empty answer (finish_reason={data['choices'][0].get('finish_reason', '?')})")
        return text


_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def set_router(router: ModelRouter | None) -> None:
    """Used by tests to inject a router with a fake transport."""
    global _router
    _router = router
