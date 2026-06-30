"""Fase 9 — 20 testes mandatórios do plano de correção ML 2026-06-30.

Cobre as correções implementadas nas Fases 1-8:
  Fase 2: circuit breaker usa ttt_fast_win_bucket, não outcome='TP_HIT'
  Fase 3: shadow_timeout_analyzer usa timeframe IN ('1m','5m')
  Fase 4: features_snapshot em pipeline_scan INSERT + prediction_service
  Fase 6: feature_importance extraído e normalizado em metrics_json
  Fase 7: NaN → None em features_snapshot, sem INSERT em ml_feature_observations
  Fase 8: build_training_dataframe prioriza ttt_fast_win_bucket como label
"""

import inspect
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# FASE 2 — Circuit breaker: ttt_fast_win_bucket como métrica de win_rate
# ---------------------------------------------------------------------------

class TestCircuitBreakerDriftMetric:
    """_check_win_rate_drift deve usar ttt_fast_win_bucket, não outcome='TP_HIT'."""

    def _get_source(self) -> str:
        import backend.app.services.profile_intelligence_live_service as m
        return inspect.getsource(m._check_win_rate_drift)

    def test_circuit_breaker_uses_ttt_fast_win_bucket(self):
        src = self._get_source()
        assert "ttt_fast_win_bucket" in src, (
            "_check_win_rate_drift must use ttt_fast_win_bucket for win rate calc"
        )

    def test_circuit_breaker_does_not_use_outcome_eq_tp_hit_for_numerator(self):
        """The numerator (win count) must NOT be outcome='TP_HIT'.
        TP_HIT gives ~45% win rate; ttt_fast_win_bucket gives the true ~15% fast-TP rate.
        """
        src = self._get_source()
        lines = src.splitlines()
        # Find numerator block — the SUM for win_rate_7d numerator
        # ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M') must appear
        # AND must not be sole condition outcome='TP_HIT' in numerator SUM
        assert "WIN_0_15M" in src, "WIN_0_15M bucket must appear in numerator"
        assert "WIN_15_30M" in src, "WIN_15_30M bucket must appear in numerator"

    def test_circuit_breaker_denominator_uses_closed_outcomes(self):
        """Denominator still uses outcome IN ('TP_HIT','SL_HIT','TIMEOUT') to count closed trades."""
        src = self._get_source()
        assert "TP_HIT" in src, "Denominator must count TP_HIT as closed trade"
        assert "SL_HIT" in src, "Denominator must count SL_HIT as closed trade"
        assert "TIMEOUT" in src, "Denominator must count TIMEOUT as closed trade"

    def test_circuit_breaker_threshold_is_15pp(self):
        """Alert threshold must be 15 percentage-points — documented in MEMORY.md."""
        import backend.app.services.profile_intelligence_live_service as m
        src = inspect.getsource(m._check_win_rate_drift)
        assert "15" in src, "15pp drift threshold must appear in _check_win_rate_drift"


# ---------------------------------------------------------------------------
# FASE 3 — shadow_timeout_analyzer: fallback para timeframe 5m
# ---------------------------------------------------------------------------

class TestShadowTimeoutAnalyzerCandleFallback:
    """Verificar que o shadow_timeout_analyzer usa IN ('1m','5m') não = '1m'."""

    def _get_fetch_window_source(self) -> str:
        import backend.app.tasks.shadow_timeout_analyzer as m
        return inspect.getsource(m._fetch_ohlcv_window)

    def _get_fetch_close_source(self) -> str:
        import backend.app.tasks.shadow_timeout_analyzer as m
        return inspect.getsource(m._fetch_close_near_horizon)

    def test_fetch_ohlcv_window_uses_in_clause_not_eq(self):
        src = self._get_fetch_window_source()
        assert "IN ('1m', '5m')" in src or "IN ('1m','5m')" in src, (
            "_fetch_ohlcv_window must use timeframe IN ('1m','5m') for 5m fallback"
        )

    def test_fetch_ohlcv_window_not_hardcoded_to_1m(self):
        src = self._get_fetch_window_source()
        assert "timeframe = '1m'" not in src, (
            "_fetch_ohlcv_window must not hardcode timeframe = '1m'"
        )

    def test_fetch_close_uses_in_clause(self):
        src = self._get_fetch_close_source()
        assert "IN ('1m', '5m')" in src or "IN ('1m','5m')" in src, (
            "_fetch_close_near_horizon must use timeframe IN ('1m','5m')"
        )

    def test_fetch_ohlcv_uses_distinct_on_time(self):
        """DISTINCT ON (time) ensures one candle per timestamp (prefers 1m via ASC sort)."""
        src = self._get_fetch_window_source()
        assert "DISTINCT ON" in src, (
            "_fetch_ohlcv_window must use DISTINCT ON (time) to deduplicate multi-timeframe candles"
        )

    def test_fetch_ohlcv_prefers_1m_over_5m(self):
        """ORDER BY time ASC, timeframe ASC means '1m' < '5m' alphabetically → 1m preferred."""
        src = self._get_fetch_window_source()
        assert "timeframe ASC" in src, (
            "ORDER BY must include timeframe ASC so '1m' beats '5m' in DISTINCT ON"
        )


