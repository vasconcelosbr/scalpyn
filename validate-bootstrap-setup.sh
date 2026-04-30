#!/bin/bash
# validate-bootstrap-setup.sh
#
# Validation script to check if all components are in place for the
# bootstrap simulation job.

set -e

echo "════════════════════════════════════════════════════════════════════════════"
echo "  BOOTSTRAP SIMULATION JOB - SETUP VALIDATION"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""

EXIT_CODE=0

# ── Check 1: Bootstrap script exists ────────────────────────────────────────
echo "✓ Checking bootstrap_simulations.py..."
if [ -f "backend/bootstrap_simulations.py" ]; then
    echo "  ✓ Script found at: backend/bootstrap_simulations.py"
else
    echo "  ✗ ERROR: bootstrap_simulations.py not found in backend/"
    EXIT_CODE=1
fi
echo ""

# ── Check 2: Helper script exists ───────────────────────────────────────────
echo "✓ Checking run-bootstrap-job.sh..."
if [ -f "run-bootstrap-job.sh" ] && [ -x "run-bootstrap-job.sh" ]; then
    echo "  ✓ Helper script found and executable: run-bootstrap-job.sh"
else
    echo "  ✗ ERROR: run-bootstrap-job.sh not found or not executable"
    EXIT_CODE=1
fi
echo ""

# ── Check 3: Cloud Run Job YAML ─────────────────────────────────────────────
echo "✓ Checking cloud-run-job.yaml..."
if [ -f "cloud-run-job.yaml" ]; then
    echo "  ✓ Configuration found: cloud-run-job.yaml"
else
    echo "  ✗ ERROR: cloud-run-job.yaml not found"
    EXIT_CODE=1
fi
echo ""

# ── Check 4: Documentation ──────────────────────────────────────────────────
echo "✓ Checking documentation..."
if [ -f "docs/BOOTSTRAP_SIMULATION_JOB.md" ]; then
    echo "  ✓ Documentation found: docs/BOOTSTRAP_SIMULATION_JOB.md"
else
    echo "  ✗ ERROR: docs/BOOTSTRAP_SIMULATION_JOB.md not found"
    EXIT_CODE=1
fi
echo ""

# ── Check 5: Dockerfile includes bootstrap ──────────────────────────────────
echo "✓ Checking Dockerfile..."
if [ -f "backend/Dockerfile" ]; then
    echo "  ✓ Dockerfile found: backend/Dockerfile"
    if grep -q "COPY . ." backend/Dockerfile; then
        echo "  ✓ Dockerfile copies all files (includes bootstrap_simulations.py)"
    else
        echo "  ⚠ WARNING: Dockerfile may not copy bootstrap_simulations.py"
    fi
else
    echo "  ✗ ERROR: backend/Dockerfile not found"
    EXIT_CODE=1
fi
echo ""

# ── Check 6: Required models ────────────────────────────────────────────────
echo "✓ Checking required models..."
if [ -f "backend/app/models/trade_simulation.py" ]; then
    echo "  ✓ TradeSimulation model found"
else
    echo "  ✗ ERROR: TradeSimulation model not found"
    EXIT_CODE=1
fi
echo ""

# ── Check 7: SimulationService ──────────────────────────────────────────────
echo "✓ Checking SimulationService..."
if [ -f "backend/app/services/simulation_service.py" ]; then
    echo "  ✓ SimulationService found"
else
    echo "  ✗ ERROR: SimulationService not found"
    EXIT_CODE=1
fi
echo ""

# ── Check 8: cloudbuild.yaml ────────────────────────────────────────────────
echo "✓ Checking cloudbuild.yaml..."
if [ -f "cloudbuild.yaml" ]; then
    echo "  ✓ Cloud Build config found"
    # Check if it builds from backend directory
    if grep -q "backend" cloudbuild.yaml; then
        echo "  ✓ Cloud Build configured for backend directory"
    fi
else
    echo "  ⚠ WARNING: cloudbuild.yaml not found (optional)"
fi
echo ""

# ── Summary ─────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════════════════"
echo "  VALIDATION SUMMARY"
echo "════════════════════════════════════════════════════════════════════════════"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ All checks passed!"
    echo ""
    echo "Next steps:"
    echo "  1. Build and push Docker image:"
    echo "     gcloud builds submit --config cloudbuild.yaml"
    echo ""
    echo "  2. Run bootstrap job:"
    echo "     ./run-bootstrap-job.sh"
    echo ""
    echo "  3. Validate results:"
    echo '     psql -c "SELECT COUNT(*) FROM trade_simulations;"'
    echo ""
else
    echo "✗ Validation failed with errors"
    echo ""
    echo "Please fix the errors above before proceeding."
fi

echo "════════════════════════════════════════════════════════════════════════════"

exit $EXIT_CODE
