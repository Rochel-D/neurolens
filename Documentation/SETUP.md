# Running NeuroLens locally

Needs **Python 3.11 or newer**. All commands run inside the `Source-Code` folder.

## 1. API keys

```bash
cd Source-Code
cp .env.example .env
```

Open `.env` and fill in:

| Setting | What to put |
|---|---|
| `GEMINI_API_KEY_1` … `_3` | Up to three Google AI Studio keys |
| `GROK_API_KEY_1` … `_3` | Up to three Groq (`gsk_…`) or xAI (`xai-…`) keys |
| `SERPER_API_KEY` | Serper.dev key for web search (optional) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | The admin account created on first start |

The app also works with no keys at all: it then uses rule-based planning and template answers.

## 2. MRI model (optional)

Put the trained weights at `models/mri_classifier.pt`. They are not in Git because of their size. To train them:

```bash
python training/mri/train_mri_classifier.py --data <path to Brain Tumor MRI Dataset> --epochs 5 --out mri_out
cp mri_out/mri_classifier.pt models/
```

## 3. Start

**macOS / Linux**
```bash
./run.sh
```

**Windows**
```bat
run.bat
```

The script creates `.venv`, installs the packages, installs PyTorch when an MRI model is present, and starts the
server. Open **http://localhost:8000**.

- Port already in use: `lsof -ti:8000 | xargs kill` (macOS), or close the other terminal.
- Next time, just run `./run.sh` again.

## 4. Fine-tuned explainer (optional)

To let our own Qwen2.5-0.5B model write the patient view:

```bash
source .venv/bin/activate
pip install -r requirements-ml.txt
```

Copy the adapter folders into `models/` and set in `.env`:

```
EXPLAINER_MODE=local
SFT_ADAPTER=models/sft_r32_a64
DPO_ADAPTER=models/dpo_lr1e-5_b0.3
```

Restart the app. Answers written by the model show the engine tag **"fine-tuned Qwen2.5-0.5B (SFT+DPO)"**.
On a CPU each answer takes about 20–60 seconds.

## 5. Tests and evaluation

```bash
source .venv/bin/activate
python -m pytest -q          # 56 tests
python evals/run_evals.py    # evaluation suite → evals/results.md
```

## Sample files

`sample_data/reports/` holds fictional reports to try: `cbc_report.txt`, `metabolic_report.pdf`,
`metabolic_report.txt` and `thyroid_electrolytes_report.txt`.
