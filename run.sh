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
[ -f .env ] || cp .env.example .env
echo ""
echo "NeuroLens is starting at http://localhost:8000  (Ctrl+C to stop)"
echo ""
python -m uvicorn backend.app.main:app --reload --port 8000
