import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.crypto_ev_config import default_crypto_ev_config
from backend.app.services.crypto_ev_score_service import (
    CryptoEVScoreService,
    CryptoEVTrade,
    normalize_crypto_ev_config,
    normalize_ev_to_score,
    resolve_crypto_ev_window,
    replay_l3_filters_from_snapshot,
    resolve_atr_bucket,
    resolve_state,
    shrink_ev,
)


def test_crypto_ev_normalization_maps_configured_range_to_score():
    config = default_crypto_ev_config()

    assert normalize_ev_to_score(-0.010, config) == 0.0
    assert normalize_ev_to_score(0.0, config) == 50.0
    assert normalize_ev_to_score(0.010, config) == 100.0
    assert normalize_ev_to_score(0.020, config) == 100.0


def test_crypto_ev_shrinkage_uses_empirical_bayes_weight():
    w, shrunk = shrink_ev(ev_symbol=0.010, ev_prior=-0.002, n=30, k=30)

    assert w == 0.5
    assert shrunk == 0.004


def test_crypto_ev_state_is_insufficient_until_minimum_sample():
    config = default_crypto_ev_config()

    assert resolve_state(90.0, n=14, previous_state=None, config=config) == "INSUFFICIENT_DATA"
    assert resolve_state(66.0, n=15, previous_state=None, config=config) == "FAVORABLE"
    assert resolve_state(61.0, n=15, previous_state="FAVORABLE", config=config) == "FAVORABLE"
    assert resolve_state(59.0, n=15, previous_state="FAVORABLE", config=config) == "NEUTRAL"


def test_crypto_ev_atr_bucket_uses_configured_boundaries():
    config = default_crypto_ev_config()
    buckets = config["atr_buckets"]

    assert resolve_atr_bucket(0.9, buckets) == "LOW"
    assert resolve_atr_bucket(1.5, buckets) == "MID"
    assert resolve_atr_bucket(3.0, buckets) == "HIGH"


def test_crypto_ev_current_month_window_starts_on_first_day_utc():
    config = default_crypto_ev_config()
    now = datetime(2026, 7, 8, 15, 30, tzinfo=timezone.utc)

    policy, window_start, window_end, window_hours = resolve_crypto_ev_window(config, now=now)

    assert policy == "current_month_to_date"
    assert window_start == datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    assert window_end == now
    assert window_hours == 184


def test_crypto_ev_legacy_config_defaults_to_spectrum_operational_view():
    legacy_config = default_crypto_ev_config()
    legacy_config.pop("window_policy")
    legacy_config["views"] = {"operational_view": "executable"}

    normalized = normalize_crypto_ev_config(legacy_config)

    assert normalized["window_policy"] == "current_month_to_date"
    assert normalized["views"]["operational_view"] == "spectrum"


def test_crypto_ev_l3_replay_is_fail_closed_without_l3_context():
    passed, reason = replay_l3_filters_from_snapshot({"atr_pct": 1.2, "rsi": 55}, {})

    assert passed is None
    assert reason == "missing_l3_snapshot_context"


def test_crypto_ev_executable_excludes_unreplayable_without_mapping_to_false():
    config = default_crypto_ev_config()
    config["min_trades_for_state"] = 1
    service = CryptoEVScoreService()
    trades = [
        CryptoEVTrade(
            shadow_trade_id="t1",
            symbol="BTC_USDT",
            created_at="2026-07-08T00:00:00Z",
            net_return_decimal=0.010,
            atr_pct=1.0,
            would_pass_l3=True,
            replay_status="PASSED",
            l3_config_version="l3v",
        ),
        CryptoEVTrade(
            shadow_trade_id="t2",
            symbol="BTC_USDT",
            created_at="2026-07-08T01:00:00Z",
            net_return_decimal=-0.010,
            atr_pct=1.0,
            would_pass_l3=None,
            replay_status="UNREPLAYABLE",
            l3_config_version="l3v",
        ),
    ]

    snapshots = service._build_snapshots(
        trades=trades,
        config=config,
        config_version="cfg",
        previous_states={},
        ml_health={"healthy": False, "reason": "disabled"},
        window_policy="current_month_to_date",
        window_start=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 8, 1, 0, tzinfo=timezone.utc),
        effective_window_hours=170,
    )

    executable = next(item for item in snapshots if item["symbol"] == "BTC_USDT" and item["view"] == "executable")
    assert executable["n_trades"] == 1
    assert executable["n_excluded_unreplayable"] == 1
    assert executable["state"] == "INSUFFICIENT_DATA"
    assert executable["window_hours"] == 170
    assert executable["audit_json"]["window_policy"] == "current_month_to_date"
    assert executable["audit_json"]["window_start"] == "2026-07-01T00:00:00+00:00"
