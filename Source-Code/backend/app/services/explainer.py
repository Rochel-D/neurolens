"""Explainer — turns verified findings into a Doctor view or a Patient view.

Engines, tried in order:
1. "local": our fine-tuned Qwen2.5-0.5B-Instruct + SFT adapter (r32/α64) + DPO adapter
   (gentle DPO, lr 1e-5, β 0.3) from the LLM Engineering project. Patient view only.
2. "router": the 6-key Gemini/Grok pool, with the same patient-friendly system prompt we
   trained with (or a technical prompt for the Doctor view).
3. "template": deterministic text built from the knowledge base — always works offline.

The engine only ever sees grounded facts produced by tools (RAG + parsed values), which addresses
the main limitation found in the LLM Engineering report: the small model made factual errors
when it answered from its own memory.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from ..config import get_settings
from .model_router import AllProvidersFailed, get_router

# Same system prompt used for SFT/DPO training in the LLM Engineering project.
PATIENT_SYSTEM = (
    "You are a helpful medical educator. Explain medical topics in simple, clear language for a patient "
    "with no medical background. Give general education only. Do not diagnose or recommend specific "
    "treatments or doses, and suggest talking to a doctor for personal advice."
)
PATIENT_RULES = (
    " Use ONLY the facts given between <<< >>>. Do not add numbers that are not in the facts. "
    "Use short sentences and explain any medical word. Keep it under 220 words. Use a few short bullet points "
    "for flagged results. If a value is marked critical, say clearly that they should contact a doctor today. "
    "Cite sources as [1], [2] using the numbered source list."
)
DOCTOR_SYSTEM = (
    "You are a clinical decision-support assistant writing for a doctor. Summarise the structured findings "
    "concisely and technically: abnormal values with reference ranges, likely patterns worth considering "
    "(as differentials, not diagnoses), and reasonable follow-up investigations. Use ONLY the facts given "
    "between <<< >>>; do not invent values. Do not prescribe doses. Cite sources as [1], [2]. Under 220 words."
)


@dataclass
class Explanation:
    text: str
    engine: str
    detail: str = ""


class _LocalModel:
    """Lazy loader for Qwen + SFT + DPO adapters (needs requirements-ml.txt)."""

    _lock = threading.Lock()
    _model = None
    _tok = None
    error = ""

    @classmethod
    def available(cls) -> bool:
        s = get_settings()
        if s.explainer_mode != "local" or not s.sft_adapter:
            return False
        if cls._model is not None:
            return True
        if cls.error:
            return False
        with cls._lock:
            if cls._model is None and not cls.error:
                try:
                    import torch
                    from peft import PeftModel
                    from transformers import AutoModelForCausalLM, AutoTokenizer

                    tok = AutoTokenizer.from_pretrained(s.base_model)
                    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
                    m = AutoModelForCausalLM.from_pretrained(s.base_model, torch_dtype=dtype)
                    for adapter in (s.sft_adapter, s.dpo_adapter):  # SFT first, then DPO on top (two-adapter design)
                        if adapter:
                            m = PeftModel.from_pretrained(m, adapter).merge_and_unload()
                    if torch.cuda.is_available():
                        m = m.to("cuda")
                    cls._model, cls._tok = m.eval(), tok
                except Exception as e:  # noqa: BLE001
                    cls.error = f"{type(e).__name__}: {e}"[:200]
        return cls._model is not None

    @classmethod
    def generate(cls, system: str, user: str, max_new_tokens: int = 320) -> str:
        import torch

        tok, m = cls._tok, cls._model
        text = tok.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tokenize=False,
            add_generation_prompt=True,
        )
        enc = tok(text, return_tensors="pt").to(m.device)
        with torch.no_grad():
            out = m.generate(
                **enc, max_new_tokens=max_new_tokens, do_sample=False, repetition_penalty=1.1, pad_token_id=tok.eos_token_id
            )
        return tok.decode(out[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()


def local_status() -> dict:
    s = get_settings()
    return {
        "mode": s.explainer_mode,
        "base_model": s.base_model,
        "sft_adapter": s.sft_adapter or None,
        "dpo_adapter": s.dpo_adapter or None,
        "loaded": _LocalModel._model is not None,
        "error": _LocalModel.error or None,
    }


def _user_prompt(question: str, facts: str, sources: list[dict], history: str, feedback: str) -> str:
    src = "\n".join(f"[{i}] {s.get('title', '')}" for i, s in enumerate(sources, 1)) or "(none)"
    parts = [f"Question: {question}"]
    if history:
        parts.append(f"Recent conversation (for context only):\n{history}")
    parts.append(f"<<<FACTS\n{facts}\n>>>")
    parts.append(f"Sources:\n{src}")
    if feedback:
        parts.append(f"A checker found problems in your previous draft. Fix them:\n{feedback}")
    return "\n\n".join(parts)


def explain(
    question: str,
    facts: str,
    audience: str = "patient",
    sources: list[dict] | None = None,
    history: str = "",
    feedback: str = "",
    allow_local: bool = True,
    template: str = "",
) -> Explanation:
    sources = sources or []
    user = _user_prompt(question, facts, sources, history, feedback)
    if audience == "patient" and allow_local and _LocalModel.available():
        try:
            return Explanation(_LocalModel.generate(PATIENT_SYSTEM + PATIENT_RULES, user), "fine-tuned Qwen2.5-0.5B (SFT+DPO)")
        except Exception as e:  # noqa: BLE001
            _LocalModel.error = str(e)[:200]
    system = PATIENT_SYSTEM + PATIENT_RULES if audience == "patient" else DOCTOR_SYSTEM
    try:
        r = get_router().complete(system, user, temperature=0.3, max_tokens=900)
        return Explanation(r.text.strip(), f"router · {r.slot} · {r.model}", r.slot)
    except AllProvidersFailed as e:
        return Explanation(template or facts, "template (offline)", str(e)[:120])
