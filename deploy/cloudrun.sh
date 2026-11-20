#!/usr/bin/env bash
# Deploy NeuroLens v2.1 to Google Cloud Run.
#
#   ./deploy/cloudrun.sh <PROJECT_ID> [REGION]
#
# Run it again any time to redeploy (new code or new keys). Works with the bash that ships with macOS.
# What it does:
#   1. turns on the Google APIs Cloud Run needs
#   2. copies your .env into ONE Secret Manager secret (keys never go into the image)
#   3. builds the image in Google Cloud Build (no Docker needed on your Mac) and deploys it
#   4. keeps only the 2 newest images so storage stays almost free
set -euo pipefail

PROJECT="${1:-}"
REGION="${2:-asia-south1}"           # Mumbai
SERVICE="neurolens"
SECRET="neurolens-env"

cd "$(dirname "$0")/.."

if [ -z "$PROJECT" ]; then
  echo "Usage: ./deploy/cloudrun.sh <PROJECT_ID> [REGION]"
  echo "Your projects:"; gcloud projects list --format="value(projectId)" 2>/dev/null || true
  exit 1
fi
command -v gcloud >/dev/null || { echo "gcloud not found. Install it first: brew install --cask google-cloud-sdk"; exit 1; }
[ -f .env ] || { echo "No .env file here. Copy .env.example to .env and add your keys first."; exit 1; }

if [ -f models/mri_classifier.pt ]; then
  echo "✓ MRI model found (models/mri_classifier.pt) — it will be included."
else
  echo "! models/mri_classifier.pt not found — the MRI page will say the model is not installed."
  echo "  Copy it from your training output (mri_out/) into models/ to include it. Continuing in 5 s…"; sleep 5
fi

gcloud config set project "$PROJECT" >/dev/null
echo "→ Turning on APIs (first time takes a minute)…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com --quiet

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# ---------------------------------------------------------------- production .env -> Secret Manager
TMP_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV"' EXIT
# your keys and settings, minus the lines this script controls
grep -v -E '^(ENV|DATABASE_URL|EXPLAINER_MODE|SFT_ADAPTER|DPO_ADAPTER|JWT_SECRET|ADMIN_PASSWORD)=' .env > "$TMP_ENV" || true

get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true; }

JWT="$(get JWT_SECRET)"
if [ "${#JWT}" -lt 32 ] || [ "${JWT#dev-only}" != "$JWT" ]; then
  # keep the same secret across redeploys so people stay logged in
  JWT="$(gcloud secrets versions access latest --secret="$SECRET" 2>/dev/null | grep -E '^JWT_SECRET=' | cut -d= -f2- || true)"
  [ "${#JWT}" -ge 32 ] || JWT="$(openssl rand -hex 32)"
fi

ADMIN_PW="$(get ADMIN_PASSWORD)"
NEW_ADMIN=0
if [ -z "$ADMIN_PW" ] || [ "$ADMIN_PW" = "Admin12345" ]; then
  ADMIN_PW="$(gcloud secrets versions access latest --secret="$SECRET" 2>/dev/null | grep -E '^ADMIN_PASSWORD=' | cut -d= -f2- || true)"
  if [ -z "$ADMIN_PW" ]; then ADMIN_PW="NL-$(openssl rand -hex 6)"; NEW_ADMIN=1; fi
fi

{
  echo "ENV=production"
  echo "DATABASE_URL=sqlite:////tmp/neurolens.db"
  echo "EXPLAINER_MODE=router"
  echo "JWT_SECRET=$JWT"
  echo "ADMIN_PASSWORD=$ADMIN_PW"
} >> "$TMP_ENV"

echo "→ Saving your keys in Secret Manager ($SECRET)…"
if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  gcloud secrets versions add "$SECRET" --data-file="$TMP_ENV" --quiet >/dev/null
  # disable older versions (only the newest is needed; keeps it inside the free tier)
  for v in $(gcloud secrets versions list "$SECRET" --filter="state=ENABLED" --sort-by=~createTime \
              --format="value(name)" | tail -n +2); do
    gcloud secrets versions destroy "$v" --secret="$SECRET" --quiet >/dev/null 2>&1 || true
  done
else
  gcloud secrets create "$SECRET" --replication-policy=automatic --data-file="$TMP_ENV" --quiet >/dev/null
fi
gcloud secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/secretmanager.secretAccessor" --quiet >/dev/null
# newer projects build with the compute service account, which needs this role
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/run.builder" --condition=None --quiet >/dev/null 2>&1 || true

# ---------------------------------------------------------------- build + deploy
echo "→ Building and deploying (about 6–10 minutes the first time)…"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi --cpu 1 --cpu-boost \
  --min-instances 0 --max-instances 1 \
  --concurrency 20 --timeout 300 \
  --set-secrets "/secrets/neurolens.env=${SECRET}:latest" \
  --quiet

# keep only the two newest images (each is ~1 GB)
POLICY="$(mktemp)"
cat > "$POLICY" <<'JSON'
[{"name": "keep-newest-2", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
 {"name": "delete-rest", "action": {"type": "Delete"}, "condition": {"tagState": "ANY"}}]
JSON
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --location="$REGION" \
  --policy="$POLICY" --no-dry-run --quiet >/dev/null 2>&1 || true
rm -f "$POLICY"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ""
echo "✅ NeuroLens is live: $URL"
echo "   Health check:   $URL/api/health"
echo "   Admin login:    $(get ADMIN_EMAIL | sed 's/^$/admin@neurolens.local/')"
if [ "$NEW_ADMIN" = 1 ]; then
  echo "   Admin password: $ADMIN_PW   ← new, write it down (also stored in Secret Manager → $SECRET)"
else
  echo "   Admin password: (the one in your .env / already in Secret Manager)"
fi
echo "   Data resets whenever the service restarts (demo mode)."
