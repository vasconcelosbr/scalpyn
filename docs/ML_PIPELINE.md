# XGBoost ML Pipeline — Trade Outcome Prediction

## Dataset Validity Gate — `ml_dataset_valid_from`

**Set on:** 2026-06-11 (deployed with B1 fix — features now captured live at L1 promotion).

**Why this exists:**

Before the B1 fix, `create_l1_spectrum_shadows` copied `analysis_snapshot` from the L1 asset,
which is always empty at the L1 stage (indicators are only computed at L2/L3). This meant every
`L1_SPECTRUM` shadow had `features_snapshot = {}` — 37 features all absent. Training on those
records would produce a model with only NaNs as X and no learnable signal.

**The fix (B1):** `create_l1_spectrum_shadows` now calls `get_merged_indicators` to fetch live
indicator values at the moment of L1 promotion (T0-safe by construction). Coverage metadata is
recorded in `features_snapshot` itself: `_features_captured_at`, `_features_coverage`,
`_oldest_indicator_age_s`.

**`ml_dataset_valid_from` rule:**
- Stored in `config_profiles` where `config_type='ml'`, key `ml_dataset_valid_from` (ISO string).
- The trainer filters: `AND created_at >= ml_dataset_valid_from` (applied only when set).
- This field **only moves forward** — never set it to a past value. Moving it back would
  re-introduce pre-fix empty-feature records into training.
- To set after deploy:
  ```sql
  -- backend/sql/set_ml_dataset_valid_from.sql
  UPDATE config_profiles
  SET config_json = config_json || jsonb_build_object(
      'ml_dataset_valid_from',
      to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
  )
  WHERE config_type = 'ml' AND is_active = true;
  ```
- Verify: `SELECT config_json->>'ml_dataset_valid_from' FROM config_profiles WHERE config_type='ml';`

**Historical shadows (pre-fix):** remain in the DB, untouched. They are filtered out by
`ml_dataset_valid_from`, not deleted. Zero retroactive updates (additive-only invariant).

**Post-deploy validation (run 24h after B1 deploy):**
```sql
SELECT COUNT(*) AS novos,
       COUNT(*) FILTER (WHERE features_snapshot = '{}'::jsonb
                          OR features_snapshot IS NULL) AS vazios,
       AVG((features_snapshot->>'_features_coverage')::float) AS cobertura_media
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND created_at >= '<ml_dataset_valid_from>';
-- Expected: vazios=0; cobertura_media >= 0.8 (below 0.8 = investigate bootstrap timing)
```

---

## Known Data Quirks (read before adding features)

### MAE > 0 in pre-2026-06-10 records (B2 fix)

Records created before commit `FIX_B123` (2026-06-10) may have `mae_pct > 0` in `shadow_trades`.
This is semantically wrong: MAE (Maximum Adverse Excursion) must be ≤ 0 for long trades.
The positive value occurs when `min_price_post_entry > entry_price` (gap-up entry where price
never dipped below entry). The correct interpretation is `mae = 0` (no adverse move), not a gain.

**Impact on training:** If MAE/MFE are added as features, the trainer loader must apply a
runtime clamp:
```python
mae_pct = min(0.0, record["mae_pct"])   # pre-fix records: positive → 0
mfe_pct = max(0.0, record["mfe_pct"])   # pre-fix records: negative → 0
```
New records (post-deploy) will have the correct clamped values from the monitor.

### Source = 'L3' is Stream B (policy validation), not ML training

`shadow_trades.source = 'L3'` means the shadow was created AFTER the full L1→L2→L3 filter chain
passed. **Do NOT use L3 records as ML training data.** They represent what the policy decided to
execute, not what the market offered. Two embedded biases:
1. **Survivorship bias:** the model never sees signals the L3 score gate rejected.
2. **Symbol cooldown bias:** the per-source constraint means only the first ALLOW for a symbol
   (while no L3 shadow is running) creates a shadow. ~14% of L3-ALLOWs become shadows.

