# Evaluation

Run from `Source-Code/`:

```bash
python -m pytest -q          # 56 automated tests
python evals/run_evals.py    # evaluation suite → evals/results.md and evals/results.json
```

## System evaluation (`evals/run_evals.py`)

| Evaluation | What it does | Result |
|---|---|---|
| Lab parser | 43 hand-labelled values in 4 sample reports | precision 1.0, recall 1.0, value accuracy 1.0, status accuracy 1.0 |
| Verifier | Deliberately wrong answers (changed number, wrong status, fake citation) plus correct ones | 10/10 tampered answers caught, 0 false alarms; MRI checks 4/4 |
| Guardrails | 18 prompts: emergencies, general questions, self-harm, injection, normal | 18/18 correct; output rules 4/4 |
| Retrieval | 14 questions with the expected document | hit@1 = 1.0, hit@3 = 1.0 |
| Key failover | 400 simulated requests, each key failing 35% of the time | 65% answered with 1 key, 99.8% with 6 keys |
| Latency | parse + retrieve | p50 25.8 ms, p95 31.2 ms |
| Agent (offline) | 9 full runs without API keys | 9/9 verified; patient answer reading ease 67.8 |

## Automated tests (pytest)

| File | Tests | Risks covered |
|---|---|---|
| `test_lab_parser.py` | 10 | aliases, unit conversion, Indian digit grouping, lakh with spaces, suspect values, critical values |
| `test_guardrails_verifier.py` | 7 | emergency vs. general question, self-harm, injection, dose removal, tampered numbers |
| `test_model_router.py` | 17 | key order, retired models, empty answers, bad keys, quota per model, timeouts, thinking settings |
| `test_api_agent.py` | 10 | ownership (404), full agent run, memory consent, deletion |
| `test_v2_features.py` | 12 | new chat never sees the old attachment, PDF export, translation number check, OCR digits, export expiry, duplicate upload |

## MRI classifier

EfficientNet-B0, ImageNet weights, 5 epochs on Apple MPS; Brain Tumor MRI Dataset (Kaggle), separate test folder.

| Class | Precision | Recall | F1 |
|---|---|---|---|
| Glioma | 0.997 | 0.815 | 0.897 |
| Meningioma | 0.890 | 0.995 | 0.940 |

Test accuracy **0.9525**, macro F1 **0.9513**, best validation accuracy 0.9911. Full per-class results are written to
`mri_out/mri_metrics.json` by the training script.

## Fine-tuned explainer (LLM Engineering)

Qwen2.5-0.5B-Instruct; 1,000 MedQuAD pairs rewritten in simple English (600 SFT / 200 DPO / 50 validation / 150 test).

| Model | Reading ease | Words | Accuracy | Clarity | Safety |
|---|---|---|---|---|---|
| Original Qwen | 52.6 | 179.2 | 1.73 | 3.13 | 4.23 |
| Qwen + SFT | 57.9 | 65.0 | 2.40 | 3.90 | 4.93 |
| SFT + DPO main | 64.6 | 74.9 | 2.13 | 3.63 | 5.00 |
| SFT + DPO middle | 61.3 | 70.9 | 2.27 | 3.70 | 5.00 |
| SFT + DPO gentle (used) | 59.3 | 65.6 | 2.33 | 3.87 | 5.00 |

Reading ease and words: 150 test questions. Accuracy, clarity and safety: Gemini judge (1–5), 30 held-out questions.
Best LoRA setting: rank 32, alpha 64 (validation loss 1.657).

**Conclusion:** fine-tuning improved style and safety but not factual accuracy, so in NeuroLens the model only
rewrites facts produced by tools, and a code verifier checks every answer.