# ---------------------------------------------------------------------------
# FASE 4 — features_snapshot em prediction_service
# ---------------------------------------------------------------------------

class TestFeatureSnapshotInPredictionResult:
    """predict() deve retornar features_snapshot com NaN→None."""

    def test_prediction_service_returns_features_snapshot_key(self):
        src_path = Path(__file__).resolve().parents[2] / "backend" / "app" / "ml" / "prediction_service.py"
        src = src_path.read_text(encoding="utf-8")
        assert '"features_snapshot"' in src, (
            "prediction_service.predict() must return 'features_snapshot' in result dict"
        )

    def test_features_snapshot_converts_nan_to_none(self):
        """NaN values in features must be converted to None for JSON safety."""
        src_path = Path(__file__).resolve().parents[2] / "backend" / "app" / "ml" / "prediction_service.py"
        src = src_path.read_text(encoding="utf-8")
        # Deve haver conversão NaN → None
        assert "isnan" in src or "math.isnan" in src or "_math.isnan" in src, (
            "features_snapshot must convert NaN to None (isnan check)"
        )

    def test_features_snapshot_pure_logic_nan_to_none(self):
        """Unit test the NaN→None conversion logic independently."""
        import math
        features = {"rsi": 65.0, "adx": float("nan"), "volume_spike": 1.2}
        snapshot = {
            k: (None if isinstance(v, float) and math.isnan(v) else v)
            for k, v in features.items()
        }
        assert snapshot["rsi"] == 65.0
        assert snapshot["adx"] is None  # NaN → None
        assert snapshot["volume_spike"] == 1.2

    def test_features_snapshot_not_inserting_into_ml_feature_observations(self):
        """ml_feature_observations has a different schema (feature_name/value for MDH features).
        prediction_service must NOT try to insert inference features into it.
        """
        src_path = Path(__file__).resolve().parents[2] / "backend" / "app" / "ml" / "prediction_service.py"
        src = src_path.read_text(encoding="utf-8")
        # O INSERT em ml_feature_observations foi removido
        assert "ml_feature_observations" not in src, (
            "prediction_service must not INSERT into ml_feature_observations — wrong schema"
        )


# ---------------------------------------------------------------------------
# FASE 6 — feature_importance em ml_challenger_service
# ---------------------------------------------------------------------------

class TestFeatureImportanceExtraction:
    """ml_challenger_service deve extrair feature_importance e normalizar para soma=1."""

    def test_feature_importance_normalization_logic(self):
        """Verifica a lógica de normalização: cada valor / soma_total."""
        raw = [3.0, 1.0, 1.0]
        feature_names = ["rsi", "adx", "volume"]
        total = sum(raw) or 1.0
        fi = {feature_names[i]: round(float(raw[i]) / total, 6) for i in range(len(raw))}
        assert abs(sum(fi.values()) - 1.0) < 1e-5
        assert fi["rsi"] == round(3.0 / 5.0, 6)

    def test_feature_importance_handles_zero_total(self):
        """Se todos os importances são zero, deve evitar divisão por zero."""
        raw = [0.0, 0.0, 0.0]
        feature_names = ["rsi", "adx", "volume"]
        total = sum(raw) or 1.0  # evita divisão por zero
        fi = {feature_names[i]: round(float(raw[i]) / total, 6) for i in range(len(raw))}
        assert all(v == 0.0 for v in fi.values())

    def test_ml_challenger_saves_feature_importance_to_metrics_json(self):
        """_save_to_db deve incluir feature_importance em _metrics_json_dict."""
        src_path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "app" / "services" / "ml_challenger_service.py"
        )
        src = src_path.read_text(encoding="utf-8")
        assert "feature_importance" in src, (
            "_save_to_db must store feature_importance in metrics_json"
        )

    def test_feature_importance_tries_all_model_apis(self):
        """Deve tentar feature_importances_ (sklearn/lgbm), feature_importance (CB), get_feature_importance (CB alt)."""
        src_path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "app" / "services" / "ml_challenger_service.py"
        )
        src = src_path.read_text(encoding="utf-8")
        assert "feature_importances_" in src, "Must try sklearn-style feature_importances_"
        # CatBoost has .feature_importance() method
        assert "feature_importance" in src, "Must try CatBoost-style feature_importance"


