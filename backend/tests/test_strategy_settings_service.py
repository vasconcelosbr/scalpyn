from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.config_profile import ConfigProfile
from app.schemas.spot_engine_config import SpotEngineConfig
from app.schemas.strategy_settings import MLShadowConfig
from app.services.shadow_trade_service import (
    _apply_barrier_params,
    _shadow_user_config_from_persisted,
)
from app.services.strategy_settings_service import (
    StrategySettingsConflictError,
    StrategySettingsService,
    StrategySettingsValidationError,
)


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _Scalars(self.rows)


class FakeSession:
    def __init__(self, profiles):
        self.profiles = profiles
        self.audits = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        return _Result(list(self.profiles))

    def add(self, value):
        if isinstance(value, ConfigProfile):
            if value.id is None:
                value.id = uuid4()
            self.profiles.append(value)
        else:
            self.audits.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def aggregate():
    user_id = uuid4()
    spot = SpotEngineConfig().model_dump(mode="json")
    strategy = {
        "strategies": [
            {"id": "momentum", "name": "Momentum", "enabled": True, "params": {"adx": 18.0}}
        ]
    }
    ml_shadow = MLShadowConfig().model_dump(mode="json")
    ml = {**ml_shadow, "unrelated_training_key": {"keep": True}}
    profiles = [
        ConfigProfile(
            id=uuid4(), user_id=user_id, pool_id=None, config_type="strategy", config_json=strategy
        ),
        ConfigProfile(
            id=uuid4(), user_id=user_id, pool_id=None, config_type="spot_engine", config_json=spot
        ),
        ConfigProfile(id=uuid4(), user_id=user_id, pool_id=None, config_type="ml", config_json=ml),
    ]
    return user_id, FakeSession(profiles)


@pytest.mark.asyncio
async def test_export_validate_round_trip_has_no_diff(aggregate):
    user_id, db = aggregate
    service = StrategySettingsService()

    exported = await service.get_config(db, user_id)
    validated = await service.validate_import(db, user_id, exported["config"])

    assert validated["valid"] is True
    assert validated["diff"] == []
    assert validated["config"]["source_hash"] == exported["config"]["source_hash"]


@pytest.mark.asyncio
async def test_partial_import_preserves_omitted_and_unrelated_ml_keys(aggregate, monkeypatch):
    user_id, db = aggregate
    service = StrategySettingsService()
    current = await service.get_config(db, user_id)
    before_spot = deepcopy(current["config"]["spot_engine"])
    invalidate = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.strategy_settings_service.config_service.invalidate_cache",
        invalidate,
    )

    result = await service.apply(
        db,
        user_id,
        payload={"ml_shadow": {"ml_fee_roundtrip_pct": 0.25}},
        source_hash=current["config"]["source_hash"],
        change_description="partial test",
        source="JSON_IMPORT",
    )

    ml_profile = next(p for p in db.profiles if p.config_type == "ml")
    assert ml_profile.config_json["ml_fee_roundtrip_pct"] == 0.25
    assert ml_profile.config_json["unrelated_training_key"] == {"keep": True}
    assert result["config"]["spot_engine"] == before_spot
    assert result["changed_config_types"] == ["ml"]
    assert db.commits == 1
    assert len(db.audits) == 1
    invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_optimistic_hash_conflict_rolls_back(aggregate):
    user_id, db = aggregate
    service = StrategySettingsService()

    with pytest.raises(StrategySettingsConflictError):
        await service.apply(
            db,
            user_id,
            payload={"ml_shadow": {"ml_fee_roundtrip_pct": 0.3}},
            source_hash="0" * 64,
            change_description="stale",
            source="FORM",
        )

    assert db.rollbacks == 1
    assert db.commits == 0


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"ml_shadow": {"unknown": 1}}, "Unknown field"),
        (
            {
                "ml_shadow": {
                    "shadow_barrier_mode": "FIXED",
                    "ml_active_barrier_contract_version": "shadow_atr_dynamic_v2",
                }
            },
            "incompatible",
        ),
        (
            {"ml_shadow": {"shadow_barrier_min_pct": 4, "shadow_barrier_max_pct": 3}},
            "less than or equal",
        ),
        ({"spot_engine": {"shadow": {"amount_usdt": -1}}}, "greater than 0"),
        ({"ml_shadow": {"canary_minimum_outcomes": 0}}, "greater than or equal to 1"),
    ],
)
def test_invalid_imports_are_rejected(payload, expected):
    service = StrategySettingsService()
    with pytest.raises(StrategySettingsValidationError, match=expected):
        service.validate_payload(payload, service._default_parts())


