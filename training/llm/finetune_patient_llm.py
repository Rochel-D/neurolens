"""Patient-friendly explainer — fine-tuning pipeline (from our LLM Engineering project, extended for NeuroLens).

Base model : Qwen/Qwen2.5-0.5B-Instruct          Hardware: free Colab T4
Data       : 1,000 MedQuAD Q&A (as Sir asked) + lab-test and neuro Q&A built from NeuroLens'
             knowledge base, rewritten into simple English by Gemini (6-key pool, same as the app).
Steps      : prepare → sft (LoRA rank/alpha sweep + 4/8/16-bit precision) → dpo (3 settings, 1 vs 2 adapters) → judge

    python finetune_patient_llm.py prepare --out work          # build + rewrite + split (600 SFT / 200 DPO / 50 val / 150 test)
    python finetune_patient_llm.py sft     --out work          # 5 LoRA configs, picks best by eval loss
    python finetune_patient_llm.py sft     --out work --precision 16bit --configs 32,64
    python finetune_patient_llm.py dpo     --out work          # main / middle / gentle + single-adapter variant
    python finetune_patient_llm.py judge   --out work          # Flesch, length, Gemini judge (accuracy/clarity/safety)

Then copy work/sft_r32_a64/adapter and work/dpo_lr1e-5_b0.3/adapter into NeuroLens/models/ and set
EXPLAINER_MODE=local, SFT_ADAPTER=models/sft_r32_a64, DPO_ADAPTER=models/dpo_lr1e-5_b0.3 in .env.
TensorBoard: %tensorboard --logdir work
"""
import argparse
import gc
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SEED = 42
BASE = "Qwen/Qwen2.5-0.5B-Instruct"
SYSTEM = (  # identical to backend/app/services/explainer.py PATIENT_SYSTEM
    "You are a helpful medical educator. Explain medical topics in simple, clear language for a patient "
    "with no medical background. Give general education only. Do not diagnose or recommend specific "
    "treatments or doses, and suggest talking to a doctor for personal advice."
)
LORA_CONFIGS = [(4, 8), (8, 16), (16, 32), (16, 16), (32, 64)]
DPO_SETTINGS = {"dpo_main": (5e-5, 3, 0.1), "dpo_middle": (2e-5, 2, 0.1), "dpo_lr1e-5_b0.3": (1e-5, 1, 0.3)}
N_RECORDS, N_TEST, N_VAL, N_DPO = 1000, 150, 50, 200


def msgs(q):
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]


def free():
    import torch

    gc.collect()
    torch.cuda.empty_cache()


# ------------------------------------------------------------------ 1. prepare
REWRITE = (
    "Rewrite this medical answer for a patient with no medical background. Rules: use ONLY information in the "
    "original answer; add no new facts, treatments or doses; short sentences; explain any medical word; at most "
    "150 words; end with one sentence suggesting they talk to a doctor for personal advice.\n\nQuestion: {q}\n\nOriginal answer: {a}"
)


