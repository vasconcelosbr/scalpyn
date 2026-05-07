#!/bin/bash
# run-bootstrap-job.sh
#
# Script to create and execute the Cloud Run Job for simulation bootstrap.
# This script is production-safe and designed for Google Cloud Run Jobs.
#
# Usage:
#   ./run-bootstrap-job.sh [--limit NUMBER] [--region REGION] [--project PROJECT_ID]
#
# Requirements:
#   - gcloud CLI installed and authenticated
#   - Appropriate permissions for Cloud Run Jobs
#   - Docker image already built and pushed to Artifact Registry

set -e

# ── Configuration ────────────────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:-clickrate-477217}"
REGION="${GCP_REGION:-us-central1}"
REPO="scalpyn"
SERVICE="scalpyn"
JOB_NAME="bootstrap-simulations"
LIMIT=5000
MEMORY="1Gi"
CPU="1"
TIMEOUT="3600s"
MAX_RETRIES=1

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --project)
            PROJECT_ID="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--limit NUMBER] [--region REGION] [--project PROJECT_ID]"
            echo ""
            echo "Options:"
            echo "  --limit NUMBER       Number of decisions to process (default: 5000)"
            echo "  --region REGION      GCP region (default: us-central1)"
            echo "  --project PROJECT_ID GCP project ID (default: clickrate-477217)"
            echo "  --help               Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:latest"

echo "════════════════════════════════════════════════════════════════════════════"
echo "  SCALPYN - BOOTSTRAP SIMULATIONS JOB"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""
echo "Configuration:"
echo "  Project ID:  ${PROJECT_ID}"
echo "  Region:      ${REGION}"
echo "  Job Name:    ${JOB_NAME}"
echo "  Image:       ${IMAGE}"
echo "  Limit:       ${LIMIT} decisions"
echo "  Memory:      ${MEMORY}"
echo "  CPU:         ${CPU}"
echo "  Timeout:     ${TIMEOUT}"
echo ""

# ── Step 1: Verify Image Exists ─────────────────────────────────────────────
echo "─────────────────────────────────────────────────────────────────────────"
echo "Step 1: Verifying Docker image exists..."
echo "─────────────────────────────────────────────────────────────────────────"

if ! gcloud artifacts docker images describe "${IMAGE}" \
    --project="${PROJECT_ID}" &>/dev/null; then
    echo "ERROR: Image not found: ${IMAGE}"
    echo ""
    echo "Please build and push the image first:"
    echo "  cd backend"
    echo "  docker build -t ${IMAGE} ."
    echo "  docker push ${IMAGE}"
    echo ""
    echo "Or trigger a Cloud Build:"
    echo "  gcloud builds submit --config cloudbuild.yaml"
    exit 1
fi

echo "✓ Image verified: ${IMAGE}"
echo ""

# ── Step 2: Check if Job Exists ────────────────────────────────────────────
echo "─────────────────────────────────────────────────────────────────────────"
echo "Step 2: Checking if job already exists..."
echo "─────────────────────────────────────────────────────────────────────────"

JOB_EXISTS=$(gcloud run jobs describe "${JOB_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format="value(name)" 2>/dev/null || echo "")

if [ -n "${JOB_EXISTS}" ]; then
    echo "Job '${JOB_NAME}' already exists. Updating..."

    gcloud run jobs update "${JOB_NAME}" \
        --image="${IMAGE}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" \
        --command=python \
        --args="bootstrap_simulations.py,--limit,${LIMIT}" \
        --set-env-vars="ENV=production" \
        --max-retries="${MAX_RETRIES}" \
        --memory="${MEMORY}" \
        --cpu="${CPU}" \
        --timeout="${TIMEOUT}" \
        --set-cloudsql-instances="clickrate-477217:us-central1:scalpyn" \
        --set-secrets="DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest,ENCRYPTION_KEY=encryption-key:latest,REDIS_URL=redis-url:latest,AI_KEYS_ENCRYPTION_KEY=ai-keys-encryption-key:latest"

    echo "✓ Job updated successfully"
else
    echo "Creating new job..."

    gcloud run jobs create "${JOB_NAME}" \
        --image="${IMAGE}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" \
        --command=python \
        --args="bootstrap_simulations.py,--limit,${LIMIT}" \
        --set-env-vars="ENV=production" \
        --max-retries="${MAX_RETRIES}" \
        --memory="${MEMORY}" \
        --cpu="${CPU}" \
        --timeout="${TIMEOUT}" \
        --set-cloudsql-instances="clickrate-477217:us-central1:scalpyn" \
        --set-secrets="DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest,ENCRYPTION_KEY=encryption-key:latest,REDIS_URL=redis-url:latest,AI_KEYS_ENCRYPTION_KEY=ai-keys-encryption-key:latest"

    echo "✓ Job created successfully"
fi

echo ""

# ── Step 3: Execute Job ─────────────────────────────────────────────────────
echo "─────────────────────────────────────────────────────────────────────────"
echo "Step 3: Executing job..."
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
echo "Starting job execution..."

# Execute job and capture the execution name
EXECUTION_OUTPUT=$(gcloud run jobs execute "${JOB_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --wait 2>&1)

EXECUTION_NAME=$(echo "${EXECUTION_OUTPUT}" | grep -oP 'execution \K[^ ]+' | head -1 || echo "")

if [ -z "${EXECUTION_NAME}" ]; then
    # Try to get the latest execution
    EXECUTION_NAME=$(gcloud run jobs executions list \
        --job="${JOB_NAME}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" \
        --limit=1 \
        --format="value(name)" 2>/dev/null || echo "")
fi

echo ""
echo "✓ Job execution started"
echo ""

if [ -n "${EXECUTION_NAME}" ]; then
    echo "Execution name: ${EXECUTION_NAME}"
    echo ""
fi

# ── Step 4: Monitor Execution ───────────────────────────────────────────────
echo "─────────────────────────────────────────────────────────────────────────"
echo "Step 4: Monitoring execution..."
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
echo "You can monitor the job in real-time using:"
echo ""
echo "  gcloud logging read \"resource.type=cloud_run_job AND resource.labels.job_name=${JOB_NAME}\" \\"
echo "    --project=${PROJECT_ID} \\"
echo "    --limit=100 \\"
echo "    --format=json"
echo ""
echo "Or view in Cloud Console:"
echo "  https://console.cloud.google.com/run/jobs/details/${REGION}/${JOB_NAME}?project=${PROJECT_ID}"
echo ""

# Wait a moment for logs to propagate
sleep 5

echo "Fetching recent logs..."
echo ""

gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=${JOB_NAME}" \
    --project="${PROJECT_ID}" \
    --limit=50 \
    --format="table(timestamp,textPayload)" \
    --freshness=5m 2>/dev/null || echo "Logs not yet available. Check Cloud Console."

echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo "Step 5: Execution complete"
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
echo "To check the final status:"
echo ""
if [ -n "${EXECUTION_NAME}" ]; then
    echo "  gcloud run jobs executions describe ${EXECUTION_NAME} \\"
    echo "    --job=${JOB_NAME} \\"
    echo "    --region=${REGION} \\"
    echo "    --project=${PROJECT_ID}"
else
    echo "  gcloud run jobs executions list \\"
    echo "    --job=${JOB_NAME} \\"
    echo "    --region=${REGION} \\"
    echo "    --project=${PROJECT_ID}"
fi
echo ""
echo "To validate database records:"
echo ""
echo '  psql -c "SELECT COUNT(*) FROM trade_simulations;"'
echo ""
echo "════════════════════════════════════════════════════════════════════════════"
