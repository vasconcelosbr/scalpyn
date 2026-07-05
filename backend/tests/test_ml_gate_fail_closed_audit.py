import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch


class _BrokenModel:
    n_features_in_ = 1

    def predict_proba(self, _x):
        raise RuntimeError("feature schema drift")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_predict_proba_exception_returns_fail_closed_contract():
    # ATUALIZADO (R2, 2026-07-05): removido o patch de fetch_macro_context —
    # o enriquecimento macro na inferência foi eliminado do prediction_service
    # (ml_macro_feature_names=[] em config). O contrato fail-closed sob
    # exceção de predict_proba permanece asserido com a mesma força.
    from backend.app.ml.prediction_service import WinFastPredictor

    predictor = WinFastPredictor()
    with patch("backend.app.ml.prediction_service.get_model", return_value=_BrokenModel()):
        with patch.object(
            predictor,
            "_get_threshold",
            new=AsyncMock(return_value=("00000000-0000-0000-0000-000000000001", 0.5, "50")),
        ):
            result = asyncio.run(
                predictor.predict(
                    metrics={"rsi": 50.0},
                    db=AsyncMock(),
                    model_lane="L3_PROFILE",
                )
            )

    assert result["win_fast_probability"] is None
    assert result["model_approved"] is False
    assert result["score_status"] == "ML_EXCEPTION_FAIL_CLOSED"
    assert result["reason_code"] == "ML_EXCEPTION_FAIL_CLOSED"
    assert result["model_lane"] == "L3_PROFILE"


def test_pipeline_ml_predict_one_exception_is_fail_closed():
    source = (_repo_root() / "backend" / "app" / "tasks" / "pipeline_scan.py").read_text(
        encoding="utf-8"
    )
    idx = source.index("async def _ml_predict_one")
    snippet = source[idx : idx + 2400]

    assert '"model_approved": False' in snippet
    assert '"reason_code": "ML_EXCEPTION_FAIL_CLOSED"' in snippet
    assert '"score_status": "ML_EXCEPTION_FAIL_CLOSED"' in snippet
    assert '"model_approved": True' not in snippet


def test_pipeline_persists_flat_ml_gate_reasons_contract():
    source = (_repo_root() / "backend" / "app" / "tasks" / "pipeline_scan.py").read_text(
        encoding="utf-8"
    )

    for key in (
        '_reasons["ml_gate"]',
        '_reasons["model_approved"]',
        '_reasons["reason_code"]',
        '_reasons["score_status"]',
        '_reasons["decision_before_ml"]',
        '_reasons["decision_after_ml"]',
        '_reasons["fallback_policy"]',
    ):
        assert key in source


def test_pipeline_ml_predictions_insert_keeps_skipped_block_rows():
    source = (_repo_root() / "backend" / "app" / "tasks" / "pipeline_scan.py").read_text(
        encoding="utf-8"
    )
    idx = source.index("INSERT INTO ml_predictions")
    snippet = source[idx : idx + 2500]

    assert "gate_payload" in snippet
    assert "reason_code" in snippet
    assert "score_status" in snippet
    assert "promotion_gate_status" in snippet
    assert "if _pprob is None" not in snippet


def test_pipeline_links_ml_ranking_to_decision_id_after_persist():
    source = (_repo_root() / "backend" / "app" / "tasks" / "pipeline_scan.py").read_text(
        encoding="utf-8"
    )

    assert "UPDATE ml_opportunity_rankings" in source
    assert "SET decision_id = :decision_id" in source
    assert "AND decision_id IS NULL" in source
    assert 'if _ml_gate_enabled and sym in _ml_gate_scores' in source
    assert 'event_type = "ML_GATE_ALLOWED" if d.get("decision") == "ALLOW" else "ML_GATE_BLOCKED"' in source


def test_pipeline_decisions_log_has_first_class_ml_gate_fields():
    source = (_repo_root() / "backend" / "app" / "tasks" / "pipeline_scan.py").read_text(
        encoding="utf-8"
    )

    for key in (
        "ranking_id=_uuid_or_none",
        "model_id=_uuid_or_none",
        "model_version=decision.get",
        "probability=decision.get",
        "threshold_used=decision.get",
        "gate_action=decision.get",
        "orchestrator_payload=decision.get",
        "ml_gate_enabled=bool",
    ):
        assert key in source