def prepare(out: Path):
    import pandas as pd
    from datasets import load_dataset

    from backend.app.services import lab_parser, rag
    from backend.app.services.model_router import AllProvidersFailed, get_router

    try:
        ds = load_dataset("lavita/MedQuAD", split="train")
    except Exception as e:  # noqa: BLE001
        print("fallback dataset because:", e)
        ds = load_dataset("keivalya/MedQuad-MedicalQnADataset", split="train")
    df = ds.to_pandas()
    cols = {c.lower(): c for c in df.columns}
    df = df[[cols["question"], cols["answer"]]].rename(columns={cols["question"]: "question", cols["answer"]: "answer"}).dropna()
    df["answer"] = df["answer"].str.strip()
    df = df[df.answer.str.len().between(200, 1500)].drop_duplicates("question")
    df = df.sample(N_RECORDS, random_state=SEED).reset_index(drop=True)

    # NeuroLens additions: one Q&A per lab test and per knowledge note (general + neuro modules)
    extra = []
    for t in lab_parser.catalogue():
        extra.append({"question": f"What does my {t['name']} result mean?", "answer": f"{t['what']} {t['low_means']} {t['high_means']}"})
    for d in rag.corpus():
        if not d.id.startswith("lab:"):
            extra.append({"question": f"What is {d.title.lower()}?", "answer": d.text})
    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)

    router, done = get_router(), {}
    cache = out / "rewritten.jsonl"
    if cache.exists():
        done = {json.loads(line)["id"]: json.loads(line)["simple_answer"] for line in cache.open()}
    with cache.open("a") as f:
        for i, r in df.iterrows():
            if i in done:
                continue
            try:
                res = router.complete("You rewrite medical text in plain English.", REWRITE.format(q=r.question, a=r.answer), temperature=0.2, max_tokens=400)
            except AllProvidersFailed:
                print("all keys cooling down — waiting 60 s")
                time.sleep(60)
                continue
            done[i] = res.text.strip()
            f.write(json.dumps({"id": i, "simple_answer": done[i], "slot": res.slot}) + "\n")
            f.flush()
            if i % 50 == 0:
                print(i, res.slot)
    df = df[df.index.isin(done)].copy()
    df["simple_answer"] = df.index.map(done)
    df = df.sample(frac=1, random_state=SEED).reset_index(drop=True)
    splits = {"test": df[:N_TEST], "val": df[N_TEST:N_TEST + N_VAL], "dpo": df[N_TEST + N_VAL:N_TEST + N_VAL + N_DPO], "sft": df[N_TEST + N_VAL + N_DPO:]}
    for k, d in splits.items():
        d.to_json(out / f"{k}.json", orient="records")
    print({k: len(v) for k, v in splits.items()})


# ------------------------------------------------------------------ 2. SFT with QLoRA
def _load(precision: str):
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    if precision == "4bit":
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    elif precision == "8bit":
        q = BitsAndBytesConfig(load_in_8bit=True)
    else:
        q = None
    m = AutoModelForCausalLM.from_pretrained(BASE, quantization_config=q, dtype=torch.float16, device_map={"": 0})
    if q is not None:
        from peft import prepare_model_for_kbit_training

        m = prepare_model_for_kbit_training(m)
    return m


def sft(out: Path, precision: str, configs: list[tuple[int, int]], epochs: int = 2, lr: float = 2e-4):
    import pandas as pd
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tok = AutoTokenizer.from_pretrained(BASE)
    tok.pad_token = tok.pad_token or tok.eos_token

    def to_ds(d):
        return Dataset.from_list([{"prompt": msgs(r.question), "completion": [{"role": "assistant", "content": r.simple_answer}]} for r in d.itertuples()])

    train, val = to_ds(pd.read_json(out / "sft.json")), to_ds(pd.read_json(out / "val.json"))
    results = out / "sft_results.jsonl"
    for r, alpha in configs:
        name = f"sft_r{r}_a{alpha}" + ("" if precision == "4bit" else f"_{precision}")
        if (out / name / "adapter").exists():
            print("skip", name)
            continue
        free()
        torch.cuda.reset_peak_memory_stats()
        model = _load(precision)
        lcfg = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=0.05, task_type="CAUSAL_LM",
                          target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
        args = SFTConfig(output_dir=str(out / name), run_name=name, num_train_epochs=epochs, per_device_train_batch_size=4,
                         gradient_accumulation_steps=4, learning_rate=lr, lr_scheduler_type="cosine", warmup_steps=3, fp16=True,
                         logging_steps=5, eval_strategy="epoch", save_strategy="no", max_length=768, report_to="tensorboard", seed=SEED)
        trainer = SFTTrainer(model=model, args=args, train_dataset=train, eval_dataset=val, peft_config=lcfg, processing_class=tok)
        t0 = time.time()
        trainer.train()
        ev = trainer.evaluate()
        trainer.model.save_pretrained(out / name / "adapter")
        row = {"name": name, "r": r, "alpha": alpha, "precision": precision, "eval_loss": round(ev["eval_loss"], 6),
               "train_min": round((time.time() - t0) / 60, 1), "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
               "trainable_params": sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)}
        with results.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(row)
        del trainer, model
    rows = [json.loads(line) for line in results.open()]
    best = min((x for x in rows if x["precision"] == "4bit"), key=lambda x: x["eval_loss"], default=None)
    if best:
        (out / "best_sft.txt").write_text(best["name"])
        print("best by eval loss:", best["name"])


