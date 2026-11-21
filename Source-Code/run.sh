#!/usr/bin/env bash
# NeuroLens — one-command start for macOS / Linux
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
# The MRI classifier needs PyTorch, which requirements.txt does not include (it is large)
if [ -f models/mri_classifier.pt ] && ! python -c "import torch, torchvision" 2>/dev/null; then
  echo "Installing PyTorch for the MRI classifier (one time, a few minutes)..."
  pip install -q torch torchvision
fi
[ -f .env ] || cp .env.example .env
echo ""
echo "NeuroLens is starting at http://localhost:8000  (Ctrl+C to stop)"
echo ""
python -m uvicorn backend.app.main:app --reload --port 8000