Stream B (source='L3') continues in production for **policy validation only** — measuring
whether the L3 filter is selecting good signals.

### Stream A: L1_SPECTRUM (exclusive ML training source)

**Architectural decision (2026-06-10):** The ML model trains exclusively on
`source='L1_SPECTRUM'` shadows. These are captured at L1 promotion — before any quality
filter, score gate, or block rule — giving an unbiased sample of what the market offered.

**Invariant of purity:** The code path from L1 promotion to L1_SPECTRUM shadow creation
contains zero quality conditionals. Only structural discards are permitted:
- Deterministic hash sampling (reproducible, quality-agnostic)
- Per-source reentry policy (one RUNNING per symbol per stream)
- Hard rate ceiling (max_per_hour config)

Each discard is recorded in `shadow_capture_skips` with its reason for auditability.

**Constraint change (migration 073):** `ux_shadow_running_user_source` on
`(user_id, symbol, source)` WHERE status='RUNNING' replaced the old
`ux_shadow_running_user_symbol` on `(user_id, symbol)`. L3 and L1_SPECTRUM shadows are
now independent per symbol — an L3 shadow running for BTC_USDT does NOT block an
L1_SPECTRUM shadow for BTC_USDT.

**Activation timeline:** Dataset clock starts at `shadow_capture_l1_enabled=true` flip.
The first ML retrain on L1_SPECTRUM data can happen once ~10K records are accumulated
(estimate: 2-4 weeks at 10% sample rate = ~749 shadows/day).

| Source | Purpose | `config_type='ml'` filter | Migration |
|--------|---------|--------------------------|-----------|
| `L1_SPECTRUM` | ML training (unbiased) | `ML_SOURCE_FILTER=L1_SPECTRUM` | 073+ |
| `L3` | Policy validation (Stream B) | never train | 060+ |
| `L3_REJECTED` | Negative examples (deprecated) | never train | 060+ |

## Overview

The SCALPYN XGBoost ML Pipeline predicts the probability of Take Profit (TP) being hit before Stop Loss (SL) for trading opportunities. The system uses labeled data from historical simulations to train a binary classification model that enhances L3 ranking with ML-driven probabilities.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     ML PIPELINE ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐ │
│  │ Simulation   │ -->  │  Dataset     │ -->  │   XGBoost    │ │
│  │  Engine      │      │  Builder     │      │   Training   │ │
│  └──────────────┘      └──────────────┘      └──────────────┘ │
│         │                      │                      │          │
│         v                      v                      v          │
│  trade_simulations      Feature Matrix          model.pkl       │
│    (DB Table)          (pandas DataFrame)     (Trained Model)   │
│                                                       │          │
│                                                       v          │
│                                              ┌──────────────┐   │
│                                              │ Prediction   │   │
│                                              │  Service     │   │
│                                              └──────────────┘   │
│                                                       │          │
│                                                       v          │
│                                              ┌──────────────┐   │
│                                              │ L3 Ranking   │   │
│                                              │ Integration  │   │
│                                              └──────────────┘   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Data Source: `trade_simulations` Table

**Schema:**
- `symbol`: Asset symbol (e.g., BTC_USDT)
- `timestamp_entry`: Entry timestamp
- `entry_price`: Entry price
- `result`: Trade outcome (WIN | LOSS | TIMEOUT)
- `direction`: Trade direction (LONG | SHORT | SPOT)
- `features_snapshot`: JSONB — indicator values at decision time
- `config_snapshot`: JSONB — config used for simulation
- `is_simulated`: Boolean flag

**Label Definition:**
- `WIN` → 1 (TP hit before SL)
- `LOSS` → 0 (SL hit before TP)
- `TIMEOUT` → 0 (Neither hit within timeout)

### 2. Dataset Builder (`backend/app/ml/dataset_builder.py`)

**Key Features:**

#### Feature Extraction
Extracts from `features_snapshot`:

