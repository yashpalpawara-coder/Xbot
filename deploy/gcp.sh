#!/usr/bin/env bash
set -euo pipefail

# Deploy XBot to Cloud Run: Playwright backend + Next.js frontend.
# Usage:
#   ./deploy/gcp.sh
#   PROJECT_ID=my-project REGION=us-central1 ./deploy/gcp.sh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-xbot}"
BACKEND_SERVICE="${BACKEND_SERVICE:-xbot-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-xbot-frontend}"
BUCKET="${BUCKET:-}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Install the Google Cloud SDK first: https://cloud.google.com/sdk/docs/install"
  echo "Then run: gcloud init && gcloud auth application-default login"
  exit 1
fi

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "Set a project: gcloud config set project YOUR_PROJECT_ID"
  echo "Or: PROJECT_ID=YOUR_PROJECT_ID ./deploy/gcp.sh"
  exit 1
fi

gcloud config set project "${PROJECT_ID}" >/dev/null
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"

echo "Enabling APIs..."
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com

if ! gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="XBot images"
fi

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo "Building backend image (linux/amd64 for Cloud Run)..."
docker build --platform linux/amd64 -t "${REGISTRY}/backend:latest" "${ROOT}/backend"
docker push "${REGISTRY}/backend:latest"

BACKEND_ENV="HEADLESS=true,DATABASE_MODE=auto,PYTHONUNBUFFERED=1,PLAYWRIGHT_BROWSERS_PATH=/ms-playwright"
if [[ -n "${SUPABASE_URL:-}" ]]; then
  BACKEND_ENV="${BACKEND_ENV},SUPABASE_URL=${SUPABASE_URL}"
fi
if [[ -n "${SUPABASE_PUBLISHABLE_KEY:-}" ]]; then
  BACKEND_ENV="${BACKEND_ENV},SUPABASE_PUBLISHABLE_KEY=${SUPABASE_PUBLISHABLE_KEY}"
fi
if [[ -n "${BUCKET}" ]]; then
  if ! gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" --uniform-bucket-level-access
  fi
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
  RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  echo "Granting ${RUNTIME_SA} access to gs://${BUCKET}..."
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/storage.objectAdmin \
    --quiet
  sleep 8
  BACKEND_ENV="${BACKEND_ENV},BROWSER_PROFILE_DIR=/app/data/chrome-profile,LOCAL_STORE_PATH=/app/data/store.json"
fi

BACKEND_ARGS=(
  run deploy "${BACKEND_SERVICE}"
  --image="${REGISTRY}/backend:latest"
  --region="${REGION}"
  --platform=managed
  --no-allow-unauthenticated
  --execution-environment=gen2
  --port=10000
  --cpu=2
  --memory=4Gi
  --timeout=3600
  --concurrency=8
  --min-instances=1
  --max-instances=1
  --no-cpu-throttling
  --cpu-boost
  --set-env-vars="${BACKEND_ENV}"
)
if [[ -n "${BUCKET}" ]]; then
  BACKEND_ARGS+=(
    --add-volume="name=data,type=cloud-storage,bucket=${BUCKET}"
    --add-volume-mount="volume=data,mount-path=/app/data"
  )
else
  BACKEND_ARGS+=(--clear-volumes --clear-volume-mounts)
fi

echo "Deploying backend..."
gcloud "${BACKEND_ARGS[@]}"

BACKEND_URL="$(gcloud run services describe "${BACKEND_SERVICE}" --region="${REGION}" --format='value(status.url)')"
echo "Backend: ${BACKEND_URL}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "Allowing ${RUNTIME_SA} to call the backend..."
gcloud run services add-iam-policy-binding "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role=roles/run.invoker \
  --quiet

echo "Building frontend image (linux/amd64 for Cloud Run)..."
docker build --platform linux/amd64 \
  --build-arg "API_URL=${BACKEND_URL}" \
  --build-arg "NEXT_PUBLIC_SUPABASE_URL=${SUPABASE_URL:-}" \
  --build-arg "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=${SUPABASE_PUBLISHABLE_KEY:-}" \
  -t "${REGISTRY}/frontend:latest" "${ROOT}/frontend"
docker push "${REGISTRY}/frontend:latest"

echo "Deploying frontend..."
gcloud run deploy "${FRONTEND_SERVICE}" \
  --image="${REGISTRY}/frontend:latest" \
  --region="${REGION}" \
  --platform=managed \
  --no-allow-unauthenticated \
  --port=3000 \
  --cpu=1 \
  --memory=512Mi \
  --set-env-vars="API_URL=${BACKEND_URL},NEXT_PUBLIC_SUPABASE_URL=${SUPABASE_URL:-},NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=${SUPABASE_PUBLISHABLE_KEY:-}"

allow_unauthenticated() {
  local service="$1"
  if gcloud run services add-iam-policy-binding "${service}" \
    --region="${REGION}" \
    --member=allUsers \
    --role=roles/run.invoker \
    --quiet; then
    echo "Public access enabled for ${service}."
  else
    echo "Could not make ${service} public (org policy). Open it with an account that has Cloud Run Invoker."
  fi
}

allow_unauthenticated "${BACKEND_SERVICE}"
allow_unauthenticated "${FRONTEND_SERVICE}"

FRONTEND_URL="$(gcloud run services describe "${FRONTEND_SERVICE}" --region="${REGION}" --format='value(status.url)')"
gcloud run services update "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --update-env-vars="CORS_ORIGINS=${FRONTEND_URL}"

echo
echo "Dashboard: ${FRONTEND_URL}"
echo "API:       ${BACKEND_URL}"
echo
echo "Optional: persist the Chrome profile with BUCKET=your-bucket-name ./deploy/gcp.sh"
echo "Optional: set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY so Cloud Run does not use ephemeral JSON."
