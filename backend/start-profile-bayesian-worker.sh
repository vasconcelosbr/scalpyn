#!/bin/sh
set -eu

queues="${PROFILE_BAYESIAN_WORKER_QUEUES:-profile_bayesian,profile_optimization}"
old_ifs="$IFS"
IFS=','
set -- $queues
IFS="$old_ifs"

if [ "$#" -eq 0 ]; then
  echo "PROFILE_BAYESIAN_WORKER_QUEUES must not be empty" >&2
  exit 64
fi

for queue in "$@"; do
  case "$queue" in
    profile_bayesian|profile_optimization) ;;
    *)
      echo "Unsupported Bayesian worker queue: $queue" >&2
      exit 64
      ;;
  esac
done

concurrency="${PROFILE_BAYESIAN_WORKER_CONCURRENCY:-1}"
case "$concurrency" in
  ''|*[!0-9]*|0)
    echo "PROFILE_BAYESIAN_WORKER_CONCURRENCY must be a positive integer" >&2
    exit 64
    ;;
esac

export PROFILE_BAYESIAN_DEDICATED_APP=1

exec celery -A app.tasks.profile_bayesian_celery_app:celery_app worker \
  --loglevel="${CELERY_LOGLEVEL:-INFO}" \
  --concurrency="$concurrency" \
  --queues="$queues" \
  --hostname="profile-bayesian@%h"