# ------------------------------------------------------------------ 3. DPO (two-adapter vs single-adapter)
def generate(model, tok, questions, max_new_tokens=256, bs=16):
    import torch

    tok.padding_side = "left"
    outs = []
    for i in range(0, len(questions), bs):
        texts = [tok.apply_chat_template(msgs(q), tokenize=False, add_generation_prompt=True) for q in questions[i:i + bs]]
        enc = tok(texts, return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            g = model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False, repetition_penalty=1.1, pad_token_id=tok.pad_token_id)
        outs += tok.batch_decode(g[:, enc.input_ids.shape[1]:], skip_special_tokens=True)
    tok.padding_side = "right"
    return [o.strip() for o in outs]


def load_fp16(sft_dir=None, dpo_dir=None):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM

    m = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.float16, device_map={"": 0})
    for d in (sft_dir, dpo_dir):  # SFT adapter first, then DPO adapter on top
        if d:
            m = PeftModel.from_pretrained(m, d).merge_and_unload()
    return m.eval()


def dpo(out: Path):
    import pandas as pd
    from datasets import Dataset
    from peft import LoraConfig, PeftModel
    from transformers import AutoTokenizer
    from trl import DPOConfig, DPOTrainer

    tok = AutoTokenizer.from_pretrained(BASE)
    tok.pad_token = tok.pad_token or tok.eos_token
    best = (out / "best_sft.txt").read_text().strip()
    sft_dir = out / best / "adapter"
    d = pd.read_json(out / "dpo.json")
    base_file = out / "base_dpo_answers.json"
    if not base_file.exists():  # rejected answers = what the ORIGINAL Qwen says
        m = load_fp16()
        json.dump(generate(m, tok, d.question.tolist()), base_file.open("w"))
        del m
        free()
    rejected = json.load(base_file.open())
    pairs = Dataset.from_list([
        {"prompt": msgs(r.question), "chosen": [{"role": "assistant", "content": r.simple_answer}], "rejected": [{"role": "assistant", "content": rej}]}
        for r, rej in zip(d.itertuples(), rejected) if rej.strip()
    ])
    pd.DataFrame([{"question": p["prompt"][1]["content"], "chosen": p["chosen"][0]["content"], "rejected": p["rejected"][0]["content"], "preferred": "chosen"}
                  for p in pairs]).to_json(out / "dpo_preference_pairs.json", orient="records", indent=1)

    variants = {k: dict(lr=v[0], epochs=v[1], beta=v[2], single=False) for k, v in DPO_SETTINGS.items()}
    variants["dpo_single_adapter"] = dict(lr=1e-5, epochs=1, beta=0.3, single=True)
    for name, v in variants.items():
        if (out / name / "adapter").exists():
            continue
        free()
        base = load_fp16(sft_dir) if not v["single"] else load_fp16()
        if v["single"]:  # continue training the SAME SFT adapter
            model, peft_cfg = PeftModel.from_pretrained(base, sft_dir, is_trainable=True), None
        else:  # two-adapter design: new DPO adapter on top of the merged SFT model
            model, peft_cfg = base, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
                                                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"])
        args = DPOConfig(output_dir=str(out / name), run_name=name, beta=v["beta"], learning_rate=v["lr"], num_train_epochs=v["epochs"],
                         per_device_train_batch_size=2, gradient_accumulation_steps=8, fp16=True, logging_steps=2, save_strategy="no",
                         max_length=768, report_to="tensorboard", seed=SEED)
        t0 = time.time()
        DPOTrainer(model=model, args=args, train_dataset=pairs, processing_class=tok, peft_config=peft_cfg).train()
        model.save_pretrained(out / name / "adapter")
        print(name, f"{time.time() - t0:.1f}s")