**Core Features:**
- `taker_ratio`
- `volume_delta`
- `rsi`
- `macd_histogram`
- `adx`
- `spread_pct`
- `volume_spike`

**Trend Features:**
- `ema5`, `ema9`, `ema21`, `ema50`, `ema200`
- `ema9_gt_ema21` (boolean)
- `ema50_gt_ema200` (boolean)

**Liquidity Features:**
- `volume_24h_usdt`
- `orderbook_depth_usdt`

**Microstructure Features:**
- `taker_buy_volume`
- `taker_sell_volume`
- `vwap_distance_pct`

#### Feature Engineering
Creates derived features:
- `flow_strength = taker_ratio * volume_delta`
- `trend_alignment = ema9_gt_ema21 + ema50_gt_ema200`
- `momentum_strength = macd_histogram * adx`
- `delta_normalized = volume_delta / volume_24h_usdt`
- `ema_distance_pct = (ema9 - ema21) / ema21 * 100`

#### Direction Encoding
- `LONG` → 1
- `SHORT` → -1
- `SPOT` → 0

#### Time-Based Split
- **CRITICAL**: Uses time-ordered split (NO random shuffle)
- Training: older 80% of data
- Validation: recent 20% of data
- Prevents data leakage from future to past

### 3. Model Trainer (`backend/app/ml/train_model.py`)

**XGBoost Configuration:**
```python
{
    "objective": "binary:logistic",
    "eval_metric": ["auc", "logloss"],
    "max_depth": 5,
    "learning_rate": 0.08,
    "n_estimators": 300,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "scale_pos_weight": auto,  # Calculated from class imbalance
    "tree_method": "hist",
}
```

**Training Process:**
1. Load simulations from DB
2. Extract and engineer features
3. Time-based train/val split
4. Calculate `scale_pos_weight` for class imbalance
5. Train with early stopping (patience=20)
6. Save model + metadata to `/tmp/scalpyn_models/model.pkl`

**Outputs:**
- AUC (ROC-AUC score)
- Log Loss
- Precision / Recall / F1
- Feature importance
- Win rate by probability bucket

### 4. Model Loader (`backend/app/ml/model_loader.py`)

**Responsibilities:**
- Load trained model from disk
- Global singleton pattern for efficiency
- Validate model metadata
- Provide prediction interface

**Model File Structure:**
```python
{
    "model": XGBClassifier,
    "feature_columns": List[str],
    "metadata": {
        "trained_at": "ISO timestamp",
        "params": dict,
        "metrics": dict,
    }
}
```

### 5. Prediction Service (`backend/app/ml/predict_service.py`)

**Key Methods:**

#### `predict_best_direction(features, profile_type)`
For FUTURES:
- Predicts prob_long with direction=1
- Predicts prob_short with direction=-1
- Returns best direction and probability

For SPOT:
- Only predicts LONG direction
- Returns probability for SPOT trades

#### `calculate_final_score(score, probability)`
```python
final_score = (score / 100) * probability
```

#### `should_block(probability, threshold)`
Returns `True` if `probability < threshold`

### 6. L3 Integration (`backend/app/api/watchlists.py`)

**Integration Point:**
- Endpoint: `GET /api/watchlists/{id}/assets`
- Triggers on: `wl.level == "L3"`

**Flow:**
1. Compute alpha scores (base scoring)
2. If ML enabled and L3:
   - Load ai-settings config
   - Get prediction service
   - For each asset:
     - Predict direction & probability
     - Calculate final_score
     - Check blocking threshold
3. Add ML fields to response:
   - `ml_probability`
   - `ml_direction`
   - `ml_base_score`
   - `ml_final_score`
   - `blocked_by_ml`
4. Sort by `ml_final_score` (if available)

**Sorting Logic:**
- FUTURES: ML final_score > confidence_score > score_long/short
- SPOT: ML final_score > alpha_score

### 7. Evaluation (`backend/app/ml/evaluation_report.py`)

