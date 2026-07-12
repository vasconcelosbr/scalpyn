# Contrato do dataset oficial ML point-in-time-v1

Sem `native_capture_start_at`, o dataset retorna zero linhas (`DATA_COLLECTION_NOT_STARTED`). São obrigatórios contrato `point-in-time-v1`, captura posterior à fronteira, snapshot, hash, extractor/schema versionados, `eligible_for_training=true` e lineage válida por source.

XGBoost usa lane L1 e somente `L1_SPECTRUM`. LightGBM/CatBoost usam lane L3 e sources L3. É proibido incluir legado, `RESEARCH_ONLY_UNPROVEN_TEMPORALITY`, `INVALID_TEMPORALITY`, temporalidade desconhecida, macrofeatures, versões incompatíveis ou fallback de `created_at`, `decision.created_at` e `promotion_at`.
