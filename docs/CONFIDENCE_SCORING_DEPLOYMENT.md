# Confidence-Weighted Scoring Deployment Guide

## Overview

This guide covers the gradual deployment of confidence-weighted scoring for the Scalpyn trading platform. The implementation uses a dual-write approach to safely roll out the new scoring algorithm with full monitoring and rollback capabilities.

## Architecture

### Phases Completed

#### Phase 10: Score Engine with Confidence Weighting ✅
- ScoreEngine now accepts IndicatorEnvelope objects with metadata
- Confidence multipliers applied to scoring rules
- Low-confidence indicators (< 0.5) are skipped
- Full backward compatibility maintained

#### Phase 11: Dual-Write Mode ✅
- Feature flag: `confidence_weighting.dual_write_mode`
- Both v1 (legacy) and v2 (confidence-weighted) scores computed
- Score deltas > 10 points logged for monitoring
- Database columns: `alpha_score_v2`, `confidence_metrics`, `scoring_version`

#### Phase 12: Automated Testing ✅
- Unit tests for confidence weighting
- Integration tests for dual-write mode
- Backward compatibility tests
- Edge case coverage

## Configuration

### Feature Flags

Located in `backend/app/services/seed_service.py`:

```python
"confidence_weighting": {
    "enabled": False,           # Use v2 scores for decisions
    "min_confidence": 0.5,      # Minimum confidence threshold
    "dual_write_mode": False,   # Compute both v1 and v2
}
```

### Configuration States

| State | enabled | dual_write_mode | Behavior |
|-------|---------|-----------------|----------|
| **Legacy** | False | False | v1 only (current production) |
| **Dual-Write** | False | True | Compute both, use v1, log deltas |
| **Confidence Only** | True | False | v2 only (full rollout) |
| **Emergency Rollback** | False | False | Revert to v1 immediately |

## Deployment Process

### Stage 1: Enable Dual-Write (Week 1)

**Objective**: Collect baseline metrics without affecting production decisions.

1. **Enable Dual-Write Mode**
   ```sql
   -- Update user's score config
   UPDATE configs
   SET value = jsonb_set(
       value,
       '{confidence_weighting,dual_write_mode}',
       'true'
   )
   WHERE key = 'score' AND user_id = '<user_id>';
   ```

2. **Deploy Migration**
   ```bash
   cd backend
   alembic upgrade head  # Runs migration 028
   ```

3. **Monitor for 7 Days**
   - Watch logs for `[score-delta]` entries
   - Verify both scores are computed successfully
   - Check for performance degradation (target: < 10% increase in compute time)

4. **Success Criteria**
   - Zero errors in score computation
   - All symbols have both v1 and v2 scores
   - Score deltas distribution identified

### Stage 2: Analyze Score Deltas (Week 2)

**Objective**: Understand the impact of confidence weighting.

1. **Query Score Comparison**
   ```sql
   -- Score delta distribution
   SELECT
       symbol,
       score AS v1_score,
       alpha_score_v2 AS v2_score,
       ABS(alpha_score_v2 - score) AS delta,
       confidence_metrics->>'overall_confidence' AS confidence
   FROM alpha_scores
   WHERE scoring_version = 'dual'
       AND time > now() - interval '7 days'
   ORDER BY delta DESC
   LIMIT 100;
   ```

2. **Identify Patterns**
   - Which symbols have largest deltas?
   - Are low-confidence indicators common?
   - Does confidence weighting improve decision quality?

3. **Decision Point**
   - **If results positive**: Proceed to Stage 3
   - **If results concerning**: Investigate anomalies, adjust thresholds, repeat Stage 1

### Stage 3: Gradual Rollout (Weeks 3-5)

**Objective**: Enable confidence-weighted scoring for production decisions.

#### 3a. 10% Rollout (Week 3)
1. **Enable for Test Watchlists**
   ```sql
   -- Enable for specific watchlists
   UPDATE profiles
   SET config = jsonb_set(
       config,
       '{scoring,confidence_weighting,enabled}',
       'true'
   )
   WHERE id IN (
       SELECT id FROM profiles
       WHERE name LIKE '%test%'
       LIMIT 10
   );
   ```

2. **Monitor for 3 Days**
   - Compare trade decisions (v1 vs v2 cohorts)
   - Track win rates, Sharpe ratios
   - Monitor error rates

3. **Success Criteria**
   - Trade quality metrics stable or improved
   - No increase in error rates
   - Confidence metrics within expected ranges

#### 3b. 50% Rollout (Week 4)
1. **Expand to Half of Watchlists**
   ```sql
   -- Enable for 50% of watchlists (deterministic selection)
   UPDATE profiles
   SET config = jsonb_set(
       config,
       '{scoring,confidence_weighting,enabled}',
       'true'
   )
   WHERE (hashtext(id::text)::bigint % 100) < 50;
   ```

2. **Monitor for 5 Days**
   - A/B test results: v1 cohort vs v2 cohort
   - Statistical significance of performance differences
   - User feedback

3. **Success Criteria**
   - v2 cohort performs equal or better than v1
   - No operational incidents
   - Confidence metrics stable

#### 3c. 100% Rollout (Week 5)
1. **Enable for All Watchlists**
   ```sql
   -- Enable globally
   UPDATE configs
   SET value = jsonb_set(
       value,
       '{confidence_weighting,enabled}',
       'true'
   )
   WHERE key = 'score';
   ```