**Metrics:**
- AUC-ROC
- Log Loss
- Accuracy, Precision, Recall, F1
- Confusion Matrix
- Feature Importance (top 20)
- Win Rate by Probability Bucket (10 buckets)
- Direction Breakdown (LONG/SHORT/SPOT)

## API Endpoints

### Training
```bash
POST /api/ml/train
{
  "min_date": "2024-01-01",
  "max_date": "2024-12-31",
  "model_name": "model.pkl",
  "params": {
    "max_depth": 5,
    "learning_rate": 0.08
  }
}
```

### Evaluation
```bash
POST /api/ml/evaluate
{
  "model_path": "/tmp/scalpyn_models/model.pkl",
  "min_date": "2024-01-01",
  "max_date": "2024-12-31",
  "save_report": true
}
```

### Prediction
```bash
POST /api/ml/predict
{
  "features": {
    "rsi": 35,
    "adx": 30,
    "volume_spike": 2.5,
    ...
  },
  "profile_type": "FUTURES"
}
```

### Status
```bash
GET /api/ml/status
```

### Reload Model
```bash
POST /api/ml/reload
{
  "model_path": "/tmp/scalpyn_models/model.pkl"
}
```

## Configuration

### AI Settings (`/api/config/ai-settings`)

```json
{
  "ml_enabled": true,
  "model_path": "/tmp/scalpyn_models/model.pkl",
  "ai_block_threshold": 0.5,
  "use_ml_ranking": true,
  "fallback_probability": 1.0,
  "auto_retrain_enabled": false,
  "retrain_interval_days": 7,
  "min_simulations_for_training": 1000
}
```

**Parameters:**
- `ml_enabled`: Master switch for ML system
- `model_path`: Path to trained model file
- `ai_block_threshold`: Probability threshold for blocking (0-1)
- `use_ml_ranking`: Enable ML-enhanced L3 ranking
- `fallback_probability`: Probability when model unavailable
- `auto_retrain_enabled`: Enable automatic retraining
- `retrain_interval_days`: Days between retraining
- `min_simulations_for_training`: Minimum dataset size

## Workflow

### Initial Setup

1. **Generate Training Data:**
   - Run simulation engine on historical data
   - Populate `trade_simulations` table
   - Aim for 1000+ simulations minimum

2. **Train Model:**
   ```bash
   curl -X POST /api/ml/train \
     -H "Content-Type: application/json" \
     -d '{"min_date": "2024-01-01", "max_date": "2024-12-31"}'
   ```

3. **Evaluate Model:**
   ```bash
   curl -X POST /api/ml/evaluate \
     -H "Content-Type: application/json" \
     -d '{"model_path": "/tmp/scalpyn_models/model.pkl"}'
   ```

4. **Enable ML in Config:**
   ```bash
   curl -X PUT /api/config/ai-settings \
     -H "Content-Type: application/json" \
     -d '{"ml_enabled": true, "use_ml_ranking": true}'
   ```

5. **Verify Status:**
   ```bash
   curl /api/ml/status
   ```

### Production Use

1. **Model serves predictions on L3 watchlist access**
2. **Final scores combine base score × probability**
3. **Assets sorted by ML final_score**
4. **Trades blocked if probability < threshold**

### Retraining

**Manual:**
```bash
curl -X POST /api/ml/train \
  -d '{"model_name": "model_v2.pkl"}'

curl -X POST /api/ml/reload \
  -d '{"model_path": "/tmp/scalpyn_models/model_v2.pkl"}'
```

**Automated:**
- Set `auto_retrain_enabled: true`
- Configure `retrain_interval_days`
- Model retrains on schedule using latest simulations

## Inference Logic

### L3 Asset Ranking

For each asset in L3:

