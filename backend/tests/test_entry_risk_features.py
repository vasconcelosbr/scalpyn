from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.feature_extractor import FEATURE_COLUMNS, ML_EXCLUDED_FIELDS
from app.services.entry_risk_features import (
    INDICATOR_GOVERNANCE,
    LEGACY_FORMULA_VERSION,
    OBSERVATIONAL_ONLY_FIELDS,
    assert_no_observational_execution_fields,
    build_entry_risk_contract,
    calculate_legacy_entry_exhaustion,
    candle_window_hash,
    closed_candle_window,
    legacy_entry_exhaustion_score,
)
from app.services.feature_engine import FeatureEngine
from app.utils.indicator_merge import merge_indicator_rows
from app.schemas.entry_risk_observation import EntryRiskObservationConfig
from pydantic import ValidationError


def _candles(count: int = 60, *, end: datetime | None = None) -> pd.DataFrame:
    end = end or datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    start = end - timedelta(minutes=5 * (count - 1))
    close = np.linspace(100.0, 112.0, count)
    return pd.DataFrame({
        "time": [start + timedelta(minutes=5 * i) for i in range(count)],
        "open": close - 0.2,
        "high": close + 0.7,
        "low": close - 0.8,
        "close": close,
        "volume": np.linspace(1000.0, 2000.0, count),
        "quote_volume": np.linspace(100000.0, 224000.0, count),
    })


def _legacy_reference(df: pd.DataFrame, atr_period: int) -> float | None:
    if len(df) < 50:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    current = float(close.iloc[-1])
    if not np.isfinite(current) or current <= 0:
        return None
    close_5 = float(close.iloc[-6])
    roc5 = max(-20.0, min(20.0, (current - close_5) / close_5 * 100))
    score5 = (roc5 + 20.0) / 40.0 * 100
    close_20 = float(close.iloc[-21])
    roc20 = max(-50.0, min(50.0, (current - close_20) / close_20 * 100))
    score20 = (roc20 + 50.0) / 100.0 * 100
    rolling_high = float(high.iloc[-50:].max())
    distance = max(-20.0, min(0.0, (current - rolling_high) / rolling_high * 100))
    distance_score = (distance + 20.0) / 20.0 * 100
    ranges = (high - low).clip(lower=0)
    atr = float(ranges.rolling(window=min(atr_period, len(df))).mean().iloc[-1])
    expansion = max(0.0, min(5.0, float(ranges.iloc[-1]) / atr))
    expansion_score = expansion / 5.0 * 100
    volume_score = float(np.mean(volume.iloc[-50:].values <= float(volume.iloc[-1]))) * 100
    return round(
        0.30 * distance_score + 0.20 * score5 + 0.20 * score20
        + 0.15 * expansion_score + 0.15 * volume_score,
        1,
    )


@pytest.mark.parametrize("atr_period", [7, 14, 21])
def test_legacy_formula_is_numerically_identical(atr_period: int):
    frame = _candles()
    expected = _legacy_reference(frame, atr_period)
    assert legacy_entry_exhaustion_score(frame, atr_period=atr_period) == expected
    engine = FeatureEngine({"atr": {"period": atr_period}})
    assert engine._calc_entry_exhaustion(frame)["entry_exhaustion_score"] == expected


def test_legacy_decomposition_reconstructs_score():
    result = calculate_legacy_entry_exhaustion(_candles())
    assert result["score"] is not None
    assert len(result["components"]) == 5
    reconstructed = round(sum(c["contribution"] for c in result["components"].values()), 1)
    assert abs(reconstructed - result["score"]) <= 0.2
    assert all(c["weight"] is not None for c in result["components"].values())


def test_closed_window_excludes_open_and_future_candles():
    frame = _candles(55, end=datetime(2026, 8, 22, 12, 10, tzinfo=timezone.utc))
    entry_at = datetime(2026, 8, 22, 12, 12, tzinfo=timezone.utc)
    window = closed_candle_window(frame, entry_at)
    assert len(window) == 50
    assert window.iloc[-1]["time"].to_pydatetime() == datetime(2026, 8, 22, 12, 5, tzinfo=timezone.utc)
    assert window.iloc[-1]["time"].to_pydatetime() + timedelta(minutes=5) <= entry_at


def test_candle_hash_is_deterministic_and_sensitive_to_data():
    frame = _candles(50)
    first = candle_window_hash(frame)
    second = candle_window_hash(frame.copy())
    changed = frame.copy()
    changed.loc[changed.index[-1], "close"] += 0.0001
    assert first == second
    assert first != candle_window_hash(changed)


