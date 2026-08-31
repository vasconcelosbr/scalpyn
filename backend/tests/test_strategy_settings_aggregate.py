from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.config_profile import ConfigProfile
from app.schemas.spot_engine_config import SpotEngineConfig
from app.schemas.strategy_settings import MLShadowConfig
from app.services.strategy_settings_service import (
    StrategySettingsConflictError,
    StrategySettingsService,
    StrategySettingsValidationError,
)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


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
            value.id = value.id or uuid4()
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
    docs = {
        "strategy": {"strategies": []},
        "spot_engine": SpotEngineConfig().model_dump(mode="json"),
        "ml": {
            **MLShadowConfig().model_dump(mode="json"),
            "unrelated_training_key": {"keep": True},
        },
    }
    return user_id, FakeSession([
        ConfigProfile(id=uuid4(), user_id=user_id, pool_id=None, config_type=key, config_json=value)
        for key, value in docs.items()
    ])


@pytest.mark.asyncio
async def test_aggregate_export_validate_round_trip_has_no_diff(aggregate):
    user_id, db = aggregate
    service = StrategySettingsService()
    exported = await service.get_config(db, user_id)
    validated = await service.validate_import(db, user_id, exported["config"])
    assert validated["diff"] == []
    assert validated["config"]["source_hash"] == exported["config"]["source_hash"]


@pytest.mark.asyncio
async def test_aggregate_partial_import_preserves_omitted_and_unrelated_ml(
    aggregate, monkeypatch
):
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
        change_description="partial",
        source="JSON_IMPORT",
    )
    ml = next(profile for profile in db.profiles if profile.config_type == "ml")
    assert ml.config_json["unrelated_training_key"] == {"keep": True}
    assert result["config"]["spot_engine"] == before_spot
    assert len(db.audits) == 1
    invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_canary_minimum_outcomes_is_governance_only_and_round_trips(
    aggregate, monkeypatch
):
    user_id, db = aggregate
    service = StrategySettingsService()
    current = await service.get_config(db, user_id)
    before = deepcopy(current["config"])
    invalidate = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.strategy_settings_service.config_service.invalidate_cache",
        invalidate,
    )

    result = await service.apply(
        db,
        user_id,
        payload={"ml_shadow": {"canary_minimum_outcomes": 25}},
        source_hash=current["config"]["source_hash"],
        change_description="governed observation threshold",
        source="FORM",
    )

    assert result["config"]["ml_shadow"]["canary_minimum_outcomes"] == 25
    assert result["config"]["strategy"] == before["strategy"]
    assert result["config"]["spot_engine"] == before["spot_engine"]
    ml = next(profile for profile in db.profiles if profile.config_type == "ml")
    assert ml.config_json["canary_minimum_outcomes"] == 25
    assert ml.config_json["unrelated_training_key"] == {"keep": True}
    invalidate.assert_awaited_once_with("ml", user_id, None, strict=True)


def test_missing_canary_threshold_preserves_legacy_source_hash_shape():
    service = StrategySettingsService()
    parts = service._normalise_parts({"ml": {}})
    assert "canary_minimum_outcomes" not in parts["ml_shadow"]


@pytest.mark.asyncio
async def test_aggregate_stale_hash_conflicts(aggregate):
    user_id, db = aggregate
    with pytest.raises(StrategySettingsConflictError):
        await StrategySettingsService().apply(
            db,
            user_id,
            payload={"ml_shadow": {"ml_fee_roundtrip_pct": 0.3}},
            source_hash="0" * 64,
            change_description="stale",
            source="FORM",
        )
    assert db.rollbacks == 1


def test_aggregate_rejects_unknown_and_incompatible_contracts():
    service = StrategySettingsService()
    with pytest.raises(StrategySettingsValidationError, match="Unknown field"):
        service.validate_payload(
            {"ml_shadow": {"unknown": 1}}, service._default_parts()
        )
    with pytest.raises(StrategySettingsValidationError, match="incompatible"):
        service.validate_payload(
            {
                "ml_shadow": {
                    "shadow_barrier_mode": "FIXED",
                    "ml_active_barrier_contract_version": "shadow_atr_dynamic_v2",
                }
            },
            service._default_parts(),
        )