```python
# 1. Extract features from indicators
features = {
    "rsi": 35,
    "adx": 30,
    "macd_histogram": 0.05,
    "taker_ratio": 0.65,
    "volume_spike": 2.3,
    ...
}

# 2. Get base alpha score (0-100)
base_score = compute_alpha_score(features)  # e.g., 75

# 3. Predict probability
if profile_type == "FUTURES":
    prob_long = predict(features + direction=1)
    prob_short = predict(features + direction=-1)

    if prob_long > prob_short:
        direction = "LONG"
        probability = prob_long
    else:
        direction = "SHORT"
        probability = prob_short
else:  # SPOT
    direction = "LONG"
    probability = predict(features + direction=0)

# 4. Calculate final score
final_score = (base_score / 100) * probability  # 0.75 * 0.72 = 0.54

# 5. Check blocking
if probability < ai_block_threshold:
    block_trade()

# 6. Return enriched data
{
    "symbol": "BTC_USDT",
    "alpha_score": 75,
    "ml_probability": 0.72,
    "ml_direction": "LONG",
    "ml_final_score": 54.0,
    "blocked_by_ml": False
}
```

## Fallback Behavior

If model fails or is unavailable:
- Log warning
- Return `probability = 1.0` (fallback)
- Use base score only
- System continues functioning

**No hard failures — graceful degradation**

## Critical Rules

### Data Integrity
- ✅ **NEVER use future data** — strict time ordering
- ✅ **ALWAYS use simulation output** — no synthetic data
- ✅ **KEEP time order** — no random shuffles in train/val split
- ✅ **ENSURE reproducibility** — fixed random seed (42)

### Model Quality
- Minimum AUC: 0.65 for deployment
- Monitor precision/recall balance
- Track win rate by probability bucket
- Retrain when AUC drops below threshold

### Feature Requirements
All features must be:
- Available at decision time (no lookahead)
- Computed from indicators (no raw OHLCV)
- Normalized and bounded

## Monitoring

### Key Metrics
- **AUC**: Model discrimination (target: >0.65)
- **Calibration**: Win rate by probability bucket
- **Coverage**: % of L3 assets with predictions
- **Latency**: Prediction time per asset
- **Fallback Rate**: % using fallback probability

### Health Checks
```bash
# Check if model loaded
GET /api/ml/status

# Verify prediction quality
POST /api/ml/evaluate

# Monitor API logs
grep "\[ML\]" /var/log/scalpyn/api.log
```

## Troubleshooting

### Model Not Loading
```
[ML] Model not loaded, skipping ML predictions
```
**Solution:**
- Verify model file exists
- Check file permissions
- Reload model: `POST /api/ml/reload`

### Insufficient Training Data
```
ValueError: Insufficient training data: 87 simulations
```
**Solution:**
- Run more simulations
- Target 1000+ minimum
- Check date range filters

### Prediction Errors
```
[ML] Prediction failed for BTC_USDT: KeyError: 'rsi'
```
**Solution:**
- Ensure indicators computed
- Check feature_columns match training
- Verify no missing critical indicators

## Future Enhancements

### Planned Features
- [ ] SHAP explanations for predictions
- [ ] Feature drift detection
- [ ] Online learning / incremental updates
- [ ] Multi-model ensemble
- [ ] Confidence intervals
- [ ] A/B testing framework

### Model Improvements
- [ ] Add limit order book features
- [ ] Include market regime detection
- [ ] Time-of-day features
- [ ] Volatility clustering
- [ ] Cross-asset correlations

## References

- XGBoost Documentation: https://xgboost.readthedocs.io/
- Simulation Engine: `docs/SIMULATION_ENGINE.md`
- Gate.io API: `docs/api-integration/gate-io-v4-mapping.md`
- Feature Engine: `backend/app/services/feature_engine.py`
- Score Engine: `backend/app/services/score_engine.py`

---

## Forward Scoring — Accumulating Genuine Out-of-Sample Predictions

**Added:** 2026-06-11 | **Status:** Active after smoke train

### Purpose

Each day of forward scoring accumulates predictions that are genuinely
out-of-sample: the score is written at shadow creation time (T0), the
outcome closes independently hours or days later. When scored shadows close,
the AUC computed on those predictions is the evidence that will gate future
ML activation — it cannot be manufactured retroactively.

