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
    from backend.app.ml.prediction_service import WinFastPredictor

    predictor = WinFastPredictor()
    with patch("backend.app.ml.prediction_service.get_model", return_value=_BrokenModel()):
        with patch.object(
            predictor,
            "_get_threshold",
            new=AsyncMock(return_value=("00000000-0000-0000-0000-000000000001", 0.5)),
        ):
            with patch(
                "backend.app.ml.prediction_service.fetch_macro_context",
                new=AsyncMock(return_value={"macro_context_available": False}),
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
    assert result["score_status"] == "SKIPPED"
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
    assert '"score_status": "SKIPPED"' in snippet
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


def test_migration_111_relaxes_ml_predictions_for_gate_blocks():
    source = (
        _repo_root()
        / "backend"
        / "alembic"
        / "versions"
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