def test_new_trade_receives_exact_persisted_runtime_and_open_snapshot_is_immutable():
    spot = SpotEngineConfig().model_dump(mode="json")
    spot["shadow"].update({"amount_usdt": 321.5, "timeout_candles": 77})
    spot["shadow"]["ttt"] = {"enabled": False, "tp_pct": 1.7, "timeout_minutes": 42}
    runtime = _shadow_user_config_from_persisted(spot)
    old_snapshot = deepcopy(runtime)

    ml = MLShadowConfig(ml_fee_roundtrip_pct=0.25).model_dump(mode="json")
    new_snapshot = _apply_barrier_params(deepcopy(runtime), ml)
    spot["shadow"]["amount_usdt"] = 999

    assert new_snapshot["amount_usdt"] == 321.5
    assert new_snapshot["timeout_candles"] == 77
    assert new_snapshot["ttt_enabled"] is False
    assert new_snapshot["ttt_tp_pct"] == 1.7
    assert new_snapshot["ttt_timeout_minutes"] == 42
    assert new_snapshot["ml_fee_roundtrip_pct"] == 0.25
    assert old_snapshot["amount_usdt"] == 321.5


def test_missing_persisted_shadow_block_fails_closed():
    with pytest.raises(ValueError, match="spot_engine.shadow is required"):
        _shadow_user_config_from_persisted({"selling": {}})


def test_shadow_runtime_has_no_removed_business_environment_fallbacks():
    source = (
        Path(__file__).parents[1] / "app" / "services" / "shadow_trade_service.py"
    ).read_text(encoding="utf-8")
    for removed in (
        "SHADOW_TRADE_AMOUNT_USDT",
        "SHADOW_TIMEOUT_CANDLES",
        "TTT_ENABLED_DEFAULT",
        "TTT_TP_PCT_DEFAULT",
        "TTT_TIMEOUT_MINUTES_DEFAULT",
    ):
        assert removed not in source


@pytest.mark.asyncio
async def test_l3_policy_materialization_is_scoped_idempotent_and_read_back(aggregate, monkeypatch):
    user_id, db = aggregate
    service = StrategySettingsService()
    spot_profile = next(p for p in db.profiles if p.config_type == "spot_engine")
    for field in (
        "l3_v3_contract_preserve",
        "l3_condition_status_capture",
        "l3_metrics_provenance",
        "l3_zero_is_value",
        "l3_block_and_skipped_policy",
        "l3_missing_indicator_policy",
        "l3_v3_provenance_resolver",
    ):
        spot_profile.config_json["scanner"].pop(field, None)
    before_non_scanner = deepcopy({
        key: value for key, value in spot_profile.config_json.items() if key != "scanner"
    })
    invalidate = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.strategy_settings_service.config_service.invalidate_cache",
        invalidate,
    )

    dry = await service.materialize_l3_gate_policy(db, user_id, apply=False)
    assert dry["changed"] is True
    assert dry["applied"] is False
    assert db.commits == 0

    applied = await service.materialize_l3_gate_policy(db, user_id, apply=True)
    assert applied["changed"] is True
    assert applied["applied"] is True
    assert applied["runtime_policy"]["l3_v3_contract_preserve"] is True
    assert applied["runtime_policy"]["l3_block_and_skipped_policy"] == "legacy"
    assert applied["runtime_policy"]["l3_missing_indicator_policy"] == "warn"
    resolver = applied["runtime_policy"]["l3_v3_provenance_resolver"]
    assert resolver["enabled"] is False
    assert resolver["profile_allowlist"] == []
    assert resolver["policy_version"] == "l3_v3_provenance_resolver_v1"
    assert set(resolver["source_policies"]) == {
        "ohlcv", "live_trade_flow", "live_order_book", "decision_context",
    }
    assert all(
        policy["max_age_seconds"] is None
        for policy in resolver["source_policies"].values()
    )
    assert len(applied["runtime_policy"]["config_hash"]) == 64
    assert {
        key: value for key, value in spot_profile.config_json.items() if key != "scanner"
    } == before_non_scanner
    invalidate.assert_awaited_once_with("spot_engine", user_id, None, strict=True)

    second = await service.materialize_l3_gate_policy(db, user_id, apply=True)
    assert second["changed"] is False
    assert second["applied"] is False