This means we want forward scoring running NOW, even with a weak smoke-train
model, because what matters is:
1. The scoring **plumbing is working** (feature extraction → inference → write)
2. The **history exists** from the earliest possible date

A model retrain replaces the active `ml_models` row but each prediction row
retains its `model_id`, keeping version histories distinct. Future AUC
analysis can filter by `model_id` to isolate each model's out-of-sample
performance.

### Architecture

```
L1_SPECTRUM shadow created (shadow_trade_service.py)
    │
    └─► safe_score_shadow_trade(shadow_trade_id, features_snapshot, symbol)
            │
            ├─ check ml_forward_scoring_enabled (config_profiles type='ml')
            ├─ load model from ml_models.model_blob (GCSModelLoader singleton)
            ├─ extract_features(features_snapshot)          ← T0-safe
            ├─ model.predict_proba(X)
            └─ INSERT ml_predictions (shadow_trade_id, model_id, probability)
                        │
                        └─ AUDIT LOG ONLY — zero reads from this table
                           in any decision path
```

### Isolation Invariants

The `ml_predictions` table is **write-only** from any decision-making
perspective. Verified by grep: no query reads `ml_predictions` to determine
buy/sell/approve/reject. The only reads are:

- `admin_diagnostics.py` — `SELECT COUNT(*)` for health metrics
- `watchlists.py` — display-only, never a guard condition

`ML_GATE_ENABLED` controls whether the ML probability influences L3 ordering.
Forward scoring is independent of this flag and runs regardless.

### Control Flag

```sql
-- Enable forward scoring after smoke train passes
UPDATE config_profiles
SET config_json = config_json || '{"ml_forward_scoring_enabled": true}'::jsonb
WHERE config_type = 'ml' AND is_active = true;

-- Disable
UPDATE config_profiles
SET config_json = config_json || '{"ml_forward_scoring_enabled": false}'::jsonb
WHERE config_type = 'ml' AND is_active = true;
```

### Validation Queries

After several hours of forward scoring enabled:
```sql
-- Count predictions written in last 24h
SELECT COUNT(*), MIN(scored_at), MAX(scored_at),
       ROUND(AVG(win_fast_probability)::numeric, 4) AS avg_prob,
       ROUND(STDDEV(win_fast_probability)::numeric, 4) AS stddev_prob
FROM ml_predictions
WHERE scored_at >= NOW() - INTERVAL '24 hours'
  AND shadow_trade_id IS NOT NULL;
-- Expected: >0 rows; avg_prob NOT ~same value (non-degenerate distribution)

-- Distribution check (degenerate = bug)
SELECT width_bucket(win_fast_probability, 0, 1, 10) AS bucket,
       COUNT(*) AS n
FROM ml_predictions
WHERE shadow_trade_id IS NOT NULL
GROUP BY 1 ORDER BY 1;

-- Forward AUC (once enough shadows have closed)
SELECT AVG(CASE WHEN st.outcome = 'TP_HIT' AND
                     st.holding_seconds <= 10800 THEN 1.0 ELSE 0.0 END) AS win_rate,
       COUNT(*) AS n_closed,
       COUNT(*) FILTER (WHERE mp.win_fast_probability >= mp.threshold_used) AS n_approved
FROM ml_predictions mp
JOIN shadow_trades st ON st.id = mp.shadow_trade_id
WHERE mp.shadow_trade_id IS NOT NULL
  AND st.outcome IS NOT NULL;
```

### Gate for ML_GATE_ENABLED Activation

The future gate requires ALL of the following on forward-scored shadows:
- `n_closed >= 500`
- Win rate between 15% and 85% (balanced classes)
- Leakage audit clean (all features confirmed T0-safe)
- Forward AUC with 95% bootstrap CI excluding 0.50

Until then: `ML_GATE_ENABLED=false`. Forward scoring accumulates evidence
without influencing any trade decision.

---

**Version:** 2.0.0
**Last Updated:** 2026-06-11
**Status:** Production Ready
