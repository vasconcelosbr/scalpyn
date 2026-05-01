"""Phase 3 (deprecation) tests — robust default + LEGACY_PIPELINE_ROLLBACK.

Phase 3 makes the robust engine the formal default everywhere and
keeps the legacy engine on standby behind a single
``LEGACY_PIPELINE_ROLLBACK`` flag. These tests assert:

  * ``Settings`` ships with ``USE_ROBUST_INDICATORS_PERCENT=100`` and
    ``LEGACY_PIPELINE_ROLLBACK=False`` out of the box.
  * ``is_legacy_rollback_active`` honours both the settings attribute
    and the env-var fallback (truthy strings only).
  * ``should_use_robust`` is a thin wrapper that returns ``True`` for
    every non-empty symbol unless the rollback flag is set.
  * ``select_authoritative_score`` short-circuits to legacy when the
    rollback is active and bumps the ``legacy_rollback`` silent-
    fallback counter.
  * The Celery rollback-standby check fires only after the rollback
    has been ACTIVE for >24h and clears state once unset.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Settings  # noqa: E402
from app.services.robust_indicators import (  # noqa: E402
    is_legacy_rollback_active,
    select_authoritative_score,
    should_use_robust,
)
from app.services.robust_indicators.metrics import (  # noqa: E402
    reset_silent_fallback,
    silent_fallback_snapshot,
)


@pytest.fixture(autouse=True)
def _clean_rollback_env(monkeypatch):
    """Every test starts with the legacy-rollback flag OFF and a clean
    silent-fallback counter so cross-test bleed-through can't mask a
    regression in either direction.
    """
    monkeypatch.delenv("LEGACY_PIPELINE_ROLLBACK", raising=False)
    from app.services.robust_indicators import bucketing as bucketing_mod
    monkeypatch.setattr(
        bucketing_mod.settings, "LEGACY_PIPELINE_ROLLBACK", False, raising=False
    )
    reset_silent_fallback()
    yield


# ── settings defaults ───────────────────────────────────────────────────────


def test_settings_defaults_make_robust_the_formal_default(monkeypatch):
    """``USE_ROBUST_INDICATORS_PERCENT`` defaults to 100 and rollback to False."""
    # Clear any env-var influence so we observe the dataclass default.
    for var in ("USE_ROBUST_INDICATORS_PERCENT", "LEGACY_PIPELINE_ROLLBACK"):
        monkeypatch.delenv(var, raising=False)
    fresh = Settings()
    assert fresh.USE_ROBUST_INDICATORS_PERCENT == 100
    assert fresh.LEGACY_PIPELINE_ROLLBACK is False


# ── is_legacy_rollback_active ───────────────────────────────────────────────


def test_rollback_inactive_by_default():
    assert is_legacy_rollback_active() is False


def test_rollback_via_settings(monkeypatch):
    from app.services.robust_indicators import bucketing as bucketing_mod
    monkeypatch.setattr(
        bucketing_mod.settings, "LEGACY_PIPELINE_ROLLBACK", True, raising=False
    )
    assert is_legacy_rollback_active() is True


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("True", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_rollback_env_var_truthy_parsing(monkeypatch, raw: str, expected: bool):
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", raw)
    assert is_legacy_rollback_active() is expected


# ── should_use_robust hot path ──────────────────────────────────────────────


def test_should_use_robust_default_true():
    assert should_use_robust("BTC_USDT") is True
    assert should_use_robust("eth_usdt") is True


def test_should_use_robust_short_circuits_under_rollback(monkeypatch):
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "1")
    assert should_use_robust("BTC_USDT") is False


# ── select_authoritative_score under rollback ───────────────────────────────


_LEGACY_PRICE = 67.25


def test_select_score_robust_path_when_rollback_off():
    res = select_authoritative_score(
        "BTC_USDT",
        {"rsi": 60.0, "adx": 28.0, "macd_histogram": 0.1,
         "ema9": 100.0, "ema50": 95.0, "ema200": 90.0,
         "taker_ratio": 0.6, "buy_pressure": 0.55,
         "volume_delta": 100.0, "taker_source": "merged",
         "atr_pct": 1.0, "close": 100.0,
         "macd": 0.4, "macd_signal_line": 0.3,
         "taker_buy_volume": 600.0, "taker_sell_volume": 400.0},
        legacy_score=_LEGACY_PRICE,
        score_config={"scoring_rules": [
            {"id": "rsi", "indicator": "rsi", "operator": ">=",
             "value": 50, "points": 10, "category": "momentum"},
        ]},
    )
    assert res.engine_tag == "robust"
    assert res.bucketed is True
    assert res.score is not None
    assert silent_fallback_snapshot().get("legacy_rollback", 0) == 0


def test_select_score_returns_legacy_under_rollback(monkeypatch):
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    res = select_authoritative_score(
        "BTC_USDT", {"rsi": 60.0},
        legacy_score=_LEGACY_PRICE,
        score_config={"scoring_rules": []},
    )
    assert res.engine_tag == "legacy"
    assert res.bucketed is False
    assert res.score == pytest.approx(_LEGACY_PRICE)
    assert res.fell_back is True
    assert res.fallback_reason == "legacy_rollback"
    assert silent_fallback_snapshot().get("legacy_rollback", 0) == 1


def test_rollback_short_circuit_ignores_indicators_and_percent(monkeypatch):
    """Even with healthy indicators + percent=100 the rollback still wins."""
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "1")
    res = select_authoritative_score(
        "BTC_USDT",
        {"rsi": 60.0, "adx": 28.0, "macd_histogram": 0.1},
        legacy_score=_LEGACY_PRICE,
        score_config={"scoring_rules": []},
        percent=100,
    )
    assert res.engine_tag == "legacy"
    assert res.fallback_reason == "legacy_rollback"


# ── Phase 3 exclusivity: legacy is unreachable outside rollback ────────────


def test_legacy_unreachable_when_rollback_off_missing_indicators():
    """Robust failures outside rollback MUST yield a robust-tagged sentinel.

    The legacy engine is on standby and is not allowed to be re-introduced
    as a routine fallback for missing-indicator paths.
    """
    res = select_authoritative_score(
        "BTC_USDT", {},
        legacy_score=_LEGACY_PRICE,
        score_config={"scoring_rules": []},
    )
    assert res.engine_tag == "robust"
    assert res.score is None
    assert res.fell_back is False
    # Reason is preserved purely for telemetry.
    assert res.fallback_reason == "missing_indicators"


def test_legacy_unreachable_when_rollback_off_compute_failure(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("envelope blew up")

    monkeypatch.setattr(
        "app.services.robust_indicators.select_score.envelope_indicators",
        _boom,
    )
    res = select_authoritative_score(
        "BTC_USDT", {"rsi": 60.0, "adx": 28.0},
        legacy_score=_LEGACY_PRICE,
        score_config={"scoring_rules": []},
    )
    assert res.engine_tag == "robust"
    assert res.score is None
    assert res.fell_back is False
    assert res.fallback_reason == "compute_failed"


def test_legacy_unreachable_when_rollback_off_empty_symbol():
    res = select_authoritative_score(
        "", {"rsi": 60.0},
        legacy_score=_LEGACY_PRICE,
        score_config={"scoring_rules": []},
    )
    assert res.engine_tag == "robust"
    assert res.score is None


# ── evaluate_signals Phase 3 contract: legacy is never used outside rollback ─


def _fake_sel(engine_tag, score, fallback_reason=None):
    """Build a minimal SelectScoreResult-shaped object for unit tests."""
    from app.services.robust_indicators.select_score import SelectScoreResult
    return SelectScoreResult(
        score=score,
        engine_tag=engine_tag,
        bucketed=True,
        fell_back=(engine_tag == "legacy"),
        fallback_reason=fallback_reason,
    )


def test_resolve_signal_score_uses_robust_value_when_available():
    from app.tasks.evaluate_signals import _resolve_signal_score

    def selector(symbol, indicators, *, legacy_score, flow_source_hint=None):
        return _fake_sel("robust", 82.5)

    out = _resolve_signal_score(
        "BTC_USDT", {"close": 50000.0}, legacy_alpha_score=70.0,
        selector=selector,
    )
    assert out == pytest.approx(82.5)


def test_resolve_signal_score_skips_on_robust_sentinel_outside_rollback():
    """Phase 3 contract: a robust-tagged sentinel must NEVER cause the
    candidate to fall through with the legacy alpha_score. Returning
    None tells the caller to skip the candidate entirely.
    """
    from app.tasks.evaluate_signals import _resolve_signal_score

    captured = {}

    def selector(symbol, indicators, *, legacy_score, flow_source_hint=None):
        captured["legacy_score"] = legacy_score
        return _fake_sel("robust", None, fallback_reason="missing_indicators")

    out = _resolve_signal_score(
        "BTC_USDT", {"close": 50000.0}, legacy_alpha_score=70.0,
        selector=selector,
    )
    assert out is None
    # The selector saw the legacy score (so it could decide), but the
    # caller MUST NOT substitute it as the signal alpha_score.
    assert captured["legacy_score"] == pytest.approx(70.0)


def test_resolve_signal_score_uses_legacy_only_when_engine_tag_legacy():
    """The only way ``_resolve_signal_score`` returns a legacy value is
    when the selector itself emitted ``engine_tag="legacy"`` — which
    only happens while ``LEGACY_PIPELINE_ROLLBACK`` is active.
    """
    from app.tasks.evaluate_signals import _resolve_signal_score

    def selector(symbol, indicators, *, legacy_score, flow_source_hint=None):
        # Simulate the rollback short-circuit returning legacy.
        return _fake_sel("legacy", 70.0, fallback_reason="legacy_rollback")

    out = _resolve_signal_score(
        "BTC_USDT", {"close": 50000.0}, legacy_alpha_score=70.0,
        selector=selector,
    )
    assert out == pytest.approx(70.0)


def test_resolve_signal_score_real_selector_skips_missing_indicators():
    """Smoke-test using the real selector — empty indicators outside
    rollback yields a sentinel and ``_resolve_signal_score`` returns
    ``None`` (skip), not the legacy alpha_score.
    """
    from app.tasks.evaluate_signals import _resolve_signal_score

    out = _resolve_signal_score(
        "BTC_USDT", {}, legacy_alpha_score=70.0,
    )
    assert out is None


def test_resolve_signal_score_real_selector_uses_legacy_under_rollback(monkeypatch):
    """Smoke-test using the real selector — under rollback the legacy
    alpha_score is returned exactly as the operator requested.
    """
    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    from app.tasks.evaluate_signals import _resolve_signal_score

    out = _resolve_signal_score(
        "BTC_USDT", {"close": 50000.0}, legacy_alpha_score=70.0,
    )
    assert out == pytest.approx(70.0)


# ── Admin status: any single unsafe day flips the top-level alert ──────────


def test_seven_day_trend_aggregates_alert_when_any_single_day_unsafe(monkeypatch):
    """Phase 3 fail-loud contract: even when the 7-day aggregate stays
    inside the pre-flight bounds, a single unsafe day must surface a
    top-level ``summary.alert`` with the day-level reason. Aggregating
    drift away into a 7-day average would let a bad day hide.
    """
    from datetime import datetime, timezone, timedelta
    from types import SimpleNamespace
    from app.api import admin_robust_indicators as mod

    today = datetime.now(timezone.utc).replace(microsecond=0)

    # Build 7 day-rows: 6 healthy days + 1 unsafe day. The aggregate
    # must stay safe so we know the alert is driven by the day-level
    # check, not the summary thresholds.
    healthy_rows = [
        SimpleNamespace(
            day=today - timedelta(days=i + 1),
            total=1000,
            rejected=10,            # 1% rejection — well under threshold
            divergent_high=10,      # 1% divergence — well under threshold
            avg_confidence=0.85,    # well above min
        )
        for i in range(6)
    ]
    # One unsafe day with high rejection rate but low traffic, so the
    # aggregate barely shifts.
    bad_day = SimpleNamespace(
        day=today,
        total=20,
        rejected=20,                # 100% rejection — clearly unsafe
        divergent_high=0,
        avg_confidence=0.85,
    )

    rows = [bad_day, *healthy_rows]

    class _FakeResult:
        def fetchall(self):
            return rows

    class _FakeDb:
        async def execute(self, *_args, **_kw):
            return _FakeResult()

    out = asyncio.run(mod._seven_day_trend(_FakeDb()))

    summary = out["summary"]
    # Aggregate should be safely under thresholds.
    assert summary["rejection_rate"] < mod.REJECTION_RATE_MAX
    # But the top-level alert must still fire because of the bad day.
    assert summary["alert"] is True
    assert summary["unsafe_day_count"] >= 1
    # And the alert reasons must mention the unsafe day's metric.
    joined = " | ".join(summary["alert_reasons"])
    assert "rejection_rate" in joined


def test_seven_day_trend_no_alert_when_all_days_safe(monkeypatch):
    """Sanity check: with every day safe AND the aggregate safe, no
    top-level alert fires.
    """
    from datetime import datetime, timezone, timedelta
    from types import SimpleNamespace
    from app.api import admin_robust_indicators as mod

    today = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [
        SimpleNamespace(
            day=today - timedelta(days=i),
            total=1000,
            rejected=10,
            divergent_high=10,
            avg_confidence=0.85,
        )
        for i in range(7)
    ]

    class _FakeResult:
        def fetchall(self):
            return rows

    class _FakeDb:
        async def execute(self, *_args, **_kw):
            return _FakeResult()

    out = asyncio.run(mod._seven_day_trend(_FakeDb()))

    assert out["summary"]["alert"] is False
    assert out["summary"]["alert_reasons"] == []
    assert out["summary"]["unsafe_day_count"] == 0


# ── pipeline_scan rollout exception is fail-closed under Phase 3 ───────────


def test_apply_robust_authoritative_scoring_zeroes_sentinel_assets(monkeypatch):
    """The consumer in pipeline_scan must zero out asset score columns
    when the selector returns the Phase 3 sentinel — otherwise a
    pre-existing legacy numeric value gets persisted under the
    ``robust`` engine tag, which is exactly what Phase 3 forbids.
    """
    from app.tasks.pipeline_scan import _apply_robust_authoritative_scoring

    # Pre-existing legacy numbers that must NOT survive the rollout.
    assets = [
        {
            "symbol": "BTC_USDT",
            "indicators": {},  # empty → sentinel path
            "_score": 70.0,
            "score": 70.0,
            "alpha_score": 70.0,
        }
    ]
    counters = _apply_robust_authoritative_scoring(
        assets, score_config={"scoring_rules": []}, is_futures=False
    )
    assert counters["fallbacks"] == 1
    asset = assets[0]
    assert asset["engine_tag"] == "robust"
    assert asset["_score"] == 0.0
    assert asset["score"] == 0.0
    assert asset["alpha_score"] == 0.0


def test_evaluate_signals_candidate_query_no_legacy_prefilter_outside_rollback():
    """Phase 3 contract: outside rollback, the candidate-selection
    SQL in ``evaluate_signals`` MUST NOT prefilter on the legacy
    ``alpha_scores.score`` column. Symbol eligibility is decided by
    indicators + the robust selector, not by legacy values.

    We can't run the live SQL here without a database, so we assert
    the source-level contract: the rollback branch reads the legacy
    score gate; the non-rollback branch does not.
    """
    import inspect
    from app.tasks import evaluate_signals as es
    src = inspect.getsource(es._evaluate_async)

    # The rollback branch keeps the legacy gate (operator's request).
    assert "if rollback_on:" in src
    assert "a.score >= 60" in src

    # The non-rollback branch must:
    #   1. NOT include any legacy score predicate
    #   2. SELECT FROM indicators i (not FROM alpha_scores a)
    #   3. LEFT JOIN alpha_scores so the column is informational only
    rollback_marker = "if rollback_on:"
    else_marker = "else:"
    rb_idx = src.find(rollback_marker)
    else_idx = src.find(else_marker, rb_idx)
    assert rb_idx > 0 and else_idx > rb_idx

    non_rollback = src[else_idx:else_idx + 1500]
    assert "FROM indicators i" in non_rollback
    assert "LEFT JOIN alpha_scores" in non_rollback
    # Critical: no legacy-score gate on the non-rollback path.
    assert "a.score >=" not in non_rollback


def test_execute_buy_candidate_query_no_legacy_prefilter_outside_rollback():
    """Same contract for ``execute_buy``: outside rollback, the
    candidate-selection SQL must not gate on or order by legacy
    ``alpha_scores.score``."""
    import inspect
    from app.tasks import execute_buy as eb
    src = inspect.getsource(eb)

    # Rollback branch keeps the threshold predicate (operator's request).
    assert "if rollback_on:" in src
    assert "a.score >= :threshold" in src

    # Non-rollback branch (the indicators-first query)
    non_rollback_block = src[
        src.find("# Robust authority: pull every recent-indicator"):
        src.find("candidates = ranked_res.fetchall()")
    ]
    assert "FROM indicators i" in non_rollback_block
    assert "LEFT JOIN alpha_scores" in non_rollback_block
    assert "a.score >=" not in non_rollback_block
    # Outside rollback the legacy-score-DESC ordering is also dropped
    # (we can't rank by a column we no longer trust).
    assert "ORDER BY i.symbol, i.time DESC" in non_rollback_block

    # And the per-row loop runs through _resolve_signal_score so the
    # robust selector is the actual authority.
    assert "_resolve_signal_score" in src


def test_robust_futures_direction_bias_pure_indicator_signal():
    """The direction helper is computed from indicator envelopes
    alone — no legacy ``score_long`` / ``score_short`` /
    ``confidence_score`` ever influence its output."""
    from app.tasks.pipeline_scan import _robust_futures_direction_bias

    # Strong long: every indicator votes long.
    long_ind = {
        "ema9_gt_ema50": True,
        "ema50_gt_ema200": True,
        "macd_histogram": 1.5,
        "rsi": 68.0,
    }
    assert _robust_futures_direction_bias(long_ind) == 1.0

    # Strong short.
    short_ind = {
        "ema9_gt_ema50": False,
        "ema50_gt_ema200": False,
        "macd_histogram": -0.8,
        "rsi": 32.0,
    }
    assert _robust_futures_direction_bias(short_ind) == -1.0

    # Mixed → bias near zero.
    mixed = {
        "ema9_gt_ema50": True,
        "ema50_gt_ema200": False,
        "macd_histogram": 0.0,   # abstains
        "rsi": 50.0,             # neutral zone — abstains
    }
    assert _robust_futures_direction_bias(mixed) == 0.0

    # Empty / non-dict → 0.0
    assert _robust_futures_direction_bias({}) == 0.0
    assert _robust_futures_direction_bias(None) == 0.0  # type: ignore[arg-type]

    # Envelope-shaped values ({"value": ...}) are unwrapped.
    enveloped = {
        "ema9_gt_ema50": {"value": True},
        "ema50_gt_ema200": {"value": True},
        "macd_histogram": {"value": 0.5},
        "rsi": {"value": 60.0},
    }
    assert _robust_futures_direction_bias(enveloped) == 1.0

    # CRITICAL invariant: legacy score_long / score_short keys must
    # NOT influence the bias. Feed wildly biased "legacy" values and
    # confirm the indicator-only result is unchanged.
    contaminated = dict(long_ind)
    contaminated.update({
        "score_long": 99.9,
        "score_short": 0.1,
        "confidence_score": 99.9,
    })
    assert _robust_futures_direction_bias(contaminated) == 1.0


def test_futures_output_independent_of_legacy_score_state(monkeypatch):
    """Phase 3 contract: outside rollback, the robust authoritative
    output for futures must NOT depend on the previously-stored
    legacy ``score_long`` / ``score_short`` / ``confidence_score``
    columns. We run the same indicators twice with wildly different
    legacy state and assert identical robust output."""
    from app.tasks.pipeline_scan import _apply_robust_authoritative_scoring

    monkeypatch.delenv("LEGACY_PIPELINE_ROLLBACK", raising=False)

    indicators = {
        "rsi": 60.0,
        "adx": 25.0,
        "macd": 0.5,
        "macd_histogram": 0.3,
        "ema50": 1.0,
        "ema9_gt_ema50": True,
        "ema50_gt_ema200": True,
    }

    asset_a = {
        "symbol": "BTC_USDT",
        "indicators": dict(indicators),
        "_score": 50.0,
        "score_long": 99.9,        # contaminated legacy state
        "score_short": 0.1,
        "confidence_score": 99.9,
    }
    asset_b = {
        "symbol": "BTC_USDT",
        "indicators": dict(indicators),
        "_score": 50.0,
        "score_long": 0.1,         # opposite contamination
        "score_short": 99.9,
        "confidence_score": 0.1,
    }

    _apply_robust_authoritative_scoring(
        [asset_a], score_config={"scoring_rules": []}, is_futures=True
    )
    _apply_robust_authoritative_scoring(
        [asset_b], score_config={"scoring_rules": []}, is_futures=True
    )

    # Same indicators in, same robust output out — regardless of the
    # legacy state we threw at it.
    assert asset_a["confidence_score"] == asset_b["confidence_score"]
    assert asset_a["score_long"] == asset_b["score_long"]
    assert asset_a["score_short"] == asset_b["score_short"]
    assert asset_a["_score"] == asset_b["_score"]
    assert asset_a["alpha_score"] == asset_b["alpha_score"]
    # And the engine is correctly tagged robust.
    assert asset_a["engine_tag"] == "robust"
    assert asset_b["engine_tag"] == "robust"


def test_apply_robust_authoritative_scoring_counter_semantics_for_sentinel():
    """Sentinel assets are accounted as ``fallbacks`` only — never
    ``robust_used``. The invariant is:

        robust_used + fallbacks + legacy == count of bucketed-or-legacy assets

    With sentinels included only in ``fallbacks`` the rollout summary
    reflects how many assets actually ran end-to-end vs how many had
    no robust score to apply.
    """
    from app.tasks.pipeline_scan import _apply_robust_authoritative_scoring

    assets = [
        # Sentinel — empty indicators
        {"symbol": "BTC_USDT", "indicators": {}, "_score": 70.0},
        # Sentinel — empty indicators
        {"symbol": "ETH_USDT", "indicators": {}, "_score": 65.0},
    ]
    counters = _apply_robust_authoritative_scoring(
        assets, score_config={"scoring_rules": []}, is_futures=False
    )
    # Both are bucketed (Phase 3 default) and both are sentinels.
    assert counters["bucketed"] == 2
    assert counters["fallbacks"] == 2
    assert counters["robust_used"] == 0   # the bug being guarded against
    assert counters["legacy"] == 0
    # Invariant holds.
    assert (
        counters["robust_used"] + counters["fallbacks"] + counters["legacy"]
        == counters["bucketed"] + counters["legacy"]
    )


def test_apply_robust_authoritative_scoring_zeroes_sentinel_assets_futures(monkeypatch):
    from app.tasks.pipeline_scan import _apply_robust_authoritative_scoring

    assets = [
        {
            "symbol": "BTC_USDT",
            "indicators": {},
            "_score": 70.0,
            "score": 70.0,
            "alpha_score": 70.0,
            "confidence_score": 72.0,
            "score_long": 65.0,
            "score_short": 35.0,
        }
    ]
    counters = _apply_robust_authoritative_scoring(
        assets, score_config={"scoring_rules": []}, is_futures=True
    )
    assert counters["fallbacks"] == 1
    asset = assets[0]
    assert asset["engine_tag"] == "robust"
    assert asset["confidence_score"] == 0.0
    assert asset["score_long"] == 0.0
    assert asset["score_short"] == 0.0


# ── Standby check production gating ─────────────────────────────────────────


def test_standby_alerts_enabled_defaults_to_production(monkeypatch):
    from app.tasks import robust_alerts

    for var in ("ROBUST_ALERTS_ENVIRONMENT", "APP_ENV", "ENVIRONMENT", "ENV",
                "ROBUST_ALERTS_FORCE_STANDBY"):
        monkeypatch.delenv(var, raising=False)
    assert robust_alerts._standby_check_environment() == "production"
    assert robust_alerts._standby_alerts_enabled() is True


@pytest.mark.parametrize("env_value", ["staging", "dev", "test", "local"])
def test_standby_alerts_disabled_in_non_production(monkeypatch, env_value):
    from app.tasks import robust_alerts

    monkeypatch.delenv("ROBUST_ALERTS_FORCE_STANDBY", raising=False)
    for var in ("APP_ENV", "ENVIRONMENT", "ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROBUST_ALERTS_ENVIRONMENT", env_value)
    assert robust_alerts._standby_alerts_enabled() is False


def test_standby_alerts_force_override_in_non_production(monkeypatch):
    """ROBUST_ALERTS_FORCE_STANDBY=true re-enables alerts (e.g. for a
    staging fire-drill)."""
    from app.tasks import robust_alerts

    monkeypatch.setenv("ROBUST_ALERTS_ENVIRONMENT", "staging")
    monkeypatch.setenv("ROBUST_ALERTS_FORCE_STANDBY", "true")
    assert robust_alerts._standby_alerts_enabled() is True


def test_standby_check_skips_alert_in_non_production(monkeypatch):
    """End-to-end: with rollback active >24h in a non-prod env, the
    standby check records the report but does NOT page ops."""
    from app.tasks import robust_alerts

    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    monkeypatch.setenv("ROBUST_ALERTS_ENVIRONMENT", "staging")
    monkeypatch.delenv("ROBUST_ALERTS_FORCE_STANDBY", raising=False)

    fake_redis = _FakeRedis()
    twenty_five_hours_ago = (
        __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).timestamp() - 25 * 60 * 60
    )
    fake_redis.store[robust_alerts._ROLLBACK_FIRST_SEEN_KEY] = str(twenty_five_hours_ago)
    monkeypatch.setattr(
        "app.services.config_service._make_redis_client", lambda: fake_redis
    )

    sent = []

    async def _capture(msg):
        sent.append(msg)

    monkeypatch.setattr(robust_alerts, "_send_ops_alert", _capture)

    report = asyncio.run(robust_alerts._check_legacy_rollback_standby_async())
    assert report["rollback_active"] is True
    assert report["fired"] is False
    assert report.get("skipped") == "non_production"
    assert sent == []


# ── No global mutation in the rollback rate-limit path ──────────────────────


def test_standby_check_does_not_mutate_global_rate_limit(monkeypatch):
    """Regression guard: the standby check must use the per-call ttl
    parameter and leave ``_RATE_LIMIT_SECONDS`` untouched, otherwise the
    high-frequency sustained-condition alerts would race the standby
    alert's 6-hour window in the same worker process.
    """
    from app.tasks import robust_alerts

    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    fake_redis = _FakeRedis()
    # Pre-seed >24h so the alert path runs.
    from datetime import datetime, timezone
    twenty_five_hours_ago = (
        datetime.now(timezone.utc).timestamp() - 25 * 60 * 60
    )
    fake_redis.store[robust_alerts._ROLLBACK_FIRST_SEEN_KEY] = str(twenty_five_hours_ago)

    monkeypatch.setattr(
        "app.services.config_service._make_redis_client", lambda: fake_redis
    )

    async def _noop(msg):
        return None

    monkeypatch.setattr(robust_alerts, "_send_ops_alert", _noop)

    original = robust_alerts._RATE_LIMIT_SECONDS
    asyncio.run(robust_alerts._check_legacy_rollback_standby_async())
    assert robust_alerts._RATE_LIMIT_SECONDS == original


# ── daily standby check ─────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal async Redis stub honouring SET ... NX, GET, DELETE."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


def test_standby_check_no_op_when_rollback_inactive(monkeypatch):
    from app.tasks import robust_alerts

    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.config_service._make_redis_client", lambda: fake_redis
    )
    sent: list[str] = []

    async def _capture(msg):
        sent.append(msg)

    monkeypatch.setattr(robust_alerts, "_send_ops_alert", _capture)

    report = asyncio.run(robust_alerts._check_legacy_rollback_standby_async())
    assert report["rollback_active"] is False
    assert report["fired"] is False
    assert sent == []
    # The first-seen key must NOT be set when the rollback is inactive.
    assert robust_alerts._ROLLBACK_FIRST_SEEN_KEY not in fake_redis.store


def test_standby_check_records_first_seen_but_does_not_fire_under_24h(monkeypatch):
    from app.tasks import robust_alerts

    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "app.services.config_service._make_redis_client", lambda: fake_redis
    )
    sent: list[str] = []

    async def _capture(msg):
        sent.append(msg)

    monkeypatch.setattr(robust_alerts, "_send_ops_alert", _capture)

    report = asyncio.run(robust_alerts._check_legacy_rollback_standby_async())
    assert report["rollback_active"] is True
    assert report["fired"] is False
    assert sent == []
    assert robust_alerts._ROLLBACK_FIRST_SEEN_KEY in fake_redis.store


def test_standby_check_fires_after_24h(monkeypatch):
    from app.tasks import robust_alerts

    monkeypatch.setenv("LEGACY_PIPELINE_ROLLBACK", "true")
    fake_redis = _FakeRedis()
    # Pre-seed first-seen 25 hours ago.
    from datetime import datetime, timezone
    twenty_five_hours_ago = (
        datetime.now(timezone.utc).timestamp() - 25 * 60 * 60
    )
    fake_redis.store[robust_alerts._ROLLBACK_FIRST_SEEN_KEY] = str(twenty_five_hours_ago)

    monkeypatch.setattr(
        "app.services.config_service._make_redis_client", lambda: fake_redis
    )
    sent: list[str] = []

    async def _capture(msg):
        sent.append(msg)

    monkeypatch.setattr(robust_alerts, "_send_ops_alert", _capture)

    report = asyncio.run(robust_alerts._check_legacy_rollback_standby_async())
    assert report["rollback_active"] is True
    assert report["fired"] is True
    assert len(sent) == 1
    assert "LEGACY_PIPELINE_ROLLBACK" in sent[0]


def test_standby_check_clears_first_seen_when_rollback_unset(monkeypatch):
    from app.tasks import robust_alerts

    fake_redis = _FakeRedis()
    fake_redis.store[robust_alerts._ROLLBACK_FIRST_SEEN_KEY] = "1234.0"

    monkeypatch.setattr(
        "app.services.config_service._make_redis_client", lambda: fake_redis
    )
    sent: list[str] = []

    async def _capture(msg):
        sent.append(msg)

    monkeypatch.setattr(robust_alerts, "_send_ops_alert", _capture)

    report = asyncio.run(robust_alerts._check_legacy_rollback_standby_async())
    assert report["rollback_active"] is False
    assert report["fired"] is False
    assert robust_alerts._ROLLBACK_FIRST_SEEN_KEY not in fake_redis.store
