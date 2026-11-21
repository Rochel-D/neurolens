#!/usr/bin/env bash
# Remove everything the deploy created (service, images, secret). Credits stop being used.
#   ./deploy/teardown.sh <PROJECT_ID> [REGION]
set -euo pipefail
PROJECT="${1:?Usage: ./deploy/teardown.sh <PROJECT_ID> [REGION]}"; REGION="${2:-asia-south1}"
gcloud config set project "$PROJECT" >/dev/null
gcloud run services delete neurolens --region "$REGION" --quiet || true
gcloud artifacts repositories delete cloud-run-source-deploy --location "$REGION" --quiet || true
gcloud secrets delete neurolens-env --quiet || true
echo "Done. NeuroLens is removed from $PROJECT."
