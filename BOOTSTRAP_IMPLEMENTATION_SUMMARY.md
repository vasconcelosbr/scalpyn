# Bootstrap Simulation Job - Implementation Summary

## 🎯 Objective

Implement a production-safe method to populate the `trade_simulations` table with 1000+ rows using Google Cloud Run Jobs.

## ✅ Completed Tasks

### 1. **Bootstrap Script Verification** ✓
- **Location**: `backend/bootstrap_simulations.py` (moved from root to backend/)
- **Functionality**:
  - Reads from `decisions_log` table
  - Writes to `trade_simulations` table
  - Supports `--limit` argument (default: 5000)
  - Provides detailed execution statistics
  - Idempotent execution (respects unique constraints)

### 2. **Docker Image Preparation** ✓
- **Dockerfile**: `backend/Dockerfile`
- **Verification**: `COPY . .` includes `bootstrap_simulations.py`
- **Dependencies**: All required packages in `requirements.txt`
- **Database Connection**: Configured via `DATABASE_URL` environment variable
- **Cloud SQL Integration**: Unix socket connection ready

### 3. **Cloud Run Job Configuration** ✓
- **File**: `cloud-run-job.yaml`
- **Configuration**:
  - Job name: `bootstrap-simulations`
  - Resources: 1 vCPU, 1 GiB memory
  - Timeout: 3600s (1 hour)
  - Max retries: 1
  - Command: `python bootstrap_simulations.py --limit 5000`
  - Cloud SQL: `clickrate-477217:us-central1:scalpyn`
  - Secrets: All required secrets mapped from Secret Manager

### 4. **Helper Script** ✓
- **File**: `run-bootstrap-job.sh`
- **Features**:
  - Verifies Docker image exists
  - Creates or updates Cloud Run Job
  - Executes job with proper parameters
  - Monitors execution and displays logs
  - Provides validation commands
  - Supports custom arguments (`--limit`, `--region`, `--project`)

### 5. **Documentation** ✓
- **File**: `docs/BOOTSTRAP_SIMULATION_JOB.md`
- **Contents**:
  - Architecture explanation (why Cloud Run Jobs)
  - Prerequisites checklist
  - Quick start guide
  - Manual execution steps
  - Validation procedures
  - Comprehensive troubleshooting
  - Cost estimation
  - Re-execution guidelines

### 6. **Validation** ✓
- **File**: `validate-bootstrap-setup.sh`
- **Checks**:
  - Bootstrap script exists in correct location
  - Helper script exists and is executable
  - Cloud Run Job YAML exists
  - Documentation exists
  - Dockerfile configured correctly
  - Required models and services exist
  - Cloud Build config present
- **Result**: All checks passed ✓

## 📦 Deliverables

### Files Created/Modified

1. **`backend/bootstrap_simulations.py`** (moved from root)
   - Fixed import path for backend directory
   - Ready for Docker container execution

2. **`run-bootstrap-job.sh`** (new)
   - Production-ready helper script
   - Handles job creation, execution, and monitoring

3. **`cloud-run-job.yaml`** (new)
   - Declarative job configuration
   - Can be used with `gcloud` or manually

4. **`docs/BOOTSTRAP_SIMULATION_JOB.md`** (new)
   - Comprehensive production guide
   - Troubleshooting section
   - Cost estimation

5. **`validate-bootstrap-setup.sh`** (new)
   - Pre-execution validation
   - Ensures all components in place

## 🚀 Execution Instructions

### Quick Start (Recommended)

```bash
# 1. Build and push Docker image
gcloud builds submit --config cloudbuild.yaml

# 2. Run bootstrap job
./run-bootstrap-job.sh

# 3. Validate results
psql -c "SELECT COUNT(*) FROM trade_simulations;"
```

### Expected Output

