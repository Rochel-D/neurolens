# NeuroLens v2.1 — container image for Google Cloud Run (also runs anywhere Docker runs).
# API keys are NOT baked in: Cloud Run mounts them from Secret Manager at /secrets/neurolens.env.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract = local backup for photo OCR (Gemini vision is tried first)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# CPU-only PyTorch for the MRI classifier (much smaller than the CUDA build)
RUN pip install -r requirements.txt \
    && pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY backend ./backend
COPY frontend ./frontend
COPY models ./models
COPY sample_data ./sample_data

RUN useradd --create-home --uid 10001 neurolens && chown -R neurolens /app
USER neurolens

# Demo deployment: SQLite in /tmp, reset whenever the container restarts.
ENV ENV=production \
    DATABASE_URL=sqlite:////tmp/neurolens.db \
    EXPLAINER_MODE=router \
    PORT=8080

EXPOSE 8080
# --proxy-headers: Cloud Run sits behind Google's front end; use the real client IP for rate limits.
CMD exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
