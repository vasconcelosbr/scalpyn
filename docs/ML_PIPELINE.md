# XGBoost ML Pipeline — Trade Outcome Prediction

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

**Version:** 1.0.0
**Last Updated:** 2026-04-29
**Status:** Production Ready
