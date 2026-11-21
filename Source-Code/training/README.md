# Training (Colab, free T4 GPU)

Two models are trained. Neither is needed to run the app: without them NeuroLens uses the router LLM for
explanations and tells the user the MRI classifier is not installed.

## 1. Brain MRI classifier (neuro module)

```bash
pip install -r training/requirements-train.txt
kaggle datasets download -d masoudnickparvar/brain-tumor-mri-dataset -p /content --unzip
python training/mri/train_mri_classifier.py --data /content --epochs 8 --out /content/drive/MyDrive/neurolens
```

Copy `mri_classifier.pt` to `models/mri_classifier.pt`. Put the numbers from `mri_metrics.json`
(accuracy, per-class F1, confusion matrix) in the evaluation report.

## 2. Patient-friendly explainer (LLM fine-tuning)

This continues the LLM Engineering project: Qwen2.5-0.5B-Instruct, SFT with QLoRA, then DPO.
New for NeuroLens: the training set also includes one Q&A per lab test and per neuro/general
knowledge note, so the model learns to explain blood-report values and MRI terms.

```bash
export GEMINI_API_KEY_1=...   # the same 6-key pool as the app is used for rewriting and judging
python training/llm/finetune_patient_llm.py prepare --out work
python training/llm/finetune_patient_llm.py sft     --out work                       # 5 LoRA rank/alpha configs
python training/llm/finetune_patient_llm.py sft     --out work --precision 8bit  --configs 32,64
python training/llm/finetune_patient_llm.py sft     --out work --precision 16bit --configs 32,64
python training/llm/finetune_patient_llm.py dpo     --out work                       # 3 DPO settings + single-adapter variant
python training/llm/finetune_patient_llm.py judge   --out work                       # Flesch, length, Gemini judge
```

You can reuse the adapters you already trained (`sft_r32_a64`, `dpo_lr1e-5_b0.3` in your Drive
`medical_llm` folder). Copy them to `models/` and set in `.env`:

```
EXPLAINER_MODE=local
SFT_ADAPTER=models/sft_r32_a64/adapter
DPO_ADAPTER=models/dpo_lr1e-5_b0.3/adapter
```

and install `pip install -r requirements-ml.txt`. On CPU the 0.5B model takes roughly 10–40 s per
answer. If a draft from the small model fails the verifier, the agent automatically rewrites it with
the router model, so a factual slip from the 0.5B model never reaches the user.