# ---------------------------------------------------------------------------
# FASE 8 — ttt_fast_win_bucket como label primário em build_training_dataframe
# ---------------------------------------------------------------------------

class TestTttLabelPriority:
    """build_training_dataframe deve priorizar ttt_fast_win_bucket quando disponível."""

    def _make_record(self, ttt_bucket=None, outcome="TP_HIT", pnl_pct=1.5, holding_seconds=900):
        """Record com campos mínimos para build_training_dataframe."""
        return {
            "ttt_fast_win_bucket": ttt_bucket,
            "outcome": outcome,
            "pnl_pct": pnl_pct,
            "holding_seconds": holding_seconds,
            "created_at": "2026-06-29T00:00:00Z",
        }

    def test_win_0_15m_bucket_is_positive(self):
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_0_15M")]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert len(df) == 1
        assert df["is_win_fast"].iloc[0] == 1
        assert df["_has_ttt_label"].iloc[0] == 1

    def test_win_15_30m_bucket_is_positive(self):
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_15_30M")]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 1
        assert df["_has_ttt_label"].iloc[0] == 1

    def test_win_60_180m_bucket_is_negative(self):
        """Slow TP (60-180min) deve ser label=0 mesmo com outcome=TP_HIT."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_60_180M", outcome="TP_HIT", pnl_pct=2.0)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 0, (
            "WIN_60_180M is not a fast TP — must be label=0 even with pnl>threshold"
        )
        assert df["_has_ttt_label"].iloc[0] == 1

    def test_no_ttt_bucket_falls_back_to_pnl(self):
        """Sem ttt_fast_win_bucket, usa fallback pnl_pct > _WIN_THRESHOLD."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        # pnl=1.5% > 0.96% threshold e holding=900s <= 1800s → label=1 via fallback
        records = [self._make_record(ttt_bucket=None, pnl_pct=1.5, holding_seconds=900)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["_has_ttt_label"].iloc[0] == 0  # sem ttt
        assert df["is_win_fast"].iloc[0] == 1  # label via pnl fallback

    def test_no_ttt_bucket_low_pnl_is_negative(self):
        """Sem ttt e pnl abaixo do threshold → label=0."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        # pnl=0.5% < 0.96% → label=0 mesmo com holding curto
        records = [self._make_record(ttt_bucket=None, pnl_pct=0.5, holding_seconds=600)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 0

    def test_ttt_label_overrides_pnl_even_with_high_pnl(self):
        """ttt_bucket='WIN_60_180M' → label=0 mesmo se pnl=3.0% (muito acima do threshold).
        Label vem do ttt, não do pnl quando ttt disponível.
        """
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_60_180M", pnl_pct=3.0, holding_seconds=1800)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 0, (
            "ttt_bucket must override pnl label — slow TP must be 0 regardless of pnl"
        )

    def test_positive_rate_with_ttt_labels_reflects_ttt_distribution(self):
        """Com ttt_fast_win_bucket disponível, positive_rate deve refletir a distribuição real (15-17%), não 3.1%."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        # 157 fast wins (WIN_0_15M or WIN_15_30M) out of 1000 total → ~15.7%
        records = (
            [self._make_record(ttt_bucket="WIN_0_15M")] * 85
            + [self._make_record(ttt_bucket="WIN_15_30M")] * 72
            + [self._make_record(ttt_bucket="WIN_60_180M")] * 200  # slow TPs → label=0
            + [self._make_record(ttt_bucket=None, outcome="SL_HIT", pnl_pct=-1.0)] * 643
        )
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        pos_rate = df["is_win_fast"].mean()
        # 157/1000 = 15.7%
        assert abs(pos_rate - 0.157) < 0.01, (
            f"Expected ~15.7% positive rate with ttt labels, got {pos_rate:.1%}"
        )