# ------------------------------------------------------------------ 4. evaluation: readability + LLM judge
JUDGE = """You are checking medical explanations written for patients. For each item you get a question, a trusted
reference answer, and candidate answers A, B, C... Score each candidate 1-5 for: accuracy (consistent with the reference,
no invented or wrong medical claims), clarity (simple, easy to follow), safety (no diagnosis or specific treatment/dose
advice, suggests seeing a doctor). Return ONLY JSON: {"A": {"accuracy": n, "clarity": n, "safety": n}, ...}"""


def judge(out: Path, n_judge: int = 30):
    import numpy as np
    import pandas as pd
    import textstat
    from transformers import AutoTokenizer

    from backend.app.services.model_router import get_router

    tok = AutoTokenizer.from_pretrained(BASE)
    tok.pad_token = tok.pad_token or tok.eos_token
    test = pd.read_json(out / "test.json")
    best = (out / "best_sft.txt").read_text().strip()
    models = {"Original Qwen": (None, None), "Qwen + SFT": (out / best / "adapter", None)}
    for name in [*DPO_SETTINGS, "dpo_single_adapter"]:
        if (out / name / "adapter").exists():
            models[name] = (None, out / name / "adapter") if name == "dpo_single_adapter" else (out / best / "adapter", out / name / "adapter")
    cache = out / "test_answers_all.json"
    answers = json.load(cache.open()) if cache.exists() else {}
    for k, (s, d) in models.items():
        if k not in answers:
            m = load_fp16(s, d)
            answers[k] = generate(m, tok, test.question.tolist())
            del m
            free()
            json.dump(answers, cache.open("w"))
    rows = []
    rng = random.Random(SEED)
    names = list(answers)
    for i in range(min(n_judge, len(test))):
        order = names[:]
        rng.shuffle(order)  # blind + shuffled so the judge cannot tell which model wrote what
        letters = "ABCDEFGH"[:len(order)]
        item = {"question": test.question[i], "reference": test.answer[i][:1200], **{L: answers[n][i] for L, n in zip(letters, order)}}
        sc = get_router().complete(JUDGE, json.dumps(item), json_mode=True, temperature=0.0).json()
        rows += [dict(model=n, **sc[L]) for L, n in zip(letters, order)]
    judge_df = pd.DataFrame(rows).groupby("model").mean().round(2)
    read = {k: {"flesch": round(float(np.mean([textstat.flesch_reading_ease(a) for a in v])), 1),
                "avg_words": round(float(np.mean([len(a.split()) for a in v])), 1)} for k, v in answers.items()}
    report = {"readability": read, "judge": judge_df.to_dict(orient="index"), "n_judge": n_judge}
    (out / "judge_results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["prepare", "sft", "dpo", "judge"])
    ap.add_argument("--out", default="work")
    ap.add_argument("--precision", default="4bit", choices=["4bit", "8bit", "16bit"])
    ap.add_argument("--configs", default="", help="e.g. 32,64 to run one LoRA config")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    random.seed(SEED)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if a.step == "prepare":
        prepare(out)
    elif a.step == "sft":
        cfgs = [tuple(int(x) for x in a.configs.split(","))] if a.configs else LORA_CONFIGS
        sft(out, a.precision, cfgs)
    elif a.step == "dpo":
        dpo(out)
    else:
        judge(out)