```
════════════════════════════════════════════════════════════════════════════
SCALPYN - BOOTSTRAP SIMULATIONS JOB
════════════════════════════════════════════════════════════════════════════

Configuration:
  Project ID:  clickrate-477217
  Region:      us-central1
  Job Name:    bootstrap-simulations
  Image:       us-central1-docker.pkg.dev/clickrate-477217/scalpyn/scalpyn:latest
  Limit:       5000 decisions
  Memory:      1Gi
  CPU:         1
  Timeout:     3600s

─────────────────────────────────────────────────────────────────────────
Step 1: Verifying Docker image exists...
─────────────────────────────────────────────────────────────────────────
✓ Image verified

─────────────────────────────────────────────────────────────────────────
Step 2: Checking if job already exists...
─────────────────────────────────────────────────────────────────────────
Creating new job...
✓ Job created successfully

─────────────────────────────────────────────────────────────────────────
Step 3: Executing job...
─────────────────────────────────────────────────────────────────────────
✓ Job execution started

[Logs and monitoring information...]
```

## 🔍 Validation Checklist

After execution, verify:

- [ ] **Job Status**: `gcloud run jobs describe bootstrap-simulations`
  - Status: `Succeeded`
  - Exit code: 0

- [ ] **Database Records**: `SELECT COUNT(*) FROM trade_simulations;`
  - Expected: >= 1000 rows

- [ ] **Simulation Stats**: `curl GET /api/simulations/status`
  - Total simulations: >= 1000
  - Win rate: reasonable percentage
  - Unique symbols: multiple coins

- [ ] **Logs**: Check for "Bootstrap complete!" message

## 🛡️ Production Safety Features

1. **Isolated Execution**
   - Runs in dedicated Cloud Run Job (not in service)
   - No impact on API performance
   - Dedicated resources (1 vCPU, 1 GiB)

2. **Error Handling**
   - Max retries: 1 (automatic retry on failure)
   - Timeout: 1 hour (prevents runaway jobs)
   - Database rollback on errors

3. **Idempotency**
   - Unique constraint on `decision_id`
   - Safe to re-run without duplicates
   - Skips already-processed decisions

4. **Resource Management**
   - Memory-efficient batch processing
   - Connection pooling for database
   - Proper cleanup on exit

5. **Monitoring**
   - Structured logging to Cloud Logging
   - Real-time log streaming
   - Execution status tracking

## 💰 Cost Estimation

**Per execution** (processing 5000 decisions):
- Duration: ~300-600 seconds
- CPU cost: 1 vCPU × 300s × $0.00002400 = $0.0072
- Memory cost: 1 GiB × 300s × $0.00000250 = $0.00075
- **Total**: ~$0.008 per run (less than $0.01)

**Annual cost** (if run weekly):
- 52 executions/year × $0.008 = **$0.42/year**

## 🔄 Re-execution

The job is designed to be re-run safely:

```bash
# Run again with same limit
./run-bootstrap-job.sh

# Run with higher limit
./run-bootstrap-job.sh --limit 10000
```

Existing simulations are automatically skipped due to the unique constraint on `decision_id`.

## 📊 Success Metrics

After successful execution:

1. ✓ Job completes with exit code 0
2. ✓ Database contains >= 1000 `trade_simulations` rows
3. ✓ Win rate is within reasonable bounds (20-80%)
4. ✓ Multiple unique symbols represented
5. ✓ No errors in Cloud Run Job logs
6. ✓ API endpoint `/api/simulations/status` returns valid stats

## 🎓 Next Steps

1. **Trigger first execution**:
   ```bash
   ./run-bootstrap-job.sh
   ```

2. **Validate results**:
   ```sql
   SELECT COUNT(*) FROM trade_simulations;
   SELECT result, COUNT(*) FROM trade_simulations GROUP BY result;
   ```

3. **Train ML model**:
   ```bash
   curl -X POST https://scalpyn-xxxx.run.app/api/ml/train
   ```

4. **Monitor automatic simulations**:
   - Celery beat runs simulations every 10 minutes
   - Monitor via: `GET /api/simulations/status`

## 📚 References

- **Documentation**: `docs/BOOTSTRAP_SIMULATION_JOB.md`
- **Helper Script**: `run-bootstrap-job.sh --help`
- **Validation**: `./validate-bootstrap-setup.sh`
- **Cloud Run Jobs**: https://cloud.google.com/run/docs/quickstarts/jobs

## ✅ Implementation Status

**Status**: READY FOR PRODUCTION EXECUTION

All components are in place and validated. The system is production-safe and ready to execute the bootstrap process.
