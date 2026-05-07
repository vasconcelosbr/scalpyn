# Bootstrap Simulation Job — Production Execution Guide

## Overview

This guide explains how to execute the simulation bootstrap process in a production-safe way using Google Cloud Run Jobs. The bootstrap process populates the `trade_simulations` table with at least 1000+ rows for ML training.

## Architecture

### Why Cloud Run Jobs?

Cloud Run **services** are stateless and scale to zero between requests. Running a long-running bootstrap process inside a service shell is **not recommended** because:

1. **Ephemeral instances**: Cloud Run services can terminate at any time
2. **Resource constraints**: Services are optimized for request-response patterns
3. **No isolation**: Running heavy batch jobs inside a service can affect API performance
4. **Cost inefficiency**: Services are billed per request/time, not batch jobs

Cloud Run **Jobs** provide the proper execution environment:

- **Dedicated resources**: 1 vCPU, 1 GiB memory, isolated from API traffic
- **Timeout control**: Up to 1 hour per execution (configurable)
- **Retry logic**: Automatic retry on failure with backoff
- **Cost-efficient**: Pay only for actual execution time
- **Production-safe**: No impact on running services

## Prerequisites

Before running the bootstrap job, ensure:

1. **Docker image is built and pushed**:
   ```bash
   gcloud builds submit --config cloudbuild.yaml
   ```

2. **Required secrets exist in Secret Manager**:
   - `database-url`: PostgreSQL connection string
   - `jwt-secret`: JWT signing key
   - `encryption-key`: Data encryption key
   - `redis-url`: Redis connection string
   - `ai-keys-encryption-key`: AI keys encryption key

3. **Cloud SQL instance is running**:
   - Instance: `clickrate-477217:us-central1:scalpyn`
   - Database: `scalpyn`
   - Schema: Up-to-date via Alembic migrations

4. **Required permissions**:
   - `roles/run.admin` or `roles/run.developer`
   - `roles/artifactregistry.reader`
   - `roles/cloudsql.client`
   - `roles/secretmanager.secretAccessor`

## Quick Start

### Method 1: Using the Helper Script (Recommended)

The simplest way to execute the bootstrap job:

```bash
# From repository root
./run-bootstrap-job.sh
```

**Options:**
```bash
# Process 10,000 decisions
./run-bootstrap-job.sh --limit 10000

# Use a different region
./run-bootstrap-job.sh --region us-east1

# Custom project
./run-bootstrap-job.sh --project my-project-id
```

The script will:
1. ✓ Verify the Docker image exists
2. ✓ Create or update the Cloud Run Job
3. ✓ Execute the job
4. ✓ Display logs and status
5. ✓ Provide validation commands

### Method 2: Manual Execution

If you prefer manual control:

#### Step 1: Create the Job

```bash
gcloud run jobs create bootstrap-simulations \
  --image us-central1-docker.pkg.dev/clickrate-477217/scalpyn/scalpyn:latest \
  --region us-central1 \
  --project clickrate-477217 \
  --command python \
  --args bootstrap_simulations.py,--limit,5000 \
  --set-env-vars ENV=production \
  --max-retries 1 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600s \
  --set-cloudsql-instances clickrate-477217:us-central1:scalpyn \
  --set-secrets DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest,ENCRYPTION_KEY=encryption-key:latest,REDIS_URL=redis-url:latest,AI_KEYS_ENCRYPTION_KEY=ai-keys-encryption-key:latest
```

#### Step 2: Execute the Job

```bash
gcloud run jobs execute bootstrap-simulations \
  --region us-central1 \
  --project clickrate-477217 \
  --wait
```

#### Step 3: Monitor Execution

```bash
# View logs in real-time
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=bootstrap-simulations" \
  --project clickrate-477217 \
  --limit 100 \
  --format "table(timestamp,textPayload)"

# Check job status
gcloud run jobs describe bootstrap-simulations \
  --region us-central1 \
  --project clickrate-477217
```

## Validation

After the job completes successfully, validate the results:

### 1. Check Database Records

```sql
-- Connect to Cloud SQL
gcloud sql connect scalpyn --user=postgres --project=clickrate-477217

-- Check record count
SELECT COUNT(*) FROM trade_simulations;
-- Expected: >= 1000

-- Check data distribution
SELECT
    result,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as percentage
FROM trade_simulations
GROUP BY result
ORDER BY count DESC;

-- Check unique symbols
SELECT COUNT(DISTINCT symbol) FROM trade_simulations;

-- Check date range
SELECT
    MIN(timestamp_entry) as oldest,
    MAX(timestamp_entry) as newest,
    MAX(timestamp_entry) - MIN(timestamp_entry) as timespan
FROM trade_simulations;
```

### 2. Verify Simulation Stats

Using the API:

```bash
# Get simulation statistics
curl -X GET https://scalpyn-xxxx.run.app/api/simulations/status

# Expected response:
# {
#   "total": 1000+,
#   "wins": XX,
#   "losses": XX,
#   "win_rate": XX.XX,
#   "unique_symbols": XX,
#   "avg_time_to_result_seconds": XX.XX
# }
```

### 3. Check Logs for Success