2. **Monitor for 7 Days**
   - Full production monitoring
   - Alert on any anomalies
   - Keep dual-write active for rollback capability

3. **Success Criteria**
   - All metrics stable for 7 days
   - No regression in system performance
   - Positive user feedback

### Stage 4: Disable Dual-Write (Week 6)

**Objective**: Reduce compute overhead after successful rollout.

1. **Disable Dual-Write**
   ```sql
   UPDATE configs
   SET value = jsonb_set(
       value,
       '{confidence_weighting,dual_write_mode}',
       'false'
   )
   WHERE key = 'score';
   ```

2. **Archive v1 Scores**
   ```sql
   -- Optional: Archive old v1 scores after 30 days
   DELETE FROM alpha_scores
   WHERE scoring_version = 'v1'
       AND time < now() - interval '30 days';
   ```

## Rollback Procedures

### Emergency Rollback (< 5 minutes)

If critical issues detected:

```sql
-- Disable confidence weighting immediately
UPDATE configs
SET value = jsonb_set(
    value,
    '{confidence_weighting,enabled}',
    'false'
)
WHERE key = 'score';

-- System automatically uses v1 scores
-- No service restart required
```

### Partial Rollback

Disable for specific watchlists:

```sql
UPDATE profiles
SET config = jsonb_set(
    config,
    '{scoring,confidence_weighting,enabled}',
    'false'
)
WHERE id = '<watchlist_id>';
```

## Monitoring

### Key Metrics

1. **Score Delta Distribution**
   ```sql
   SELECT
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ABS(alpha_score_v2 - score)) AS median_delta,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY ABS(alpha_score_v2 - score)) AS p95_delta,
       AVG(ABS(alpha_score_v2 - score)) AS avg_delta
   FROM alpha_scores
   WHERE scoring_version = 'dual'
       AND time > now() - interval '1 day';
   ```

2. **Confidence Distribution**
   ```sql
   SELECT
       (confidence_metrics->>'overall_confidence')::float AS confidence,
       COUNT(*) AS count
   FROM alpha_scores
   WHERE scoring_version IN ('dual', 'v2')
       AND time > now() - interval '1 day'
   GROUP BY 1
   ORDER BY 1;
   ```

3. **Low-Confidence Indicators**
   ```sql
   SELECT
       (confidence_metrics->>'low_confidence_rules')::int AS low_conf_count,
       COUNT(*) AS occurrences
   FROM alpha_scores
   WHERE scoring_version IN ('dual', 'v2')
       AND time > now() - interval '1 day'
   GROUP BY 1
   ORDER BY 1 DESC;
   ```

### Log Monitoring

Watch for these log patterns:

```bash
# Score deltas
grep "\[score-delta\]" /var/log/scalpyn/celery.log | tail -100

# Confidence issues
grep "low_confidence" /var/log/scalpyn/celery.log | tail -50

# Errors
grep "ERROR.*score" /var/log/scalpyn/celery.log | tail -50
```

## Troubleshooting

### Issue: Large Score Deltas (> 30 points)

**Symptoms**: Many symbols show v1/v2 deltas > 30 points

**Investigation**:
1. Check indicator confidence distribution
2. Verify min_confidence threshold (default: 0.5)
3. Review indicator sources (candle_approx vs gate)

**Resolution**:
- Adjust min_confidence threshold
- Improve indicator collection quality
- Review scoring rules

### Issue: Performance Degradation

**Symptoms**: Score computation taking > 2x normal time

**Investigation**:
1. Profile compute_scores task
2. Check if dual-write causing bottleneck
3. Verify database performance

**Resolution**:
- Disable dual-write if not needed
- Optimize score engine queries
- Add database indexes if needed

### Issue: Confidence Metrics Missing

**Symptoms**: confidence_metrics column is NULL

**Investigation**:
1. Check if use_confidence_weighting=True
2. Verify IndicatorEnvelope structure
3. Review feature flag configuration

**Resolution**:
- Ensure dual_write_mode or enabled is True
- Verify indicator data has confidence field
- Check seed_service DEFAULT_SCORE config

## Success Metrics

### Technical Metrics
- ✅ Dual-write success rate > 99%
- ✅ Performance overhead < 15%
- ✅ Zero data loss or corruption
- ✅ Rollback capability tested and working

### Business Metrics
- ✅ Win rate improvement > 5% (target: 10%)
- ✅ Sharpe ratio increase > 0.1
- ✅ False positive reduction > 15%
- ✅ User satisfaction maintained or improved

## Timeline Summary

| Week | Phase | Activity | Success Criteria |
|------|-------|----------|------------------|
| 1 | Dual-Write | Enable dual-write, collect data | Zero errors, both scores computed |
| 2 | Analysis | Analyze deltas, validate improvements | Positive impact identified |
| 3 | 10% Rollout | Enable for test watchlists | Metrics stable, no regressions |
| 4 | 50% Rollout | Expand to half of watchlists | v2 performs ≥ v1 |
| 5 | 100% Rollout | Full production deployment | All metrics stable 7 days |
| 6 | Cleanup | Disable dual-write, archive v1 | Overhead reduced, system optimized |

**Total Duration**: 6 weeks from dual-write to full rollout

## Contact & Support

- **Engineering Lead**: Check PR discussions
- **Deployment Issues**: Create GitHub issue with [confidence-scoring] tag
- **Emergency Rollback**: Execute SQL commands above, notify team

---

**Document Version**: 1.0
**Last Updated**: 2026-05-01
**Next Review**: After Stage 3c completion
