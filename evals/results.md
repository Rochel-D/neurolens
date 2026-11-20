# NeuroLens evaluation results

| Criterion | Result |
|---|---|
| Lab parser | precision 1.0, recall 1.0, value acc 1.0, status acc 1.0 |
| Verifier | tampered caught 10/10, false alarms 0.0, MRI checks 4/4 |
| Guardrails | input accuracy 1.0 on 18 prompts, output rules 4/4 |
| RAG | hit@1 1.0, hit@3 1.0 (14 queries) |
| Key pool | success with 1 key 0.65 vs 6 keys 0.998 (each key fails 35%) |
| Latency | parse+retrieve p50 25.83 ms, p95 31.24 ms; offline agent p50 36 ms |
| Agent (offline) | verified 1.0 of 9 runs, patient answer Flesch 67.8 |
