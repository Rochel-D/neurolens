# What each earlier project contributes to NeuroLens

## 1. LLM Engineering — Patient-Friendly Medical Explanation (SFT + QLoRA + DPO)

| In the earlier project | In NeuroLens |
|---|---|
| Qwen2.5-0.5B-Instruct, SFT with QLoRA (NF4, double quant) | `training/llm/finetune_patient_llm.py sft` — same settings (2 epochs, lr 2e-4, batch 4, grad-accum 4, max 768 tokens) |
| 1,000 MedQuAD records, Gemini rewrite, 600/200/50/150 split | `prepare` step — same split, now **plus** one Q&A per lab test and per neuro/general note, rewritten with the 6-key pool |
| 5 LoRA rank/alpha configs, best r32/α64 | Same grid in `LORA_CONFIGS`, best picked by eval loss into `best_sft.txt` |
| 4/8/16-bit precision comparison | `--precision 4bit|8bit|16bit` |
| DPO: chosen = simple answer, rejected = original Qwen; main / middle / gentle | `dpo` step — same three settings; gentle (lr 1e-5, β 0.3) is the default adapter name |
| One adapter vs two adapters (two chosen) | Both variants trained; the app loads SFT then DPO adapter (`explainer._LocalModel`) |
| Gemini LLM judge (accuracy / clarity / safety), Flesch, length | `judge` step + Flesch in `evals/run_evals.py` |
| Same system prompt for training | `explainer.PATIENT_SYSTEM` is identical, so the fine-tuned model sees the prompt it learned |
| Gradio demo: SFT vs SFT+DPO | Replaced by the full web app: "Me (simple)" vs "My doctor" views |
| **Limitation:** small model still made factual errors; future work: connect to MedlinePlus | **Fixed by design:** the model only explains facts from RAG + parsed values, and the verifier rewrites any draft with a wrong number |

## 2. AI Engineering — CampusConnect

| In CampusConnect | In NeuroLens |
|---|---|
| Thin routers, deep services (`score_ride()` hides the algorithm) | `parse_report()`, `analyse()`, `retrieve()`, `complete()`, `run_agent()` — one small public call each |
| Provider chain Groq → Gemini → extractive fallback | 6-key pool Gemini×3 → Grok/Groq×3 → deterministic template |
| Hard gates fixed a 0.57 → 1.00 precision bug found by evals | Verifier gates: numbers and status words must match the parsed report |
| 70 risk-based tests | 32 risk-based tests: failover, parsing, unit conversion, critical values, guardrails, verifier, ownership 404, consent, XSS, SQL injection |
| `run_evals.py`: domain, instruction following, cost, latency | `evals/run_evals.py`: parser P/R, verifier detection, guardrail accuracy, RAG hit@k, failover, latency, readability |
| Security: bcrypt, JWT with pinned algorithm, CORS exact origins, sanitising, upload magic bytes, generic 500s | Same, plus CSP, files processed in memory only, PII redaction in stored chat |
| Anonymity schema (no user column) | Privacy schema: no raw files stored; memory only with consent; "Delete my data" |
| System Review — 11 deterministic checks | System Review — 14 checks (hashing, consent, ownership, ranges, KB, key pool, MRI, explainer, error rate, verifier fallbacks, guardrails, JWT) |
| Pinned versions for reproducibility | `requirements.txt` pinned |

## 3. Agentic AI — KonkanBite

| In KonkanBite | In NeuroLens |
|---|---|
| Bottom-up: data + deterministic functions first, model autonomy later | Parser, ranges, retrieval and verifier are plain code; the LLM plans and explains |
| Orchestrator + 6 subagents, roles split by data and rules | LangGraph agent + 5 subagents: lab (general), neuro (MRI only), term, research, memory |
| Verifier PASS/FAIL before showing a plan | `verify` node; failure loops back to `explain` with feedback, then falls back to a safe template |
| Gated side-effects (booking needs confirmation) | Memory writes are gated by consent; nothing is saved long-term without it |
| Model router Gemini → Grok, retry, cooldown | Same idea, 6 keys, per-key cooldown, model-name fallback, JSON validity check |
| Short-term (last 10 messages) + consent-based long-term memory + delete | Same: `memory.short_term()` (10 messages) and `memory.long_term()` (profile + episodic) with consent and deletion |
| Context engineering: role prompts, narrow tools, untrusted web content | Facts-only prompts, untrusted content wrapped as data, planner sees only the message + attachment flags |
| MCP client (stdio) with graceful fallback | Official `mcp-server-fetch` over stdio; falls back to direct HTTPS if it can't start |
| Honest failure states (`logged_only`) | Honest states: "classifier not installed", "template (offline)", "safe summary shown" |
| LangSmith tracing | Built-in trace table (steps, tools, model slot, verifier, latency) on the System page |
| Tourist site + owner portal | Patient views + admin System page |
