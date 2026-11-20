# NeuroLens changelog

## Version 2.1 deployment files (November 2026)
- `Dockerfile`, `.dockerignore`, `.gcloudignore`: image with CPU PyTorch (MRI model) and Tesseract; runs as a non-root user on port 8080.
- `deploy/cloudrun.sh`: one-command deploy to Cloud Run (APIs, Secret Manager, build, deploy, image cleanup).
  `deploy/teardown.sh` removes it again. Step-by-step guide in `deploy/DEPLOY.md`.
- Settings are also read from `/secrets/neurolens.env` (the mounted secret). Local `.env` still works as before.

## Version 2.1 (November 2026) — fixes from testing version 2

Every item below was seen in our own testing (screenshots in the project report, chapter "Broken windows").

### Fixed
- **Gemini keys never answered** (Admin → API keys showed `gemini-flash-latest · ReadTimeout` on gemini#1 and #3, and
  `429 You exceeded your current quota` on gemini#2; Groq answered every time after ~40 s).
  - Cause 1: the newest Gemini models think before answering; with an 8,192-token budget they did not finish in 30 s.
    Now `thinkingLevel=low` (Gemini 3 / *latest*) or `thinkingBudget=0` (2.5 Flash) is sent; if a model rejects the
    setting, the call is repeated once without it. Timeout raised to 45 s.
  - Cause 2: free-tier quota is per model, but a 429 cooled down the whole key. Now only that model rests and the next
    model on the same key is tried; the key rests only when all its models are rate-limited.
  - Cause 3: a timed-out model was retried on every Gemini key (3 × 30 s). It is now skipped for the rest of the
    request, and after two timeouts the key is skipped. A success clears the key's cooldown.
  - Admin → API keys shows which models are resting.
- **System Review said "5/5 keys ready" while every Gemini key was failing.** "Ready" only meant "not cooling down".
  The check now lists keys that have answered and warns about keys that fail and have never recovered.
- **Platelets shown as 0.002 (critical low) from a CBC PDF.** Count units (`/cumm`, `/µL`, `lakh/cumm`, `10^3/µL`)
  were scaled once per line using the first number, so a value and its range could be scaled differently. Each number
  is now scaled on its own, spaces around `/` are ignored, and any value more than 50× outside its range is marked
  **suspect** and the report goes to the review step instead of raising a critical alarm. (Re-upload old reports.)
- **Doctor view numbering restarted at 1** after every paragraph inside a numbered list (`1. 1. 1.`).
- **Konkani translation left headings in English.** The translation prompt now requires headings and bold labels too.
- **Irrelevant source shown** ("Meningioma" under a ferritin answer). Retrieval keeps only documents close to the best
  match, and the chat lists only the sources the answer actually cites.
- **PDF showed raw asterisks** (`*Ferritin*`) — single-asterisk italics are now converted.
- **The same report uploaded twice** created a copy, so every trend read "same". Duplicates (same date and values)
  now open the existing report.
- **Trend card titles wrapped** under the badge for long test names — now truncated with an ellipsis.

### Not changed (documented limits)
- Tesseract is optional; without it, photo OCR uses Gemini vision only.
- With `EXPLAINER_MODE=router` the patient view uses the key pool with the fine-tuning prompt, not the local adapters.

## Version 2 (November 2026)

### Fixed
- **Gemini did not answer (404 model not found).** Model names are no longer hard-coded. Each API key asks its
  provider which models it can use (ListModels), ranks them (current Flash first, no TTS/image/Gemma models) and
  remembers the one that worked. Retired names heal themselves. `.env` now defaults to `GEMINI_MODEL=auto`.
- **Empty answers from "thinking" models** (Gemini 2.5+/3.x, gpt-oss). Output limits were 300–900 tokens and hidden
  reasoning could use all of them. The cap is now 8,192, thought parts are skipped, and an empty answer moves to the
  next key instead of failing.
- **A new chat showed the document from the previous chat.** Attachments now belong to a thread. A new chat starts
  empty; opening an old chat restores only its own report/scan; follow-up questions in the same chat keep its report
  without re-attaching.
- **"Generate a PDF" did nothing.** PDF export is now a real feature (see below).
- Admin key buttons did nothing for slots like `gemini#1` (the `#` was read as a page anchor) — now URL-encoded.
- Signing out left the form on "Create account", so signing in again was sent as a registration.
- Photo/OCR digit confusions such as `1O.4` (letter O) were read as `1` → false critical value. Digits inside numbers
  are now normalised (O→0, l/I→1, S→5) and photo reports always go through a review step.
- Report comparison picked the previous report by date even when it shared no tests; it now picks the latest earlier
  report with tests in common.
- Groq model ids lost their `openai/` prefix during ranking.
- Offline/verifier-fallback answers were identical for patient and doctor; the doctor view now has its own template
  (table of abnormal values, patterns to consider, follow-up note).

### New for users
- **New interface**: Home dashboard, light/dark mode, cleaner chat, report history, mobile layout.
- **PDF export**: values table with ranges and flags, simple + doctor explanations (verified), MRI image + Grad-CAM +
  probabilities, sources, disclaimer on every page. From the Reports/MRI pages, the chat PDF button, or by asking
  "make a PDF" in the chat.
- **Bilingual**: explanations and PDFs in **Hindi** and **Konkani (Romi)**. Translation runs after verification and is
  rejected if any number changes.
- **Photo reports (OCR)**: Gemini vision first, Tesseract as backup, then a **review step** where you correct values,
  remove wrong rows or add missed tests before NeuroLens explains anything.
- **Report history + trends**: charts per test across reports (by the date printed on the report), "better/worse"
  direction, and side-by-side comparison with the previous report.
- **Voice**: speak your question (browser speech recognition) and **read aloud** any answer.
- Attach an existing report/scan to a chat from the 📎 menu; delete chats, reports and scans; 👍/👎 feedback.

### New for admins
- **API keys**: per-key status, answering model, detected models, last error and a log of recent attempts;
  **Test key**, **Reset**, **Enable/disable**, and change model names from the page (saved across restarts).
- **Traces**: search + filters (intent, status, verified, rating), expandable agent steps, **CSV export**.
- **Feedback**: all 👍/👎 with comments and the engine that answered.
- **Knowledge base editor**: add/edit/delete lab tests (aliases, units, ranges, critical values, conversions,
  plain-language text) and RAG notes; **reset to default**; **test the parser** on pasted report lines.
- **Overview**: runs per day, errors, answer time, feedback ratio, intents, engines.
- System Review adds PDF-export, OCR and knowledge-base checks.

### Engineering
- 47 tests (was 33). Database migrates automatically from version 1 (new columns are added on start).
- Frontend split into ES modules (`frontend/js/`), still no build step.
- MRI training script runs on a Mac GPU (MPS) as well as Colab.

## Version 1 (early November 2026)
First complete build: LangGraph agent with 5 subagents, lab parser (43 tests), EfficientNet-B0 MRI classifier with
Grad-CAM, RAG, official MCP fetch server, 6-key router, memory, guardrails, verifier, patient/doctor views, 33 tests.
