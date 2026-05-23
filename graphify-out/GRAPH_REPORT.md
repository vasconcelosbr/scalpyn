# Graph Report - .  (2026-05-23)

## Corpus Check
- 20 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 344 nodes · 448 edges · 27 communities (21 shown, 6 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 36 edges (avg confidence: 0.75)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]

## God Nodes (most connected - your core abstractions)
1. `DatasetBuilder` - 16 edges
2. `PredictService` - 16 edges
3. `ModelLoader` - 14 edges
4. `score_futures()` - 14 edges
5. `score_futures() - Dual LONG/SHORT Pipeline Scorer` - 14 edges
6. `_get()` - 13 edges
7. `ModelTrainer` - 12 edges
8. `EvaluationReport` - 10 edges
9. `ML Trainer Job main()` - 9 edges
10. `train_model_pipeline()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `ML Trainer Job main()` --references--> `ML Trainer Requirements`  [INFERRED]
  ml_trainer/job.py → ml_trainer/requirements_trainer.txt
- `main()` --calls--> `build_training_dataframe()`  [INFERRED]
  ml_trainer/job.py → backend/app/ml/feature_extractor.py
- `main()` --calls--> `WinFastTrainer`  [INFERRED]
  ml_trainer/job.py → backend/app/ml/trainer.py
- `_score_liquidity() - Inline Liquidity (pipeline scorer)` --semantically_similar_to--> `score_liquidity() - L1 Liquidity Scorer`  [INFERRED] [semantically similar]
  backend/app/scoring/futures_pipeline_scorer.py → backend/app/scoring/layer_liquidity.py
- `_score_structure_long() - Inline Structure Long` --semantically_similar_to--> `score_structure() - L2 Multi-TF Structure Scorer`  [INFERRED] [semantically similar]
  backend/app/scoring/futures_pipeline_scorer.py → backend/app/scoring/layer_structure.py

## Hyperedges (group relationships)
- **ML Inference Pipeline (WinFastPredictor)** — prediction_service_WinFastPredictor, gcs_model_loader_get_model, feature_extractor_extract_features, feature_extractor_FEATURE_COLUMNS, feature_extractor_ML_EXCLUDED_FIELDS, db_ml_models, db_ml_predictions [EXTRACTED 1.00]
- **WinFast Training Pipeline (trainer.py)** — trainer_WinFastTrainer, feature_extractor_FEATURE_COLUMNS, feature_extractor_ML_EXCLUDED_FIELDS, feature_extractor_train_val_test_split, trainer_calibrate_threshold, concept_nan_preservation, concept_threshold_calibration [EXTRACTED 1.00]
- **ML Leakage Guard System** — concept_ml_leakage_guard, feature_extractor_ML_EXCLUDED_FIELDS, feature_extractor_extract_features, feature_extractor_build_training_dataframe, trainer_WinFastTrainer, prediction_service_WinFastPredictor [EXTRACTED 1.00]
- **Legacy PredictService Stack** — predict_service_PredictService, model_loader_ModelLoader, model_loader_get_model_loader, dataset_builder_DatasetBuilder [INFERRED 0.85]
- **Dataset Preparation Pipeline** — dataset_builder_DatasetBuilder, dataset_builder_TradeSimulation, train_model_train_model_pipeline, train_model_ModelTrainer [EXTRACTED 1.00]
- **Five-Layer Scoring System (L1-L5)** — layer_liquidity_score_liquidity, layer_structure_score_structure, layer_momentum_score_momentum, layer_order_flow_score_order_flow, layer_volatility_score_volatility, futures_pipeline_scorer_score_futures [INFERRED 0.95]
- **All Scoring Layers Share ScoringFuturesConfig** — layer_liquidity_score_liquidity, layer_momentum_score_momentum, layer_order_flow_score_order_flow, layer_structure_score_structure, layer_volatility_score_volatility, scoring_ScoringFuturesConfig_schema [EXTRACTED 1.00]
- **ML Trainer Pipeline (data extract to train to GCS persist to DB register)** — job_decisions_log_query, job_build_training_dataframe, job_WinFastTrainer, job_optuna_study, job_gcs_model_upload, job_ml_models_table, job_mlflow_tracking [EXTRACTED 1.00]
- **Gate.io Adapter Used by Async Fetch Layers (L1, L5)** — layer_liquidity_fetch_and_score, layer_order_flow_fetch_order_flow_data, gate_adapter [EXTRACTED 1.00]
- **Inline Pipeline Scorer vs Modular Layer Scorers (architectural duality)** — futures_pipeline_scorer_score_futures, futures_pipeline_scorer_score_liquidity_inline, futures_pipeline_scorer_score_volatility_inline, layer_liquidity_score_liquidity, layer_volatility_score_volatility [INFERRED 0.75]
- **taker_ratio / buy_pressure Canonical Unification (issue #82)** — layer_order_flow_safe_taker_ratio_ref, order_flow_service_safe_taker_ratio, layer_order_flow_fetch_order_flow_data, layer_order_flow_score_order_flow [EXTRACTED 1.00]
- **Spot Engine Sell Pipeline (5 Layers: L1 MeanReversion, L2 Momentum, L3 AI, L4 Trailing, L5 KillSwitch)** — spot_engine_config_MeanReversionConfig, spot_engine_config_MomentumExitConfig, spot_engine_config_AIConsultationConfig, spot_engine_config_TrailingConfig, spot_engine_config_KillSwitchConfig, spot_engine_config_SellFlowConfig [EXTRACTED 1.00]
- **Futures Leverage Guard Trio (Funding, OI, Liquidation)** — futures_engine_config_FundingGuardConfig, futures_engine_config_OIGuardConfig, futures_engine_config_LiquidationGuardConfig, futures_engine_config_LeverageChecksConfig [EXTRACTED 1.00]
- **Pipeline Funnel Table Set (Watchlist, Asset Snapshot, Rejection)** — model_PipelineWatchlist, model_PipelineWatchlistAsset, model_PipelineWatchlistRejection, concept_pipeline_funnel [EXTRACTED 1.00]
- **Shadow Trade Lifecycle (Schema + Model + Service + Monitor)** — model_ShadowTrade, shadow_trade_schema_ShadowTradeRead, shadow_trade_schema_ShadowTradeDetail, service_shadow_trade, task_shadow_trade_monitor, concept_shadow_portfolio [EXTRACTED 1.00]
- **3-Layer Config Architecture (Filters/Score/Blocks)** — config_schemas_FiltersConfig, config_schemas_ScoreConfig, config_schemas_BlocksConfig, concept_config_3layers [EXTRACTED 1.00]
- **Shadow Market Context ML Fields (BTC price, BTC 1h change, funding rate, concurrent signals)** — model_ShadowTrade, service_shadow_trade, task_shadow_trade_monitor, concept_market_context_ml [EXTRACTED 1.00]
- **Dual-Scheduler Readiness Handshake (Microstructure + Structural)** — service_pipeline_scheduler, dashboard_SystemStatusResponse, concept_pipeline_funnel [EXTRACTED 0.95]

## Communities (27 total, 6 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (25): Dataset Builder — Extract and prepare training data from trade_simulations., EvaluationReport, generate_evaluation_report(), Evaluation Report — Generate comprehensive model evaluation metrics., Generate comprehensive evaluation reports for trained models., Analyze win rate by probability bucket., Initialize evaluation report generator.          Args:             model_path, Analyze performance by trade direction. (+17 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (32): Entry Gates (_entry_long_blocked_cfg / _entry_short_blocked_cfg), score_futures() - Dual LONG/SHORT Pipeline Scorer, _score_liquidity() - Inline Liquidity (pipeline scorer), _score_momentum_long() - Inline Momentum Long, _score_momentum_short() - Inline Momentum Short, _score_order_flow_long() - Inline Order Flow Long, _score_order_flow_short() - Inline Order Flow Short, _score_structure_long() - Inline Structure Long (+24 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (31): GCS Model Cache (TTL=300s singleton), ML Leakage Guard (ML_EXCLUDED_FIELDS), NaN Preservation Strategy (XGBoost missing=nan), Decision Threshold Calibration (F1-max / max_precision_at_recall), WIN_FAST Label (pnl_pct > MIN_WIN_PNL_PCT), DatasetBuilder, TradeSimulation (model), decisions_log (DB table / metrics JSONB) (+23 more)

### Community 3 - "Community 3"
Cohesion: 0.11
Nodes (15): get_predict_service(), PredictService, Prediction Service — Real-time inference for L3 ranking and blocking., Prediction service for real-time trade outcome prediction., Predict probability for a single direction.          Args:             featur, Predict probabilities for both LONG and SHORT directions.          Args:, Predict best direction and probability.          For SPOT: only predict LONG, Initialize prediction service.          Args:             model_path: Path to (+7 more)

### Community 4 - "Community 4"
Cohesion: 0.13
Nodes (23): Shadow Trades API Router, Market Context Enrichment (ML Fase 6), Spot Sell Pipeline (5 Layers), Shadow Portfolio (Simulated Trades), ShadowTrade Model, Shadow Trade Service, ShadowTradeDetail Schema, ShadowTradeRead Schema (+15 more)

### Community 5 - "Community 5"
Cohesion: 0.12
Nodes (13): ModelTrainer, Model Training — Train XGBoost model for trade outcome prediction., Evaluate model performance.          Args:             X: Features, Train and manage XGBoost models for trade outcome prediction., Get feature importance scores.          Args:             top_n: Number of to, Analyze win rate by probability bucket.          Args:             X: Feature, Initialize model trainer.          Args:             model_dir: Directory to, Save trained model to disk.          Args:             model_name: Name of th (+5 more)

### Community 6 - "Community 6"
Cohesion: 0.13
Nodes (14): build_training_dataframe(), extract_features(), Feature Extractor — Extract and engineer features from decisions_log.metrics JSO, Extract and engineer features from a metrics dict.      Args:         metrics, Build training DataFrame from decisions_log records.      Args:         recor, Time-based split — no shuffle to avoid look-ahead bias.      Args:         df, train_val_test_split(), _calibrate_threshold() (+6 more)

### Community 7 - "Community 7"
Cohesion: 0.12
Nodes (19): Pipeline Watchlists API Router, Config Architecture (3 Execution Layers), Institutional Pipeline Funnel (POOL/L1/L2/L3), BlocksConfig (Layer 3), FilterRule, FiltersConfig (Layer 1), HardBlock, ScoreConfig (Layer 2) (+11 more)

### Community 8 - "Community 8"
Cohesion: 0.14
Nodes (10): DatasetBuilder, Extract features from simulations and create training dataset.          Args:, Build ML training datasets from trade simulation results., Create engineered features from base features.          Args:             df:, Encode direction as numeric feature.          Args:             df: DataFrame, Get list of feature columns for training (excluding metadata).          Args:, Complete data preparation pipeline.          Args:             simulations: L, Split data using time-based approach (no shuffle).          Args: (+2 more)

### Community 9 - "Community 9"
Cohesion: 0.12
Nodes (17): EmergencyConfig, ExecutionFuturesConfig, FundingDrainConfig, FundingGuardConfig, FuturesEngineConfig, LeverageCapsConfig, LeverageChecksConfig, LiquidationGuardConfig (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.32
Nodes (15): _entry_long_blocked_cfg(), _entry_short_blocked_cfg(), _get(), _has_sufficient_data(), Futures Pipeline Scorer — dual independent LONG/SHORT scoring.  Scoring layers, Compute dual LONG/SHORT futures scores for a single asset.      Returns None f, score_futures(), _score_liquidity() (+7 more)

### Community 11 - "Community 11"
Cohesion: 0.21
Nodes (13): _classify_structure(), _detect_liquidity_sweeps(), _find_swing_points(), L2Result, L2 — Market Structure Score (0-20).  Multi-timeframe structure analysis: HH/HL, Analyse a single timeframe DataFrame.     Returns (trend_direction, key_levels,, Multi-timeframe structure scoring.      Args:         dfs:             dict o, Find swing highs and lows using a simple pivot detection. (+5 more)

### Community 12 - "Community 12"
Cohesion: 0.18
Nodes (8): GCSModelLoader, get_model(), invalidate_model_cache(), Singleton que carrega e cacheia o modelo XGBoost do GCS.      Comportamento:, Retorna modelo — carrega do GCS se cache expirado., Download e deserialização do modelo do GCS., Força reload no próximo request., Função de conveniência — use em qualquer lugar.

### Community 13 - "Community 13"
Cohesion: 0.27
Nodes (10): WinFastTrainer (instantiated in job), build_training_dataframe (called in job), decisions_log DB Query (DISTINCT ON dedup), GCS Model Upload (win_fast_latest.pkl), ML Trainer Job main(), ml_models Table (Cloud SQL persistence), MLflow Tracking URI (scalpyn-mlflow-ui Cloud Run), Optuna Hyperparameter Tuning (in-memory study) (+2 more)

### Community 14 - "Community 14"
Cohesion: 0.36
Nodes (7): _calc_rsi(), _detect_divergence(), L3Result, L3 — Momentum Score (0-20).  RSI, MACD, EMA alignment, VWAP position, and dive, Detect regular bullish or bearish divergence over lookback candles.     Returns, Calculate L3 Momentum score.      Args:         df:              OHLCV DataFr, score_momentum()

### Community 15 - "Community 15"
Cohesion: 0.38
Nodes (6): fetch_and_score(), L1Result, L1 — Liquidity Score (0-20).  Evaluates trading viability based on volume, rel, Fetch liquidity data from Gate.io and score it.     adapter: GateAdapter instan, Calculate the L1 Liquidity score.      Args:         volume_24h_usdt:  24h tr, score_liquidity()

### Community 16 - "Community 16"
Cohesion: 0.33
Nodes (4): Preditor stateless — compatível com Cloud Run.     Modelo vive no GCS, carregad, Busca model_id e threshold do modelo ativo., Prediz probabilidade WIN_FAST para um sinal L3.          Returns:, WinFastPredictor

### Community 17 - "Community 17"
Cohesion: 0.33
Nodes (6): fetch_order_flow_data(), L5Result, L5 — Order Flow Score (0-20).  Taker buy/sell ratio, funding rate, open intere, Fetch all order flow data points from Gate.io for L5 scoring.      Uses real f, Calculate L5 Order Flow score., score_order_flow()

### Community 18 - "Community 18"
Cohesion: 0.50
Nodes (4): L4Result, L4 — Volatility Score (0-20).  ATR, Bollinger Bands, squeeze detection, compre, Calculate L4 Volatility score., score_volatility()

## Knowledge Gaps
- **62 isolated node(s):** `TradeSimulation (model)`, `generate_evaluation_report (function)`, `train_val_test_split (function)`, `invalidate_model_cache (function)`, `load_model (function)` (+57 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DatasetBuilder` connect `Community 8` to `Community 0`, `Community 3`, `Community 5`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `PredictService` connect `Community 3` to `Community 0`, `Community 8`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Why does `ModelLoader` connect `Community 0` to `Community 3`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `DatasetBuilder` (e.g. with `EvaluationReport` and `PredictService`) actually correct?**
  _`DatasetBuilder` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `PredictService` (e.g. with `ModelLoader` and `DatasetBuilder`) actually correct?**
  _`PredictService` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ModelLoader` (e.g. with `EvaluationReport` and `PredictService`) actually correct?**
  _`ModelLoader` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `score_futures() - Dual LONG/SHORT Pipeline Scorer` (e.g. with `score_liquidity() - L1 Liquidity Scorer` and `score_volatility() - L4 Volatility Scorer`) actually correct?**
  _`score_futures() - Dual LONG/SHORT Pipeline Scorer` has 5 INFERRED edges - model-reasoned connections that need verification._