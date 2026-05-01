"""Phase 2 rollout tests — bucketing math, score selection, fallback, preflight.

Phase 3 (deprecation) flipped the runtime default to ``robust`` for
every symbol and reduced :func:`should_use_robust` to a thin wrapper
around :func:`is_legacy_rollback_active`. The Phase 2 per-symbol bucket
math is preserved as :func:`is_symbol_in_robust_bucket` so this file —
which used to test the bucket math directly through
``should_use_robust`` — now exercises the same invariants through the
diagnostic helper. Score-selection tests assert the new "robust by
default unless rollback" behaviour.

The preflight guard is unchanged in Phase 3 and is still tested here.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.robust_indicators import (  # noqa: E402
    bucketed_symbols,
    get_rollout_percent,
    is_legacy_rollback_active,
    is_symbol_in_robust_bucket,
    select_authoritative_score,
    should_use_robust,
)
from app.services.robust_indicators.metrics import (  # noqa: E402
    reset_silent_fallback,
    silent_fallback_snapshot,
)
from app.services.robust_indicators.preflight import (  # noqa: E402
    PreflightResult,
    check_safe_to_raise,
    collect_window_metrics,
)


SAMPLE_SYMBOLS = [
    f"{c}{q}_USDT"
    for c in ("BTC", "ETH", "SOL", "ADA", "DOGE", "DOT", "LINK", "MATIC",
              "UNI", "AVAX", "SHIB", "PEPE", "WIF", "BONK", "RNDR", "FET",
              "TIA", "SEI", "JUP", "PYTH", "ARB", "OP", "BLUR", "STRK",
              "AAVE", "SUSHI", "COMP", "MKR", "CRV", "SNX", "GMX", "DYDX",
              "INJ", "KAS", "TON", "ATOM", "NEAR", "APT", "FTM", "ALGO",
              "XLM", "XRP", "LTC", "BCH", "TRX", "ETC", "VET", "HBAR",
              "ICP", "FIL")
    for q in ("",)
]


@pytest.fixture(autouse=True)
def _clean_rollback_env(monkeypatch):
    """Every test starts with the legacy-rollback flag OFF.

    Phase 3 made ``should_use_robust`` depend on
    ``LEGACY_PIPELINE_ROLLBACK``; without this fixture a stray env var
    or settings mutation from a previous test would silently flip the
    bucket math tests into the wrong branch.
    """
    monkeypatch.delenv("LEGACY_PIPELINE_ROLLBACK", raising=False)
    from app.services.robust_indicators import bucketing as bucketing_mod
    monkeypatch.setattr(
        bucketing_mod.settings, "LEGACY_PIPELINE_ROLLBACK", False, raising=False
    )
    assert is_legacy_rollback_active() is False
    yield


# ── bucket-math determinism (preserved as a diagnostic) ─────────────────────


def test_bucket_index_is_deterministic_per_symbol():
    """Same symbol → same bucket no matter how many times we ask."""
    for sym in SAMPLE_SYMBOLS[:10]:
        seen = {is_symbol_in_robust_bucket(sym, percent=37) for _ in range(20)}
        assert len(seen) == 1, f"Bucket flapped for {sym}: {seen}"


def test_bucket_uses_sha1_modulo_100():
    """Implementation contract: ``int(sha1(SYM).hex,16) % 100 < percent``."""
    sym = "BTC_USDT"
    expected_index = int(hashlib.sha1(sym.encode()).hexdigest(), 16) % 100
    assert is_symbol_in_robust_bucket(sym, percent=expected_index + 1)
    assert not is_symbol_in_robust_bucket(sym, percent=expected_index)


def test_bucket_extreme_percentages():
    for sym in SAMPLE_SYMBOLS:
        assert is_symbol_in_robust_bucket(sym, percent=100), sym
        assert not is_symbol_in_robust_bucket(sym, percent=0), sym


def test_bucketing_is_monotonic_with_percent():
    """Symbol bucketed at percent=N must also be bucketed at percent=N+k."""
    for sym in SAMPLE_SYMBOLS:
        first = next(
            (p for p in range(0, 101) if is_symbol_in_robust_bucket(sym, percent=p)),
            None,
        )
        if first is None:
            continue
        for p in range(first, 101):
            assert is_symbol_in_robust_bucket(sym, percent=p), (sym, p)


@pytest.mark.parametrize("percent,tolerance", [(10, 0.05), (50, 0.05), (90, 0.05)])
def test_bucketing_distribution_matches_percent(percent: int, tolerance: float):
    """Across a large pseudo-symbol set the bucketed fraction ≈ percent/100."""
    pool = [f"SYM{i:05d}_USDT" for i in range(2000)]
    bucketed = bucketed_symbols(pool, percent=percent)
    actual = len(bucketed) / len(pool)
    expected = percent / 100.0
    assert abs(actual - expected) < tolerance, (
        f"percent={percent} actual={actual:.3f} expected={expected:.2f}"
    )


def test_force_symbol_override(monkeypatch):
    sym = "FORCED_USDT"
    assert not is_symbol_in_robust_bucket(sym, percent=0)
    monkeypatch.setenv("ROBUST_FORCE_SYMBOLS", "FORCED_USDT,OTHER_USDT")
    assert is_symbol_in_robust_bucket(sym, percent=0)
    assert is_symbol_in_robust_bucket("other_usdt", percent=0)
    assert not is_symbol_in_robust_bucket("UNRELATED_USDT", percent=0)


def test_exclude_symbol_override(monkeypatch):
    sym = "EXCLUDED_USDT"
    assert is_symbol_in_robust_bucket(sym, percent=100)
    monkeypatch.setenv("ROBUST_EXCLUDE_SYMBOLS", "EXCLUDED_USDT")
    assert not is_symbol_in_robust_bucket(sym, percent=100)


def test_get_rollout_percent_clamps_and_parses(monkeypatch):
    monkeypatch.setattr(
        "app.services.robust_indicators.bucketing.settings",
        type("S", (), {"USE_ROBUST_INDICATORS_PERCENT": 0})(),
    )
    monkeypatch.setenv("USE_ROBUST_INDICATORS_PERCENT", "75")
    assert get_rollout_percent() == 75
    monkeypatch.setenv("USE_ROBUST_INDICATORS_PERCENT", "200")
    assert get_rollout_percent() == 100
    monkeypatch.setenv("USE_ROBUST_INDICATORS_PERCENT", "-3")
    assert get_rollout_percent() == 0
    monkeypatch.setenv("USE_ROBUST_INDICATORS_PERCENT", "garbage")
    assert get_rollout_percent() == 0
    # Explicit override always wins.
    assert get_rollout_percent(42) == 42


# ── Phase 3 hot-path: should_use_robust is a thin wrapper ──────────────────


def test_should_use_robust_is_true_by_default_for_every_symbol():
    """Phase 3: every non-empty symbol uses the robust engine by default."""
    for sym in SAMPLE_SYMBOLS:
        assert should_use_robust(sym) is True, sym
    # The percent argument is accepted for backwards compatibility but
    # MUST NOT downshift symbols off the robust engine in Phase 3.
    for sym in SAMPLE_SYMBOLS[:5]:
        assert should_use_robust(sym, percent=0) is True
        assert should_use_robust(sym, percent=10) is True
        assert should_use_robust(sym, percent=100) is True


def test_should_use_robust_returns_false_under_rollback(monkeypatch):
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    for sym in SAMPLE_SYMBOLS:
        assert should_use_robust(sym) is False, sym


def test_should_use_robust_empty_symbol_is_false():
    assert should_use_robust("") is False
    assert should_use_robust("   ") is False


# ── score selection ──────────────────────────────────────────────────────────


_INDICATORS_OK = {
    "rsi": 62.0,
    "adx": 28.0,
    "macd": 0.45,
    "macd_signal_line": 0.30,
    "macd_histogram": 0.15,
    "ema9": 100.0,
    "ema50": 95.0,
    "ema200": 90.0,
    "atr": 1.2,
    "atr_pct": 1.2,
    "close": 100.0,
    "taker_ratio": 0.6,
    "buy_pressure": 0.55,
    "volume_delta": 100.0,
    "taker_buy_volume": 600.0,
    "taker_sell_volume": 400.0,
    "taker_source": "merged",
}


_RULES_OK = [
    {"id": "r1", "indicator": "rsi",  "operator": ">=", "value": 50, "points": 10, "category": "momentum"},
    {"id": "r2", "indicator": "adx",  "operator": ">=", "value": 25, "points": 10, "category": "market_structure"},
    {"id": "r3", "indicator": "macd_histogram", "operator": ">=", "value": 0, "points": 5, "category": "momentum"},
    {"id": "r4", "indicator": "ema50", "operator": ">=", "value": 0, "points": 5, "category": "market_structure"},
]


def test_select_score_robust_by_default():
    """Phase 3: with no rollback set the robust engine is authoritative."""
    reset_silent_fallback()
    res = select_authoritative_score(
        "BTC_USDT", _INDICATORS_OK, legacy_score=72.5,
        score_config={"scoring_rules": _RULES_OK},
    )
    assert res.bucketed is True
    assert res.engine_tag == "robust"
    assert res.robust_score is not None
    assert res.score is not None
    assert 0.0 <= res.score <= 100.0
    assert silent_fallback_snapshot() == {}


def test_select_score_legacy_rollback_short_circuits(monkeypatch):
    """Setting LEGACY_PIPELINE_ROLLBACK forces every result to legacy."""
    reset_silent_fallback()
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    res = select_authoritative_score(
        "BTC_USDT", _INDICATORS_OK, legacy_score=72.5,
        score_config={"scoring_rules": _RULES_OK},
    )
    assert res.engine_tag == "legacy"
    assert res.bucketed is False
    assert res.score == pytest.approx(72.5)
    assert res.fell_back is True
    assert res.fallback_reason == "legacy_rollback"
    snap = silent_fallback_snapshot()
    assert snap.get("legacy_rollback", 0) >= 1


def test_select_score_explicit_percent_does_not_downshift_in_phase3():
    """percent=0 used to force legacy; in Phase 3 it must stay robust."""
    reset_silent_fallback()
    res = select_authoritative_score(
        "BTC_USDT", _INDICATORS_OK, legacy_score=72.5,
        score_config={"scoring_rules": _RULES_OK}, percent=0,
    )
    assert res.engine_tag == "robust"
    assert res.bucketed is True


def test_select_score_missing_indicators_is_robust_sentinel():
    """Phase 3: missing indicators outside rollback yields a robust-tagged
    sentinel (engine_tag="robust", score=None) — legacy is NOT returned.
    """
    reset_silent_fallback()
    res = select_authoritative_score(
        "BTC_USDT", {}, legacy_score=70.0,
        score_config={"scoring_rules": _RULES_OK},
    )
    assert res.bucketed is True
    assert res.engine_tag == "robust"
    assert res.score is None
    assert res.fell_back is False
    assert res.fallback_reason == "missing_indicators"
    # Counter still bumped for ops visibility.
    snap = silent_fallback_snapshot()
    assert snap.get("missing_indicators", 0) >= 1


def test_select_score_compute_failure_is_robust_sentinel(monkeypatch):
    """Phase 3: compute exceptions outside rollback yield a robust-tagged
    sentinel — legacy score is NEVER substituted.
    """
    reset_silent_fallback()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated envelope explosion")

    monkeypatch.setattr(
        "app.services.robust_indicators.select_score.envelope_indicators",
        _boom,
    )
    res = select_authoritative_score(
        "BTC_USDT", _INDICATORS_OK, legacy_score=70.0,
        score_config={"scoring_rules": _RULES_OK},
    )
    assert res.engine_tag == "robust"
    assert res.score is None
    assert res.fell_back is False
    assert res.fallback_reason == "compute_failed"
    assert silent_fallback_snapshot().get("compute_failed", 0) >= 1


def test_select_score_robust_rejected_is_authoritative():
    """Critical-gate rejection counts as a robust outcome (not a fallback)."""
    reset_silent_fallback()
    bad = dict(_INDICATORS_OK)
    bad.pop("rsi")
    bad.pop("adx")
    res = select_authoritative_score(
        "BTC_USDT", bad, legacy_score=70.0,
        score_config={"scoring_rules": _RULES_OK},
    )
    assert res.bucketed is True
    assert res.engine_tag == "robust"
    assert res.robust_score is not None
    assert res.robust_score.rejected
    assert silent_fallback_snapshot() == {}


# ── preflight guard (unchanged in Phase 3) ──────────────────────────────────


class _StubRow:
    def __init__(self, **kw: Any):
        for k, v in kw.items():
            setattr(self, k, v)


class _StubResult:
    def __init__(self, row: _StubRow):
        self._row = row

    def fetchone(self) -> _StubRow:
        return self._row


class _StubSession:
    """Async session stub that returns canned rows for the preflight query."""

    def __init__(self, row: _StubRow):
        self._row = row
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        return _StubResult(self._row)


def test_preflight_safe_when_metrics_healthy(monkeypatch):
    monkeypatch.delenv("FORCE_ROLLOUT_RAISE", raising=False)
    row = _StubRow(total=200, divergent_high=2, rejected=10, avg_confidence=0.85)
    db = _StubSession(row)
    result = asyncio.run(check_safe_to_raise(db, target_percent=50))
    assert isinstance(result, PreflightResult)
    assert result.safe is True
    assert result.forced is False
    assert result.reasons == []
    assert result.metrics["divergence_rate"] == pytest.approx(0.01)
    assert result.metrics["rejection_rate"] == pytest.approx(0.05)


def test_preflight_blocks_on_high_divergence(monkeypatch):
    monkeypatch.delenv("FORCE_ROLLOUT_RAISE", raising=False)
    row = _StubRow(total=100, divergent_high=20, rejected=5, avg_confidence=0.8)
    db = _StubSession(row)
    result = asyncio.run(check_safe_to_raise(db, target_percent=50))
    assert result.safe is False
    assert any("divergence_rate" in r for r in result.reasons)


def test_preflight_blocks_on_low_confidence(monkeypatch):
    monkeypatch.delenv("FORCE_ROLLOUT_RAISE", raising=False)
    row = _StubRow(total=100, divergent_high=1, rejected=1, avg_confidence=0.45)
    db = _StubSession(row)
    result = asyncio.run(check_safe_to_raise(db, target_percent=50))
    assert result.safe is False
    assert any("avg_confidence" in r for r in result.reasons)


def test_preflight_blocks_when_no_snapshots(monkeypatch):
    monkeypatch.delenv("FORCE_ROLLOUT_RAISE", raising=False)
    row = _StubRow(total=0, divergent_high=0, rejected=0, avg_confidence=None)
    db = _StubSession(row)
    result = asyncio.run(check_safe_to_raise(db, target_percent=10))
    assert result.safe is False
    assert any("no_snapshots" in r for r in result.reasons)


def test_preflight_force_override_bypasses_guard(monkeypatch):
    monkeypatch.setenv("FORCE_ROLLOUT_RAISE", "1")
    row = _StubRow(total=100, divergent_high=50, rejected=80, avg_confidence=0.1)
    db = _StubSession(row)
    result = asyncio.run(check_safe_to_raise(db, target_percent=100))
    assert result.safe is True
    assert result.forced is True
    # The reasons are still surfaced so the operator sees what they overrode.
    assert len(result.reasons) >= 1


def test_collect_window_metrics_resilient_to_db_error():
    class _BoomSession:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("db is sad today")

    metrics = asyncio.run(collect_window_metrics(_BoomSession()))
    assert metrics["total"] == 0
    assert "error" in metrics
