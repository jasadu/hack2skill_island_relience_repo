#!/usr/bin/env bash
# One-command deploy of the whole app: Cloud Run backend, then Firebase Hosting frontend, then the hosted check.
# Usage:  PROJECT_ID=your-project ./deploy.sh            (REGION defaults to asia-southeast1)
#         MIN_INSTANCES=1 PROJECT_ID=... ./deploy.sh     (use 1 during the evaluation window)
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID, e.g. PROJECT_ID=crested-polygon-511408-h9 ./deploy.sh}"
REGION="${REGION:-asia-southeast1}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
DAILY_AI_BUDGET="${DAILY_AI_BUDGET:-600}"
SA="island-api@${PROJECT_ID}.iam.gserviceaccount.com"
cd "$(dirname "$0")"

echo "== 1/4 Backend tests (fake AI, no network)"
(cd backend && pip install -q --break-system-packages -r requirements-dev.txt && FAKE_AI=1 STORE=memory CLIMATE_LIVE=0 python -m pytest -q)

echo "== 2/4 Deploy backend to Cloud Run (${REGION}, min instances ${MIN_INSTANCES})"
(cd backend && gcloud run deploy island-api --source . --project "$PROJECT_ID" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated --min-instances "$MIN_INSTANCES" --max-instances 3 \
  --set-env-vars "ASSESS_MODEL=gemini-3.8-flash,JUDGE_MODEL=gemini-3.6-flash,DAILY_AI_BUDGET=${DAILY_AI_BUDGET},PER_RUN_AI_CAP=25,PROMPT_VERSION=v1" \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest --quiet)

echo "== 3/4 Build and deploy frontend to Firebase Hosting"
(cd frontend && npm ci --silent && npm run build)
echo "{\"projects\":{\"default\":\"${PROJECT_ID}\"}}" > .firebaserc
npx --yes firebase-tools deploy --only hosting,firestore:rules --project "$PROJECT_ID"

echo "== 4/4 Check the hosted deployment with real Gemini"
URL="$(gcloud run services describe island-api --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
python backend/scripts/verify_hosted.py "$URL"
echo
echo "Public site: https://${PROJECT_ID}.web.app/"
