#!/usr/bin/env bash
# scripts/promote-cloud-run-topology.sh
#
# Reproducible recovery automation for the Task #239 Cloud Run topology.
# Creates (or reconciles) the 4 worker/beat services that should accompany
# the `scalpyn` API service, using the image currently running in the API
# service as the source of truth (so it cannot drift from the deployed
# code). Idempotent: re-runs deploy a new revision but never breaks an
# existing one.
#
# WHEN TO USE:
#   - Cloud Build trigger silently dropped the worker/beat deploy steps
#     (observed 2026-05-07: 6 consecutive green builds while
#     `gcloud run services list` showed only `scalpyn`). Symptom in the
#     app: queues fill up, nothing drains, /api/system/celery-status
#     reports a single embedded worker.
#   - Manual recovery when you need to bring topology back fast without
#     waiting on the next Cloud Build cycle.
#
# WHAT IT DOES NOT REPLACE:
#   - The canonical definition stays in `cloudbuild.yaml`. This script is
#     a recovery shortcut, not the steady-state pipeline. After running
#     it, still investigate why the trigger / topology-check failed to
#     prevent the gap (Task #244 candidate).
#
# REQUIREMENTS:
#   - Run from Cloud Shell (or any host) with `gcloud` authenticated and
#     IAM permissions to deploy Cloud Run services in
#     `clickrate-477217:us-central1`.
#   - The `scalpyn` API service must already exist (we read its image).
#
# USAGE:
#   bash scripts/promote-cloud-run-topology.sh
#
# Override defaults via env-vars if needed:
#   PROJECT=…  REGION=…  bash scripts/promote-cloud-run-topology.sh
#
set -euo pipefail

REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-clickrate-477217}"

# Broker URL kept inline to mirror cloudbuild.yaml exactly. Migrate to
# Secret Manager (gotcha listed in cloudbuild.yaml comments) is preexisting
# follow-up — out of scope for this recovery script.
REDIS_URL="${REDIS_URL:-redis://default:J5JGA0YkjrGvldQ7zOInNqjQBUALvFsl@redis-18005.c279.us-central1-1.gce.cloud.redislabs.com:18005/0}"

# FORCE_RESTART bumped to current UTC so the deploy creates a new revision
# even if all other args match an existing one (kills any poisoned worker).
FORCE_RESTART="${FORCE_RESTART:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

echo "==> Reading current image from scalpyn API service..."
IMAGE="$(gcloud run services describe scalpyn \
  --region="$REGION" \
  --project="$PROJECT" \
  --format='value(spec.template.spec.containers[0].image)')"

if [ -z "$IMAGE" ]; then
  echo "FATAL: could not read image from scalpyn service. Is it deployed?" >&2
  exit 1
fi
echo "    Using image: $IMAGE"
echo "    FORCE_RESTART=$FORCE_RESTART"
echo

deploy_worker() {
  local name="$1" queues="$2" max_inst="$3" memory="$4" concurrency="$5" run_beat="$6"
  echo "==> Deploying $name (queues=${queues:-<none>}, max=$max_inst, mem=$memory, beat=$run_beat)..."
  gcloud run deploy "$name" \
    --image="$IMAGE" \
    --region="$REGION" \
    --project="$PROJECT" \
    --platform=managed \
    --quiet \
    --no-allow-unauthenticated \
    --ingress=internal \
    --port=8080 \
    --timeout=300 \
    --timeout-startup=540 \
    --min-instances=1 \
    --max-instances="$max_inst" \
    --no-cpu-throttling \
    --cpu-boost \
    --memory="$memory" \
    --add-cloudsql-instances="$PROJECT:$REGION:scalpyn" \
    --update-env-vars="REDIS_URL=$REDIS_URL,FORCE_RESTART=$FORCE_RESTART,ENABLE_GATE_WS=1,WORKER_QUEUES=$queues,RUN_BEAT=$run_beat,CELERY_CONCURRENCY=$concurrency,SKIP_STRUCTURAL_SCHEDULER=1,SKIP_MICROSTRUCTURE_SCHEDULER=1,SKIP_PIPELINE_SCHEDULER=1"
  echo
}

# Mirrors cloudbuild.yaml lines 142-290 exactly (queues, sizing, env).
deploy_worker "scalpyn-worker-micro"       "microstructure" "5" "2Gi" "4" "0"
deploy_worker "scalpyn-worker-structural"  "structural"     "3" "2Gi" "2" "0"
deploy_worker "scalpyn-worker-execution"   "execution"      "2" "1Gi" "2" "0"
deploy_worker "scalpyn-beat"               ""               "1" "1Gi" "2" "1"

echo "==> Final state of Cloud Run topology in $REGION:"
gcloud run services list \
  --region="$REGION" \
  --project="$PROJECT" \
  --filter="metadata.name~scalpyn" \
  --format="table(metadata.name, status.conditions[0].status, status.latestReadyRevisionName)"

echo
echo "==> Expected: 5 rows, all status=True. If fewer, inspect the failed"
echo "    deploy output above and consult"
echo "    backend/docs/runbooks/cloud-run-celery-topology.md"
