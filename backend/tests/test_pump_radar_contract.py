import pytest
from pydantic import ValidationError

from app.api.pump_radar import router
from app.models.pump_radar import (
    PumpRadarControl,
    PumpRadarEvent,
    PumpRadarEventLink,
    PumpRadarHypothesis,
    PumpRadarIndicatorSnapshot,
    PumpRadarIndicatorValue,
    PumpRadarOHLCV,
    PumpRadarRangeResult,
    PumpRadarRun,
    PumpRadarRunAsset,
)
from app.schemas.pump_radar import PumpRadarConfig
from app.tasks.celery_app import QUEUE_PUMP_RADAR, TASK_ROUTES
from app.tasks.pump_radar import _select_universe


def test_config_is_observation_only_and_digest_is_stable() -> None:
    first = PumpRadarConfig()
    second = PumpRadarConfig.model_validate(first.model_dump())
    assert first.digest() == second.digest()
    assert first.operational_profile_mutation_enabled is False
    with pytest.raises(ValidationError):
        PumpRadarConfig.model_validate({"operational_profile_mutation_enabled": True})


def test_config_rejects_noncanonical_percentiles() -> None:
    with pytest.raises(ValidationError):
        PumpRadarConfig.model_validate({"range_percentiles": [10, 50, 90]})


def test_universe_is_bounded_by_quote_volume_with_stable_ties() -> None:
    tickers = [
        {"currency_pair": "CCC_USDT", "quote_volume": "10"},
        {"currency_pair": "BBB_USDT", "quote_volume": "20"},
        {"currency_pair": "AAA_USDT", "quote_volume": "20"},
        {"currency_pair": "DDD_USDT", "quote_volume": None},
    ]
    members = [{"symbol": item["currency_pair"]} for item in tickers]
    selected = _select_universe(tickers, PumpRadarConfig().universe_max_assets, members)
    assert [item["currency_pair"] for item in selected] == [
        "AAA_USDT", "BBB_USDT", "CCC_USDT", "DDD_USDT"
    ]
    assert [item["currency_pair"] for item in _select_universe(tickers, 2, members)] == [
        "AAA_USDT", "BBB_USDT"
    ]


def test_exchange_outsiders_never_enter_user_universe():
    tickers = [{"currency_pair": "OUT_USDT", "quote_volume": "999999"},
               {"currency_pair": "IN_USDT", "quote_volume": "1"}]
    assert _select_universe(tickers, 100, [{"symbol": "IN/USDT"}]) == [tickers[1]]
    assert _select_universe(tickers, 100, []) == []


def test_config_requires_all_native_context_timeframes():
    with pytest.raises(ValidationError):
        PumpRadarConfig(capture_timeframes=["5m"])


def test_all_tables_are_additive_and_isolated_from_operational_ohlcv() -> None:
    tables = {
        model.__tablename__
        for model in (
            PumpRadarOHLCV, PumpRadarRun, PumpRadarRunAsset, PumpRadarEvent,
            PumpRadarEventLink, PumpRadarIndicatorSnapshot, PumpRadarIndicatorValue,
            PumpRadarControl, PumpRadarRangeResult, PumpRadarHypothesis,
        )
    }
    assert len(tables) == 10
    assert all(name.startswith("pump_radar_") for name in tables)
    assert "ohlcv" not in tables


def test_public_contract_has_no_profile_apply_or_promote_endpoint() -> None:
    methods_by_path = {
        route.path: set(route.methods or set())
        for route in router.routes
    }
    required = {
        "/api/pump-radar/capabilities",
        "/api/pump-radar/config/schema",
        "/api/pump-radar/runs",
        "/api/pump-radar/runs/{run_id}/cancel",
        "/api/pump-radar/runs/{run_id}/assets",
        "/api/pump-radar/runs/{run_id}/events/{event_id}/chart",
        "/api/pump-radar/runs/{run_id}/events/{event_id}/snapshots",
        "/api/pump-radar/runs/{run_id}/comparisons",
        "/api/pump-radar/runs/{run_id}/ranges",
        "/api/pump-radar/runs/{run_id}/profiles",
        "/api/pump-radar/runs/{run_id}/hypotheses",
        "/api/pump-radar/runs/{run_id}/export",
    }
    assert required <= methods_by_path.keys()
    assert all("apply" not in path and "promote" not in path and "activate" not in path for path in methods_by_path)


def test_every_pump_radar_task_uses_isolated_queue() -> None:
    routes = {name: route for name, route in TASK_ROUTES.items() if name.startswith("app.tasks.pump_radar.")}
    assert {name.rsplit(".", 1)[-1] for name in routes} == {
        "inventory", "backfill_asset", "detect_asset", "associate_asset",
        "snapshots", "build_controls", "statistics",
    }
    assert all(route["queue"] == QUEUE_PUMP_RADAR for route in routes.values())
