# NeuroLens v2.1 — AI medical report assistant

Advanced AI Core Lab project · Kowsya Mutkundu · Muskan Khatoon · Rochel Da Silva · Goa University, MSc AI

NeuroLens reads a **brain MRI slice** (neuro module) or **any blood / lab report** — PDF, text or a **phone photo**
(general module) — checks every value against trusted sources, and explains it two ways: **simply for the patient**
and **technically for the doctor**, in **English, Hindi or Konkani (Romi)**. It exports a **PDF summary**, charts
**trends across reports**, and has a full **admin console**. See `CHANGELOG.md` for what changed from version 1.

It combines the three course areas:

| Area | Where it lives |
|---|---|
| **AI engineering** | FastAPI deep-module services, 56 risk-based tests, evaluation suite, security hardening, System Review |
| **LLM engineering** | Patient-friendly Qwen2.5-0.5B fine-tuned with SFT (QLoRA) + DPO; training pipeline in `training/llm/` |
| **Agentic AI** | LangGraph agent: guardrails → memory → planner → subagents/tools → explainer → verifier → output guard |
| **MCP** | Official `mcp-server-fetch` server over stdio for PubMed + MedlinePlus (no custom server) |
| **RAG** | Knowledge base of 67 documents (lab tests + neuro + general conditions), hybrid TF-IDF retrieval |
| **Memory** | Short-term: last 10 messages per chat. Long-term: consent-based profile + episodic summaries, deletable |
| **Guardrails** | Emergency / self-harm / prompt-injection input checks; dosing / diagnosis / stop-medicine output checks |
| **6-key failover** | 3 Gemini + 3 Grok/Groq keys; models auto-detected per key; Gemini thinks briefly, a rate-limited model gives way to another model on the same key, a slow model is skipped |
| **v2 features** | PDF export, Hindi/Konkani, photo OCR + review, trends, voice, feedback, admin key controls, KB editor |

---

## Run it

Needs **Python 3.11 or newer** (tested on 3.13, works on 3.14).

**Coming from version 1?** Copy your `.env`, `models/` folder (MRI weights, adapters) and, if you want your old data,
`neurolens.db` into the new folder. The database is upgraded automatically on first start. In `.env`, set
`GEMINI_MODEL=auto` and `GEMINI_FALLBACK_MODEL=auto` (or delete those lines) so each key picks a working model.

### macOS / Linux
```bash
unzip NeuroLens.zip && cd NeuroLens
./run.sh                      # creates .venv, installs, copies .env.example → .env, starts the server
```

### Windows (PowerShell or cmd)
```bat
cd NeuroLens
run.bat
```

### Or step by step
```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
# open .env and paste your keys (GEMINI_API_KEY_1..3, GROK_API_KEY_1..3, SERPER_API_KEY)
python -m uvicorn backend.app.main:app --reload --port 8000
```

Open **http://localhost:8000**, create an account, then try **Blood report → Try a sample**.
Admin login (System page): `admin@neurolens.local` / `Admin12345` (change it in `.env`).

**Photo reports with Tesseract (optional):** `brew install tesseract` (Mac). Gemini vision is used first and needs
no install.

**No keys yet?** The app still runs: answers are built from the knowledge base and the verified parsed values
("template (offline)" engine). Add keys for natural explanations.

### Tests and evaluation
```bash
python -m pytest                     # 47 tests, offline, ~10 s
python evals/run_evals.py            # writes evals/results.md + results.json
```

### Optional: trained models
* **MRI classifier** — train on Colab with `training/mri/train_mri_classifier.py`, put `mri_classifier.pt` in `models/`,
  then `pip install -r requirements-ml.txt`. Without it the MRI page says the classifier is not installed.
* **Fine-tuned explainer** — copy your SFT and DPO adapters into `models/` and set `EXPLAINER_MODE=local`,
  `SFT_ADAPTER=models/sft_r32_a64/adapter`, `DPO_ADAPTER=models/dpo_lr1e-5_b0.3/adapter`. See `training/README.md`.

---

## How a request flows

```
Upload report / MRI ─► parse in memory (never stored) ─► values + status saved
Chat message ─► guard_input ─► load_memory ─► plan ─► act ─► explain ─► verify ─► guard_output ─► answer
                   │ blocked                (LLM or     (sub-   (fine-tuned  │ fail → re-explain once
                   └─► safety message        rules)    agents)  Qwen/router) └─ still failing → safe template
```

* **guard_input** stops emergencies (→ call 112), self-harm (→ Tele-MANAS 14416) and prompt injection before any tool runs.
* **plan** asks Gemini/Grok for a JSON plan; if no key works it falls back to rules.
* **act** runs specialist subagents: `lab_agent`, `neuro_agent`, `term_agent`, `research_agent`, `memory_agent`.
* **explain** uses only the facts the tools produced.
* **verify** checks every number against the parsed report and the MRI label/confidence; wrong drafts are rewritten.
* **guard_output** softens diagnosis/dose wording and adds the disclaimer; critical values get a banner.

Every run is stored as a trace (tools, models, key slot, verifier result, latency) and shown in the app under
"How I answered" and on the System page.

## Project layout
```
backend/app/
  main.py              FastAPI app, security headers, error handlers, static site
  config.py            all settings from .env
  agent/graph.py       LangGraph state machine
  agent/subagents.py   lab / neuro / term / research / memory specialists
  services/            deep modules: model_router, lab_parser, mri, rag, kb, explainer, guardrails,
                       verifier, memory, translate, ocr, pdf_export, export_service, web_tools,
                       mcp_client, system_review
  routers/             thin HTTP layer: auth, uploads, chat, memory, admin
backend/data/          lab_tests.json (43 tests), knowledge.json (seed; admin edits stored in the DB),
                       fonts/ (Noto Sans + Noto Sans Devanagari, SIL Open Font License)
frontend/              index.html, app.css, js/ (ES modules: app, core, home, chat, reports, mri,
                       memory, admin) — no build step
training/mri/          EfficientNet-B0 + Grad-CAM classifier training (Colab)
training/llm/          SFT (QLoRA) + DPO pipeline, LoRA/precision/adapter experiments, LLM judge
evals/                 evaluation suite and results
tests/                 pytest suite
../Documentation/      architecture, design decisions, evaluation, setup and deploy guides
sample_data/reports/   three fictional sample reports (+ one PDF)
```

NeuroLens gives general health education, not a diagnosis.

## Deploy online (Google Cloud Run)

See **deploy/DEPLOY.md**: `./deploy/cloudrun.sh YOUR_PROJECT_ID` builds the image in Google Cloud, stores your `.env` in Secret Manager and gives you a public HTTPS link (demo mode: data resets on restart). `./deploy/teardown.sh YOUR_PROJECT_ID` removes everything.
