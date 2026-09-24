import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.signal_engine import SignalEngine
from app.tasks.compute_indicators import _compute_score_fields


# ── 2026-09-24: "score" field name collision ────────────────────────────────
#
# Dozens of L3 profiles reference `field: "score"` in their `signals`
# conditions (e.g. "score >= 65"), expecting the robust-engine Alpha Score.
# But `compute_indicators._compute_score_fields` wrote an unrelated 6-criterion
# heuristic (RSI zone, EMA9>EMA21, volume spike, ATR%, MACD sign, price>VWAP)
# into `indicators_json` under the SAME "score" key, silently shadowing the
# intended value — no "aguardando coleta" warning, just a wrong number in the
# right range. Fixed by renaming the heuristic's output keys to
# "quick_score"/"quick_score_raw"/"quick_score_max", and making
# SignalEngine.evaluate() alias "score" to the real alpha_score it already
# receives as a parameter.


def test_compute_score_fields_no_longer_writes_bare_score_keys():
    """The heuristic must not reuse "score"/"score_raw"/"score_max" — those
    names are reserved for the real Alpha Score everywhere else in the
    profile config surface."""
    fields = _compute_score_fields({
        "rsi": 55, "ema9": 10, "ema21": 9, "volume_spike": 2.0,
        "atr_pct": 1.0, "macd_signal": "positive", "price": 11, "vwap": 10,
    })

    assert set(fields.keys()) == {"quick_score", "quick_score_raw", "quick_score_max"}
    assert fields["quick_score"] == 100.0


def test_signal_engine_resolves_field_score_to_real_alpha_score():
    """A `field: "score"` condition must be evaluated against the live
    alpha_score passed into evaluate(), not against whatever (if anything)
    happens to be under "score" in the raw indicators dict."""
    engine = SignalEngine({
        "logic": "AND",
        "conditions": [
            {"id": "c1", "field": "score", "operator": ">=", "value": 65, "required": True},
        ],
    })

    # No "score" key in raw indicators at all (post-fix reality) — must still
    # resolve via the alpha_score alias, not SKIP for missing data.
    result = engine.evaluate({"rsi": 55}, alpha_score=70.0)
    assert result["signal"] is True
    assert result["failed_required"] == []

    # Below threshold — must FAIL (prove it's really reading alpha_score,
    # not silently passing because the field can't resolve).
    result_low = engine.evaluate({"rsi": 55}, alpha_score=40.0)
    assert result_low["signal"] is False
    assert "c1" in result_low["failed_required"]


def test_signal_engine_liquidity_and_momentum_score_resolve_when_merged_in():
    """evaluate_signals.py merges alpha_scores.liquidity_score/momentum_score
    into the indicators dict before calling evaluate() (2026-09-24 fix).
    Confirms SignalEngine correctly picks up merged-in component scores
    instead of treating them as always-SKIPPED."""
    engine = SignalEngine({
        "logic": "AND",
        "conditions": [
            {"id": "liq", "field": "liquidity_score", "operator": ">=", "value": 50, "required": False},
            {"id": "mom", "field": "momentum_score", "operator": ">=", "value": 55, "required": False},
        ],
    })

    # Without the merge (component scores absent) — both SKIPPED, no veto.
    skipped = engine.evaluate({"rsi": 55}, alpha_score=70.0)
    assert skipped["signal"] is True
    assert skipped["matched"] == []

    # With the merge applied (mirrors evaluate_signals.py's indicators = {**indicators, **component_scores}) — both resolve and match.
    merged_indicators = {"rsi": 55, "liquidity_score": 60, "momentum_score": 58}
    matched = engine.evaluate(merged_indicators, alpha_score=70.0)
    assert matched["signal"] is True
    assert set(matched["matched"]) == {"liq", "mom"}
