#!/usr/bin/env bash
# scripts/promote-cloud-run-topology.sh
#
# Reproducible recovery automation for the Task #239 Cloud Run topology.
# Creates (or reconciles) the 4 worker/beat services that should accompany
# the `scalpyn` API service, using the image AND the REDIS_URL currently
# running in the API service as the source of truth (so it cannot drift
# from the deployed code and no plaintext credential is duplicated in this
# script). Idempotent: re-runs deploy a new revision but never breaks an
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
#   - The `scalpyn` API service must already exist (we read its image and
#     REDIS_URL from it).
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

# FORCE_RESTART bumped to current UTC so the deploy creates a new revision
# even if all other args match an existing one (kills any poisoned worker).
FORCE_RESTART="${FORCE_RESTART:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

echo "==> Reading current image + REDIS_URL from scalpyn API service..."
IMAGE="$(gcloud run services describe scalpyn \
  --region="$REGION" \
  --project="$PROJECT" \
  --format='value(spec.template.spec.containers[0].image)')"

if [ -z "$IMAGE" ]; then
  echo "FATAL: could not read image from scalpyn service. Is it deployed?" >&2
  exit 1
fi

# Read REDIS_URL from the running scalpyn service so we don't hardcode the
# credential here. Reviewer concern (Task #243): plaintext duplication
# increases credential exposure surface. Sourcing from the live service
# means rotation in cloudbuild.yaml + redeploy of scalpyn automatically
# propagates here on next run.
REDIS_URL="$(gcloud run services describe scalpyn \
  --region="$REGION" \
  --project="$PROJECT" \
  --format='value(spec.template.spec.containers[0].env.filter("name:REDIS_URL").extract(value).flatten())')"

if [ -z "$REDIS_URL" ]; then
  echo "FATAL: could not read REDIS_URL env var from scalpyn service." >&2
  echo "       Set it manually via REDIS_URL=… before re-running." >&2
  exit 1
fi

echo "    Image: $IMAGE"
echo "    REDIS_URL: <read from scalpyn service, ${#REDIS_URL} chars>"
echo "    FORCE_RESTART: $FORCE_RESTART"
echo

# Build env-vars string. Workers carry CELERY_CONCURRENCY; beat does not
# (mirrors cloudbuild.yaml line 334 — beat has no CELERY_CONCURRENCY env).
deploy_service() {
  local name="$1" queues="$2" max_inst="$3" memory="$4" concurrency="$5" run_beat="$6"

  local env_vars="REDIS_URL=$REDIS_URL,FORCE_RESTART=$FORCE_RESTART,ENABLE_GATE_WS=1,WORKER_QUEUES=$queues,RUN_BEAT=$run_beat,SKIP_STRUCTURAL_SCHEDULER=1,SKIP_MICROSTRUCTURE_SCHEDULER=1,SKIP_PIPELINE_SCHEDULER=1"
  if [ -n "$concurrency" ]; then
    env_vars="$env_vars,CELERY_CONCURRENCY=$concurrency"
  fi

  echo "==> Deploying $name (queues='${queues}', max=$max_inst, mem=$memory, beat=$run_beat, concurrency='${concurrency:-<unset>}')..."
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
    --min-instances=1 \
    --max-instances="$max_inst" \
    --no-cpu-throttling \
    --cpu-boost \
    --memory="$memory" \
    --add-cloudsql-instances="$PROJECT:$REGION:scalpyn" \
    --update-env-vars="$env_vars"
  echo
}

# Mirrors cloudbuild.yaml lines 142-334 EXACTLY:
#                              name                        queues          max  mem   concurrency  run_beat
deploy_service "scalpyn-worker-micro"        "microstructure" "5" "2Gi" "4" "0"
deploy_service "scalpyn-worker-structural"   "structural"     "3" "2Gi" "2" "0"
deploy_service "scalpyn-worker-execution"    "execution"      "2" "1Gi" "2" "0"
deploy_service "scalpyn-beat"                ""               "1" "1Gi" ""  "1"

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
