# Graph Report - C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\backend\app\ml  (2026-06-14)

## Corpus Check
- Corpus is ~12,150 words - fits in a single context window. You may not need a graph.

## Summary
- 181 nodes · 243 edges · 11 communities (10 shown, 1 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 7 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Feature Extraction & Leakage Guard|Feature Extraction & Leakage Guard]]
- [[_COMMUNITY_ML Training Pipeline|ML Training Pipeline]]
- [[_COMMUNITY_Prediction & Forward Scoring|Prediction & Forward Scoring]]
- [[_COMMUNITY_MacroMarket Features|Macro/Market Features]]
- [[_COMMUNITY_Dataset Builder|Dataset Builder]]
- [[_COMMUNITY_Model Loader & Storage|Model Loader & Storage]]
- [[_COMMUNITY_Evaluation & Reporting|Evaluation & Reporting]]
- [[_COMMUNITY_Score Field Routing|Score Field Routing]]
- [[_COMMUNITY_Label & Fee Logic|Label & Fee Logic]]
- [[_COMMUNITY_Temporal Split Logic|Temporal Split Logic]]
- [[_COMMUNITY_SHAP & Feature Importance|SHAP & Feature Importance]]

## God Nodes (most connected - your core abstractions)
1. `PredictService` - 16 edges
2. `DatasetBuilder` - 14 edges
3. `ModelLoader` - 12 edges
4. `ModelTrainer` - 12 edges
5. `extract_macro_features()` - 8 edges
6. `train_model_pipeline()` - 8 edges
7. `generate_evaluation_report()` - 7 edges
8. `_unwrap()` - 7 edges
9. `GCSModelLoader` - 6 edges
10. `_safe_float()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `PredictService` --uses--> `DatasetBuilder`  [INFERRED]
  predict_service.py → dataset_builder.py
- `ModelTrainer` --uses--> `DatasetBuilder`  [INFERRED]
  train_model.py → dataset_builder.py
- `train_model_pipeline()` --calls--> `DatasetBuilder`  [INFERRED]
  train_model.py → dataset_builder.py
- `_extract_crypto_global()` --calls--> `_get()`  [INFERRED]
  macro_features.py → macro_client.py
- `PredictService` --uses--> `ModelLoader`  [INFERRED]
  predict_service.py → model_loader.py

## Communities (11 total, 1 thin omitted)

### Community 0 - "Feature Extraction & Leakage Guard"
Cohesion: 0.10
Nodes (16): get_predict_service(), PredictService, Prediction Service — Real-time inference for L3 ranking and blocking., Prediction service for real-time trade outcome prediction., Predict probability for a single direction.          Args:             featur, Predict probabilities for both LONG and SHORT directions.          Args:, Predict best direction and probability.          For SPOT: only predict LONG, Initialize prediction service.          Args:             model_path: Path to (+8 more)

### Community 1 - "ML Training Pipeline"
Cohesion: 0.11
Nodes (19): build_training_dataframe(), extract_features(), filter_trainable_features(), Feature Extractor — Extract and engineer features from decisions_log.metrics JSO, Extract and engineer features from a metrics dict.      Args:         metrics, Build training DataFrame from shadow_trades records (fonte canônica — Bloco B)., Filter feature columns to those with sufficient coverage and variance.      Ex, Time-based split — no shuffle to avoid look-ahead bias.      Args:         df (+11 more)

### Community 2 - "Prediction & Forward Scoring"
Cohesion: 0.12
Nodes (13): ModelTrainer, Model Training — Train XGBoost model for trade outcome prediction., Evaluate model performance.          Args:             X: Features, Train and manage XGBoost models for trade outcome prediction., Get feature importance scores.          Args:             top_n: Number of to, Analyze win rate by probability bucket.          Args:             X: Feature, Initialize model trainer.          Args:             model_dir: Directory to, Save trained model to disk.          Args:             model_name: Name of th (+5 more)

### Community 3 - "Macro/Market Features"
Cohesion: 0.13
Nodes (13): get_model_loader(), load_model(), ModelLoader, Model Loader — Load and manage trained XGBoost models., Predict probabilities for multiple feature vectors.          Args:, Load and manage trained models for inference., Get list of required feature columns., Get global model loader instance.      Args:         model_path: Path to mode (+5 more)

### Community 4 - "Dataset Builder"
Cohesion: 0.12
Nodes (11): DatasetBuilder, Dataset Builder — Extract and prepare training data from trade_simulations., Extract features from simulations and create training dataset.          Args:, Build ML training datasets from trade simulation results., Create engineered features from base features.          Args:             df:, Encode direction as numeric feature.          Args:             df: DataFrame, Get list of feature columns for training (excluding metadata).          Args:, Complete data preparation pipeline.          Args:             simulations: L (+3 more)

### Community 5 - "Model Loader & Storage"
Cohesion: 0.19
Nodes (19): _extract_bonds(), _extract_crypto_global(), _extract_forex(), _extract_indices(), extract_macro_features(), _extract_volatility(), _find_by_symbol(), Market Data Hub — feature extraction and validation.  Converts raw MDH API respo (+11 more)

### Community 6 - "Evaluation & Reporting"
Cohesion: 0.19
Nodes (11): _analyze_probability_buckets(), EvaluationReport, generate_evaluation_report(), _load_active_model_and_threshold(), _load_decisions(), Evaluation Report — Generate comprehensive model evaluation metrics.  Rewritten, Win rate and mean PnL by probability bucket., Generate a comprehensive evaluation report for the active XGBoost model.      Us (+3 more)

### Community 7 - "Score Field Routing"
Cohesion: 0.16
Nodes (9): GCSModelLoader, get_model(), invalidate_model_cache(), Model loader — loads the active XGBoost model from the PostgreSQL ml_models tabl, Força reload no próximo request., Função de conveniência — use em qualquer lugar., Singleton que carrega e cacheia o modelo XGBoost do PostgreSQL (ml_models.model_, Retorna modelo — recarrega do DB se cache expirado. (+1 more)

### Community 8 - "Label & Fee Logic"
Cohesion: 0.18
Nodes (9): fetch_macro_context(), _get(), Market Data Hub — async HTTP client.  Fetches macro/intermarket context from t, GET with 1 retry on timeout / 5xx. Returns parsed JSON or None., Fetch all required macro endpoints concurrently and return a validated     feat, Preditor stateless — compatível com Cloud Run.     Modelo vive no GCS, carregad, Busca model_id e threshold do modelo ativo., Prediz probabilidade WIN_FAST para um sinal L3.          Returns: (+1 more)

### Community 9 - "Temporal Split Logic"
Cohesion: 0.50
Nodes (3): Passive forward scorer — scores new shadow trades at creation time.  ISOLATION I, Score a shadow trade and record the prediction in ml_predictions.      Opens its, safe_score_shadow_trade()

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PredictService` connect `Feature Extraction & Leakage Guard` to `Macro/Market Features`, `Dataset Builder`?**
  _High betweenness centrality (0.165) - this node is a cross-community bridge._
- **Why does `DatasetBuilder` connect `Dataset Builder` to `Feature Extraction & Leakage Guard`, `Prediction & Forward Scoring`?**
  _High betweenness centrality (0.159) - this node is a cross-community bridge._
- **Why does `ModelLoader` connect `Macro/Market Features` to `Feature Extraction & Leakage Guard`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `PredictService` (e.g. with `ModelLoader` and `DatasetBuilder`) actually correct?**
  _`PredictService` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `DatasetBuilder` (e.g. with `PredictService` and `ModelTrainer`) actually correct?**
  _`DatasetBuilder` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Dataset Builder — Extract and prepare training data from trade_simulations.`, `Build ML training datasets from trade simulation results.`, `Initialize dataset builder.` to the rest of the system?**
  _87 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Feature Extraction & Leakage Guard` be split into smaller, more focused modules?**
  _Cohesion score 0.10052910052910052 - nodes in this community are weakly interconnected._