def test_l1_ranker_top_k_is_used_before_l3_gate():
    source = (_repo_root() / "backend" / "app" / "tasks" / "pipeline_scan.py").read_text(
        encoding="utf-8"
    )

    assert "def _rank_l1_candidates" in source
    assert "L1_TOP_K_DEFAULT" in source
    assert "L1_PERCENTILE_MIN" in source
    assert "L1_ALLOW_THRESHOLD_GATE" in source
    assert '"L1_TOP_K_SELECTED"' in source
    assert '"L1_TOP_K_REJECTED"' in source
    assert 'model_lane="L1_SPECTRUM"' in source
    assert 'if _l1_rank.get("selected")' in source
    assert "_ml = await _ml_predict_one(_d)" in source


def test_migration_112_adds_ml_gate_lineage_contract():
    # ATUALIZADO (R2, 2026-07-05): migrations pré-baseline movidas para
    # alembic/versions/legacy/ (repo reestruturado com 000_baseline_prod_schema).
    # Mesmas asserções de DDL; só o path mudou.
    source = (
        _repo_root()
        / "backend"
        / "alembic"
        / "versions"
        / "legacy"
        / "112_ml_gate_lineage_contract.py"
    ).read_text(encoding="utf-8")

    for table in ("decisions_log", "ml_opportunity_rankings", "shadow_trades"):
        assert f"ALTER TABLE {table}" in source
    for col in (
        "ranking_id UUID",
        "model_version VARCHAR",
        "gate_action VARCHAR",
        "reason_codes JSONB",
        "orchestrator_payload JSONB",
        "ml_gate_enabled BOOLEAN",
    ):
        assert col in source


def test_migration_111_relaxes_ml_predictions_for_gate_blocks():
    # ATUALIZADO (R2, 2026-07-05): path corrigido para alembic/versions/legacy/
    # (repo reestruturado com 000_baseline_prod_schema). Asserções inalteradas.
    source = (
        _repo_root()
        / "backend"
        / "alembic"
        / "versions"
        / "legacy"
        / "111_ml_gate_audit_payload.py"
    ).read_text(encoding="utf-8")

    assert "ALTER COLUMN model_id DROP NOT NULL" in source
    assert "ALTER COLUMN win_fast_probability DROP NOT NULL" in source
    assert "ALTER COLUMN threshold_used DROP NOT NULL" in source
    for col in (
        "model_lane",
        "reason_code",
        "score_status",
        "promotion_gate_status",
        "gate_payload",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in source


def test_orchestrator_blocks_l3_l1_only_fallback_when_ml_gate_enabled():
    source = (
        _repo_root() / "backend" / "app" / "services" / "decision_orchestrator.py"
    ).read_text(encoding="utf-8")

    assert 'os.getenv("ML_GATE_ENABLED", "false").lower() == "true"' in source
    assert "ML gate blocks L3 fallback" in source
    assert "continue" in source[source.index("ML gate blocks L3 fallback") : source.index("ML gate blocks L3 fallback") + 250]


def test_catboost_bytea_loader_stamps_inference_feature_count():
    source = (
        _repo_root() / "backend" / "app" / "ml" / "gcs_model_loader.py"
    ).read_text(encoding="utf-8")

    assert "model._n_inference_features = len(feature_columns)" in source
    assert "model._inference_feature_names = list(feature_columns)" in source
    assert "CatBoost" in source


def test_catboost_v50_inference_uses_dataframe_for_categorical_features():
    source = (
        _repo_root() / "backend" / "app" / "ml" / "prediction_service.py"
    ).read_text(encoding="utf-8")

    assert "_has_cat_features" in source
    assert "_pd.DataFrame" in source
    assert 'X_infer["source_encoded"]' in source
    assert 'X_infer["profile_id_encoded"]' in source
    assert "predict_positive_probability(" in source
