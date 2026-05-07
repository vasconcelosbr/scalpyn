#!/usr/bin/env bash
# scripts/promote-cloud-run-topology.sh
#
# Reproducible recovery automation for the Task #239 Cloud Run topology.
# Creates (or reconciles) the 4 worker/beat services that should accompany
# the `scalpyn` API service. Approach: clone the entire `scalpyn` service
# spec via `gcloud run services describe --format=export`, then patch
# only the worker-specific fields (name, scaling, env vars). This way we
# inherit DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, AI_KEYS_ENCRYPTION_KEY,
# REDIS_URL and any Secret Manager bindings already configured on the API
# service — without ever touching the credentials in this script.
#
# Why describe + replace (instead of `gcloud run deploy --update-env-vars`):
# Workers are NEW services. `deploy` creates them with ONLY the env vars
# we pass — DATABASE_URL etc. would be missing and the container would
# exit 1 on `start.sh` line 39 (env diagnostics) or line 90 (alembic).
# `services replace` accepts the full spec and creates the service in one
# atomic call.
#
# WHEN TO USE:
#   - Cloud Build trigger silently dropped the worker/beat deploy steps
#     (observed 2026-05-07: 6 consecutive green builds while
#     `gcloud run services list` showed only `scalpyn`).
#   - Manual recovery when you need to bring topology back fast without
#     waiting on the next Cloud Build cycle.
#
# WHAT IT DOES NOT REPLACE:
#   - The canonical definition stays in `cloudbuild.yaml`. After running
#     this, still investigate why the trigger / topology-check failed.
#
# REQUIREMENTS:
#   - `gcloud` authenticated with permissions to deploy in
#     `clickrate-477217:us-central1`.
#   - The `scalpyn` API service must already exist (we read its spec).
#   - Python 3 available (used to patch the YAML — preinstalled in Cloud Shell).
#
# USAGE:
#   bash scripts/promote-cloud-run-topology.sh
#
set -euo pipefail

REGION="${REGION:-us-central1}"
PROJECT="${PROJECT:-clickrate-477217}"
FORCE_RESTART="${FORCE_RESTART:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
TMPDIR="$(mktemp -d -t scalpyn-topology.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Exporting scalpyn service spec..."
gcloud run services describe scalpyn \
  --region="$REGION" \
  --project="$PROJECT" \
  --format=export > "$TMPDIR/scalpyn.yaml"
echo "    Saved to $TMPDIR/scalpyn.yaml ($(wc -l <"$TMPDIR/scalpyn.yaml") lines)"
echo "    FORCE_RESTART: $FORCE_RESTART"
echo

# Patch the exported spec into a worker/beat spec.
# Args:  $1=src_yaml  $2=dst_yaml  $3=new_name  $4=queues  $5=concurrency
#        $6=run_beat  $7=max_instances  $8=memory
patch_spec() {
  local src="$1" dst="$2" new_name="$3" queues="$4" concurrency="$5"
  local run_beat="$6" max_inst="$7" memory="$8"

  python3 - "$src" "$dst" "$new_name" "$queues" "$concurrency" \
    "$run_beat" "$max_inst" "$memory" "$FORCE_RESTART" <<'PY'
import sys, yaml, re

src, dst, new_name, queues, concurrency, run_beat, max_inst, memory, force_restart = sys.argv[1:10]

with open(src) as f:
    spec = yaml.safe_load(f)

# Rename the service
spec['metadata']['name'] = new_name

# Strip read-only / instance-specific server-side fields that `services
# replace` rejects (they're regenerated on apply).
spec['metadata'].pop('uid', None)
spec['metadata'].pop('resourceVersion', None)
spec['metadata'].pop('generation', None)
spec['metadata'].pop('creationTimestamp', None)
spec['metadata'].pop('selfLink', None)
ann = spec['metadata'].setdefault('annotations', {})
for k in list(ann):
    if k.startswith('serving.knative.dev/') or k.startswith('run.googleapis.com/operation-id'):
        ann.pop(k, None)
spec.pop('status', None)

# Rename the revision template (otherwise create fails with "revision name
# already exists" because the API service's revision name is in there).
template = spec['spec']['template']
template['metadata'].pop('name', None)
tmpl_ann = template['metadata'].setdefault('annotations', {})

# Scaling
tmpl_ann['autoscaling.knative.dev/minScale'] = '1'
tmpl_ann['autoscaling.knative.dev/maxScale'] = max_inst

# Patch container env vars: keep all secrets/secret-refs from API, override
# only the worker-specific ones. ENABLE_GATE_WS=1 is also forced because
# the original cloudbuild.yaml sets it on every service.
container = template['spec']['containers'][0]
env = container.setdefault('env', [])

worker_overrides = {
    'WORKER_QUEUES': queues,
    'RUN_BEAT': run_beat,
    'FORCE_RESTART': force_restart,
    'ENABLE_GATE_WS': '1',
    'SKIP_STRUCTURAL_SCHEDULER': '1',
    'SKIP_MICROSTRUCTURE_SCHEDULER': '1',
    'SKIP_PIPELINE_SCHEDULER': '1',
}
if concurrency:
    worker_overrides['CELERY_CONCURRENCY'] = concurrency

# Remove existing env entries we want to override (keeps secret-refs intact)
to_override = set(worker_overrides) | {'CELERY_CONCURRENCY'}
env[:] = [e for e in env if e.get('name') not in to_override or 'valueFrom' not in e]
# Drop plain-value duplicates of the override keys
env[:] = [e for e in env if not (e.get('name') in worker_overrides and 'value' in e)]
# If concurrency not set for this service (beat), drop any leftover entry
if not concurrency:
    env[:] = [e for e in env if e.get('name') != 'CELERY_CONCURRENCY']
# Append fresh values
for k, v in worker_overrides.items():
    env.append({'name': k, 'value': v})

# Memory
res = container.setdefault('resources', {}).setdefault('limits', {})
res['memory'] = memory

with open(dst, 'w') as f:
    yaml.safe_dump(spec, f, sort_keys=False)
print(f"  patched -> {dst}", file=sys.stderr)
PY
}

deploy_service() {
  local name="$1" queues="$2" max_inst="$3" memory="$4" concurrency="$5" run_beat="$6"
  local spec="$TMPDIR/$name.yaml"

  echo "==> Building spec for $name (queues='${queues}', max=$max_inst, mem=$memory, beat=$run_beat, concurrency='${concurrency:-<unset>}')..."
  patch_spec "$TMPDIR/scalpyn.yaml" "$spec" "$name" "$queues" "$concurrency" "$run_beat" "$max_inst" "$memory"

  echo "==> Applying $name via services replace..."
  gcloud run services replace "$spec" \
    --region="$REGION" \
    --project="$PROJECT" \
    --quiet
  echo
}

# Mirrors cloudbuild.yaml topology:
#                              name                       queues          max  mem   concurrency  run_beat
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
echo "==> Expected: 5 rows, all status=True. If fewer, run:"
echo "    gcloud run services describe <name> --region=$REGION --format='value(status.conditions)'"
echo "    to see why the failed one didn't reach Ready."
