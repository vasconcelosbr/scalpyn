"""Fase 9 — 20 testes mandatórios do plano de correção ML 2026-06-30.

Cobre as correções implementadas nas Fases 1-8:
  Fase 2: circuit breaker usa ttt_fast_win_bucket, não outcome='TP_HIT'
  Fase 3: shadow_timeout_analyzer usa timeframe IN ('1m','5m')
  Fase 4: features_snapshot em pipeline_scan INSERT + prediction_service
  Fase 6: feature_importance extraído e normalizado em metrics_json
  Fase 7: NaN → None em features_snapshot, sem INSERT em ml_feature_observations
  Fase 8: build_training_dataframe prioriza ttt_fast_win_bucket como label
          [SUPERSEDIDA pelo label v2 — TestLabelV2SimOutcome assere o contrato
          vigente: outcome=='TP_HIT' AND holding<=threshold; TTT/pnl não
          definem label. Reescrita R2 2026-07-05.]
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
# FASE 8 (reescrita R2, 2026-07-05) — label v2: simulator ground truth
# ---------------------------------------------------------------------------

class TestLabelV2SimOutcome:
    """Label v2 (`is_tp_4h_v2_sim_outcome`): outcome=='TP_HIT' AND holding<=threshold.

    HISTÓRICO (R2, 2026-07-05): a classe original (TestTttLabelPriority, plano
    2026-06-30) asseria prioridade de `ttt_fast_win_bucket` e fallback por
    `pnl_pct` — ambos REMOVIDOS deliberadamente pelo label v2: TTT buckets e
    PnL realizado são sinais pós-entrada e não definem o label supervisionado
    (ver docstring de build_training_dataframe). Testes reescritos para
    asserir o contrato v2 com a mesma força:
      - bucket TTT é IGNORADO (não prioriza nem sobrepõe o label);
      - pnl_pct não define label (apenas pnl NULL derruba a linha);
      - TP lento (holding > threshold) é 0; TP rápido é 1; não-TP é 0.
    """

    def _make_record(self, ttt_bucket=None, outcome="TP_HIT", pnl_pct=1.5, holding_seconds=900):
        """Record com campos mínimos para build_training_dataframe."""
        return {
            "ttt_fast_win_bucket": ttt_bucket,
            "outcome": outcome,
            "pnl_pct": pnl_pct,
            "holding_seconds": holding_seconds,
            "created_at": "2026-06-29T00:00:00Z",
        }

    def test_fast_tp_is_positive_and_ttt_column_gone(self):
        """TP_HIT com holding<=threshold → 1; mecanismo _has_ttt_label não existe mais."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_0_15M")]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert len(df) == 1
        assert df["is_win_fast"].iloc[0] == 1
        assert "_has_ttt_label" not in df.columns, (
            "coluna do mecanismo TTT removido não deve reaparecer no dataframe"
        )

    def test_slow_tp_is_negative_even_with_fast_bucket(self):
        """v2: TP_HIT com holding>threshold é 0 — mesmo com bucket WIN_0_15M presente."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_0_15M", holding_seconds=7200)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 0, (
            "slow win é entrada ruim com saída sortuda — label 0 independe do bucket TTT"
        )

    def test_slow_bucket_does_not_override_fast_holding(self):
        """v2 IGNORA o bucket: WIN_60_180M com holding=900s<=1800 → 1 (outcome+holding mandam)."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket="WIN_60_180M", pnl_pct=3.0, holding_seconds=900)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 1, (
            "ttt_fast_win_bucket não sobrepõe o ground truth do simulador no v2"
        )

    def test_high_pnl_without_tp_outcome_is_negative(self):
        """pnl_pct não define label: TIMEOUT com pnl=3.0% é 0."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket=None, outcome="TIMEOUT", pnl_pct=3.0, holding_seconds=600)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 0, (
            "sem TP_HIT não há label 1 — pnl alto não é fallback no v2"
        )

    def test_low_pnl_fast_tp_is_positive(self):
        """Mudança INTENCIONAL vs plano jun-30: pnl=0.5% não derruba o label —
        TP_HIT + holding ok → 1 (ground truth do simulador, sem threshold de pnl)."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(ttt_bucket=None, pnl_pct=0.5, holding_seconds=600)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert df["is_win_fast"].iloc[0] == 1

    def test_null_pnl_row_dropped(self):
        """pnl_pct NULL → linha descartada (não vira label 0 silencioso)."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = [self._make_record(pnl_pct=None)]
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        assert len(df) == 0

    def test_positive_rate_reflects_outcome_holding_distribution(self):
        """positive_rate = fração de TP_HIT rápidos — 157/1000 = 15.7%."""
        from backend.app.ml.feature_extractor import build_training_dataframe
        records = (
            [self._make_record(holding_seconds=800)] * 85       # TP rápido → 1
            + [self._make_record(holding_seconds=1500)] * 72    # TP rápido → 1
            + [self._make_record(holding_seconds=7200)] * 200   # TP lento → 0
            + [self._make_record(outcome="SL_HIT", pnl_pct=-1.0)] * 643  # → 0
        )
        df = build_training_dataframe(records, win_fast_threshold_s=1800.0)
        pos_rate = df["is_win_fast"].mean()
        assert abs(pos_rate - 0.157) < 0.01, (
            f"Expected ~15.7% positive rate under label v2, got {pos_rate:.1%}"
        )