def test_contract_is_monitor_only_and_never_invents_candidate_scores():
    entry_at = datetime(2026, 8, 22, 12, 5, tzinfo=timezone.utc)
    frame = _candles(50, end=datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc))
    features = {"adx": 24.0, "entry_exhaustion_score": 65.0}
    metadata = {"adx": {"timestamp": entry_at.isoformat(), "timeframe": "5m", "group": "structural", "stale": False}}
    contract = build_entry_risk_contract(
        candles=frame,
        features=features,
        feature_metadata=metadata,
        symbol="UNI_USDT",
        exchange="gate",
        market_type="spot",
        entry_at=entry_at,
        decision_at=entry_at,
        profile_id="p1",
        profile_name="L3_TEST",
        profile_family="TEST",
        profile_version_id="v1",
        regime={"regime": "TRENDING"},
    )
    assert contract["schema_version"] == "entry_risk_features_v1"
    assert contract["legacy"]["formula_version"] == LEGACY_FORMULA_VERSION
    assert contract["legacy"]["operational_effect"] is False
    assert contract["momentum_intensity"]["momentum_intensity_score"] is None
    assert contract["exhaustion_risk"]["exhaustion_risk_score"] is None
    assert contract["momentum_intensity"]["operational_effect"] is False
    assert contract["exhaustion_risk"]["operational_effect"] is False
    for component in contract["momentum_intensity"]["components"].values():
        assert component["weight"] is None
        assert component["contribution"] is None
    for dimension in contract["exhaustion_risk"]["dimensions"].values():
        for component in dimension.values():
            assert component["weight"] is None
            assert component["contribution"] is None
    serialized = json.dumps(contract).lower()
    assert "mae_pct" not in serialized
    assert "mfe_pct" not in serialized
    assert "outcome" not in serialized
    assert "pnl_pct" not in serialized


def test_timeframe_metadata_is_preserved_without_changing_latest_wins():
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    merged = merge_indicator_rows([
        ("structural", now - timedelta(seconds=30), "30m", {"adx": 20.0}),
        ("structural", now - timedelta(seconds=10), "5m", {"adx": 25.0}),
    ], now=now)
    assert merged.values["adx"] == 25.0
    assert merged.meta["adx"]["timeframe"] == "5m"
    assert merged.meta["adx"]["observed_timeframes"] == ["30m", "5m"]
    assert merged.meta["adx"]["timeframe_conflict"] is True


def test_observational_scores_are_rejected_from_profiles_and_ml():
    for name in OBSERVATIONAL_ONLY_FIELDS:
        assert INDICATOR_GOVERNANCE[name]["status"] == "OBSERVATIONAL_ONLY"
        assert INDICATOR_GOVERNANCE[name]["entry_trigger_allowed"] is False
        with pytest.raises(ValueError, match="OBSERVATIONAL_ONLY"):
            assert_no_observational_execution_fields({
                "entry_triggers": {"conditions": [{"indicator": name, "operator": ">", "value": 50}]}
            })
        assert name in ML_EXCLUDED_FIELDS
        assert name not in FEATURE_COLUMNS


def test_operational_config_cannot_be_enabled_in_v1():
    assert EntryRiskObservationConfig().source_stale_seconds == 300
    with pytest.raises(ValidationError):
        EntryRiskObservationConfig(momentum_operational=True)
    with pytest.raises(ValidationError):
        EntryRiskObservationConfig(exhaustion_operational=True)


def test_shadow_writers_initialize_durable_pending_capture():
    service = Path(__file__).parents[1] / "app" / "services" / "shadow_trade_service.py"
    source = service.read_text(encoding="utf-8")
    assert source.count("entry_risk_features_json, entry_risk_capture_status") == 2
    assert source.count('"entry_risk_capture_status": "PENDING"') == 3


def test_capture_and_migration_are_fail_closed_and_point_in_time():
    backend = Path(__file__).parents[1]
    capture = (
        backend / "app" / "services" / "entry_risk_capture_service.py"
    ).read_text(encoding="utf-8")
    migration = (
        backend / "alembic" / "versions" / "196_entry_risk_observation.py"
    ).read_text(encoding="utf-8")
    assert "timeframe = '5m'" in capture
    assert "market_type = 'spot'" in capture
    assert "time + interval '5 minutes' <= :entry_at" in capture
    # asyncpg cannot infer a nullable bind used by both ``IS NULL`` and
    # ``lower()``.  Keep the explicit cast so live captures do not fail with
    # AmbiguousParameterError when exchange is populated.
    assert capture.count("CAST(:exchange AS text)") == 3
    assert "FOR UPDATE OF st SKIP LOCKED" in capture
    assert "LEGACY_UNVERIFIABLE" not in migration
    for status in ("NOT_AVAILABLE", "PENDING", "VALID", "PARTIAL", "INVALID", "ERROR"):
        assert status in migration


def test_candidate_scores_have_no_execution_consumers():
    root = Path(__file__).parents[1] / "app"
    allowed = {
        root / "services" / "entry_risk_features.py",
        root / "services" / "entry_risk_capture_service.py",
        root / "services" / "module_ai_analysis_service.py",
        root / "ml" / "feature_extractor.py",
    }
    execution_files = [
        root / "services" / "score_engine.py",
        root / "services" / "profile_engine.py",
        root / "services" / "l3_trade_consolidation.py",
        root / "services" / "execution_engine.py",
        root / "tasks" / "pipeline_scan.py",
        root / "tasks" / "execute_buy.py",
        root / "ml" / "promotion_gate.py",
        root / "ml" / "intelligence_gate.py",
    ]
    for path in execution_files:
        if path in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        assert "momentum_intensity_score" not in source
        assert "exhaustion_risk_score" not in source