```bash
# Look for completion message
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=bootstrap-simulations" \
  --project clickrate-477217 \
  --limit 50 \
  --format json | jq '.[] | select(.textPayload | contains("Bootstrap complete"))'
```

## Troubleshooting

### Job Fails with "Image not found"

**Cause**: Docker image not built or pushed to Artifact Registry

**Solution**:
```bash
# Build and push image
gcloud builds submit --config cloudbuild.yaml
```

### Job Fails with "Database connection error"

**Cause**: Database secret not configured or Cloud SQL instance unreachable

**Solution**:
```bash
# Check secret exists
gcloud secrets describe database-url --project clickrate-477217

# Verify Cloud SQL instance is running
gcloud sql instances describe scalpyn --project clickrate-477217

# Check Cloud SQL connection in job config
gcloud run jobs describe bootstrap-simulations \
  --region us-central1 \
  --project clickrate-477217 \
  --format "value(spec.template.spec.template.spec.cloudSqlInstances)"
```

### Job Times Out

**Cause**: Processing too many decisions or slow network

**Solution**:
```bash
# Increase timeout (max: 3600s = 1 hour)
gcloud run jobs update bootstrap-simulations \
  --timeout 3600s \
  --region us-central1 \
  --project clickrate-477217

# Or reduce limit
gcloud run jobs update bootstrap-simulations \
  --args bootstrap_simulations.py,--limit,2000 \
  --region us-central1 \
  --project clickrate-477217
```

### Job Fails with "Permission denied"

**Cause**: Service account lacks necessary permissions

**Solution**:
```bash
# Check service account
gcloud run jobs describe bootstrap-simulations \
  --region us-central1 \
  --project clickrate-477217 \
  --format "value(spec.template.spec.template.spec.serviceAccountName)"

# Grant required roles
gcloud projects add-iam-policy-binding clickrate-477217 \
  --member serviceAccount:SERVICE_ACCOUNT \
  --role roles/cloudsql.client

gcloud projects add-iam-policy-binding clickrate-477217 \
  --member serviceAccount:SERVICE_ACCOUNT \
  --role roles/secretmanager.secretAccessor
```

### Duplicate Simulations

**Cause**: Running bootstrap multiple times without proper deduplication

**Solution**: The `trade_simulations` table should have a UNIQUE constraint on `decision_id`. Check the schema:

```sql
-- Verify unique constraint exists
SELECT constraint_name, constraint_type
FROM information_schema.table_constraints
WHERE table_name = 'trade_simulations';

-- If needed, add manually (after removing duplicates):
ALTER TABLE trade_simulations
ADD CONSTRAINT unique_decision_id UNIQUE (decision_id);
```

## Re-running the Job

The bootstrap job is designed to be **idempotent** when the `decision_id` unique constraint is in place. To re-run:

```bash
# Execute again with same or different limit
gcloud run jobs execute bootstrap-simulations \
  --region us-central1 \
  --project clickrate-477217 \
  --wait
```

Existing simulations will be skipped; only new decisions will be processed.

## Job Configuration Details

### Resource Allocation

- **CPU**: 1 vCPU (sufficient for sequential processing)
- **Memory**: 1 GiB (adequate for batch operations)
- **Timeout**: 3600s (1 hour)
- **Max Retries**: 1 (retry once on failure)

### Environment Variables

- `ENV=production`: Production environment flag
- Database/Redis/Secrets: Loaded from Secret Manager

### Cloud SQL Connection

- **Instance**: `clickrate-477217:us-central1:scalpyn`
- **Connection Type**: Unix socket via Cloud SQL Proxy (automatic)
- **Connection String**: Injected via `DATABASE_URL` secret

## Cost Estimation

Cloud Run Jobs pricing (as of 2024):

- **CPU**: $0.00002400 per vCPU-second
- **Memory**: $0.00000250 per GiB-second
- **Requests**: No request charges for jobs

**Estimated cost per execution** (1000 decisions, ~300s runtime):
- CPU: 1 vCPU × 300s × $0.00002400 = $0.0072
- Memory: 1 GiB × 300s × $0.00000250 = $0.00075
- **Total**: ~$0.008 per run (less than $0.01)

## Next Steps

After successful bootstrap:

1. **Verify ML training data**:
   ```bash
   curl -X GET https://scalpyn-xxxx.run.app/api/simulations/status
   ```

2. **Train ML model**:
   ```bash
   curl -X POST https://scalpyn-xxxx.run.app/api/ml/train
   ```

3. **Monitor automatic simulations**:
   - Celery beat runs simulations every 10 minutes automatically
   - Monitor via: `GET /api/simulations/status`

4. **Set up alerts** (optional):
   - Create Cloud Monitoring alert for job failures
   - Set up Slack/email notifications

## References

- [Cloud Run Jobs Documentation](https://cloud.google.com/run/docs/quickstarts/jobs)
- [Cloud SQL Connections](https://cloud.google.com/sql/docs/postgres/connect-run)
- [Secret Manager Integration](https://cloud.google.com/run/docs/configuring/secrets)
- [Simulation Engine Docs](./SIMULATION_ENGINE.md)
- [ML Pipeline Docs](./ML_PIPELINE.md)
