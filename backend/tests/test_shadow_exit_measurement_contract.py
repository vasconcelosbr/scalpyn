from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.tasks.shadow_trade_monitor import _finalize_outcome


BASE = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _shadow() -> SimpleNamespace:
    return SimpleNamespace(
        amount_usdt=100.0,
        entry_timestamp=BASE,
        min_price_post_entry=None,
        max_price_post_entry=None,
        mae_pct=None,
        mfe_pct=None,
        max_drawdown_pct=None,
        max_profit_pct=None,
        barrier_touched=None,
        barrier_touched_at=None,
        pnl_pct=None,
        config_snapshot={"ml_fee_roundtrip_pct": 0.2},
        ttt_enabled=False,
        eligible_for_training=False,
    )


def test_sl_observed_sample_preserves_nominal_and_signed_overshoot() -> None:
    shadow = _shadow()
    _finalize_outcome(
        shadow,
        "SL_HIT",
        98.703,
        BASE + timedelta(minutes=1),
        100.0,
        exit_price_nominal=99.0,
        exit_price_observed=98.703,
        exit_price_semantics="OBSERVED_SAMPLE_TRIGGER",
        closure_path="regular_batch",
    )

    assert shadow.exit_price == pytest.approx(98.703)
    assert shadow.exit_price_nominal == pytest.approx(99.0)
    assert shadow.exit_price_observed == pytest.approx(98.703)
    assert shadow.barrier_overshoot_pct == pytest.approx(-0.3)


def test_tp_observed_sample_preserves_positive_overshoot() -> None:
    shadow = _shadow()
    _finalize_outcome(
        shadow,
        "TP_HIT",
        101.303,
        BASE + timedelta(minutes=1),
        100.0,
        exit_price_nominal=101.0,
        exit_price_observed=101.303,
        exit_price_semantics="OBSERVED_SAMPLE_TRIGGER",
        closure_path="regular_batch",
    )

    assert shadow.exit_price == pytest.approx(101.303)
    assert shadow.barrier_overshoot_pct == pytest.approx(0.3)


def test_intrabar_touch_does_not_invent_unobservable_overshoot() -> None:
    shadow = _shadow()
    _finalize_outcome(
        shadow,
        "SL_HIT",
        99.0,
        BASE + timedelta(minutes=1),
        100.0,
        exit_price_nominal=99.0,
        exit_price_observed=None,
        exit_price_semantics="INTRABAR_TOUCH_NOMINAL",
        closure_path="regular_batch",
    )

    assert shadow.exit_price == pytest.approx(99.0)
    assert shadow.barrier_overshoot_pct is None
    assert shadow.exit_price_semantics == "INTRABAR_TOUCH_NOMINAL"
