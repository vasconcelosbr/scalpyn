import pytest
from fastapi import HTTPException
from types import SimpleNamespace
from uuid import uuid4

from app.api import profiles as profiles_api
from app.api.profiles import (
    _profile_role_matches_watchlist_levels,
    _requires_l3_feature_identity,
    _validate_profile_config,
    _validate_profile_config_for_role,
)


def test_signal_conditions_accept_legacy_indicator_and_store_field():
    config = _validate_profile_config({
        "signals": {
            "logic": "AND",
            "conditions": [
                {"indicator": "rsi", "operator": ">=", "value": 72},
                {"indicator": "volume_spike", "operator": ">=", "value": 1.2},
                {
                    "indicator": "orderbook_depth_usdt",
                    "operator": ">=",
                    "value": 20000,
                },
            ],
        },
    })

    assert [c["field"] for c in config["signals"]["conditions"]] == [
        "rsi",
        "volume_spike",
        "orderbook_depth_usdt",
    ]
    assert all("indicator" not in c for c in config["signals"]["conditions"])


def test_signal_condition_without_field_or_indicator_is_rejected():
    with pytest.raises(ValueError, match="Signal condition missing 'field'"):
        _validate_profile_config({
            "signals": {
                "conditions": [{"operator": ">=", "value": 72}],
            },
        })


def test_validation_preserves_unrelated_profile_fields():
    config = _validate_profile_config(
        {
            "reference_window": "20_candles",
            "risk": {"stop_loss_atr_multiplier": 1.5},
            "filters": {"logic": "AND", "conditions": []},
            "signals": {"logic": "AND", "conditions": []},
            "entry_triggers": {"logic": "AND", "conditions": []},
            "block_rules": {"blocks": []},
        }
    )

    assert config["reference_window"] == "20_candles"
    assert config["risk"] == {"stop_loss_atr_multiplier": 1.5}


@pytest.mark.parametrize(
    ("profile_role", "required"),
    [
        ("universe_filter", False),
        ("primary_filter", False),
        ("score_engine", False),
        ("acquisition_queue", True),
        (None, True),
        ("unknown_role", True),
    ],
)
def test_feature_identity_requirement_is_scoped_by_pipeline_role(
    profile_role, required
):
    assert _requires_l3_feature_identity(profile_role) is required


def test_pool_filters_can_be_saved_without_l3_source_identity():
    config = _validate_profile_config_for_role(
        {
            "default_timeframe": "5m",
            "filters": {
                "logic": "AND",
                "conditions": [
                    {"field": "volume_24h", "operator": ">=", "value": 2_000_000},
                    {
                        "field": "orderbook_depth_usdt",
                        "operator": ">=",
                        "value": 5_000,
                    },
                    {"field": "spread_pct", "operator": "<=", "value": 0.5},
                ],
            },
        },
        "universe_filter",
    )

    assert len(config["filters"]["conditions"]) == 3


def test_same_missing_source_config_is_rejected_for_l3():
    with pytest.raises(ValueError, match="L3_FEATURE_IDENTITY_INVALID.*SOURCE_REQUIRED"):
        _validate_profile_config_for_role(
            {
                "default_timeframe": "5m",
                "filters": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "volume_24h", "operator": ">=", "value": 2_000_000}
                    ],
                },
            },
            "acquisition_queue",
        )


def test_profile_role_must_match_all_associated_watchlist_levels():
    assert _profile_role_matches_watchlist_levels("universe_filter", ["POOL"])
    assert _profile_role_matches_watchlist_levels("primary_filter", ["L1", "l1"])
    assert not _profile_role_matches_watchlist_levels("score_engine", ["L1"])
    assert not _profile_role_matches_watchlist_levels("acquisition_queue", ["L2", "L3"])


class _ProfileResult:
    def __init__(self, profile):
        self.profile = profile

    def scalars(self):
        return self

    def first(self):
        return self.profile


class _ProfileSession:
    def __init__(self, profile):
        self.profile = profile
        self.commits = 0

    async def execute(self, _statement, _params=None):
        return _ProfileResult(self.profile)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _profile):
        return None


def _profile_with_role(role):
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        name="profile",
        description="",
        is_active=True,
        config={},
        profile_type="STANDARD",
        profile_role=role,
        pipeline_order=_FUNNEL_ROLE_ORDER_FOR_TEST[role],
        pipeline_label=role,
        profile_version=None,
        is_shadow_only=False,
        live_trading_enabled=False,
        preset_ia_last_run=None,
        preset_ia_config=None,
        created_at=None,
        updated_at=None,
    )


_FUNNEL_ROLE_ORDER_FOR_TEST = {
    "universe_filter": "0",
    "primary_filter": "1",
    "score_engine": "2",
    "acquisition_queue": "3",
}


@pytest.mark.asyncio
async def test_update_pool_profile_accepts_indicator_edits_without_l3_identity(monkeypatch):
    profile = _profile_with_role("universe_filter")
    session = _ProfileSession(profile)
    activation_calls = []

    async def _activate(_db, **kwargs):
        activation_calls.append(kwargs)
        kwargs["profile"].config = kwargs["config"]
        return {}

    monkeypatch.setattr(profiles_api, "activate_profile_config", _activate)
    config = {
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 2_000_000},
                {"field": "orderbook_depth_usdt", "operator": ">=", "value": 5_000},
                {"field": "spread_pct", "operator": "<=", "value": 0.5},
            ],
        },
    }

    result = await profiles_api.update_profile(
        profile.id,
        {
            "config": config,
            "profile_role": "universe_filter",
        },
        db=session,
        user_id=profile.user_id,
    )

    assert result["config"]["filters"]["conditions"] == config["filters"]["conditions"]
    assert activation_calls[0]["require_feature_identity"] is False
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_mtf_profile_uses_governed_editor_contract(monkeypatch):
    profile = _profile_with_role("primary_filter")
    profile.profile_type = "MTF_LAYER"
    profile.is_shadow_only = True
    session = _ProfileSession(profile)
    activation_calls = []
    expected_version_id = uuid4()
    expected_hash = "a" * 64

    async def _activate(_db, **kwargs):
        activation_calls.append(kwargs)
        kwargs["profile"].config = kwargs["config"]
        return {
            "profile_version_id": str(uuid4()),
            "profile_config_hash": "b" * 64,
        }

    monkeypatch.setattr(profiles_api, "activate_profile_config", _activate)

    result = await profiles_api.update_profile(
        profile.id,
        {
            "name": profile.name,
            "config": {
                "default_timeframe": "1h",
                "filters": {"logic": "AND", "conditions": []},
            },
            "profile_role": "primary_filter",
            "expected_profile_version_id": str(expected_version_id),
            "expected_profile_config_hash": expected_hash,
        },
        db=session,
        user_id=profile.user_id,
    )

    call = activation_calls[0]
    assert call["expected_profile_version_id"] == expected_version_id
    assert call["expected_profile_config_hash"] == expected_hash
    assert call["require_feature_identity"] is True
    assert call["change_source"] == "profile_ui_editor"
    assert result["profile_config_hash"] == "b" * 64
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_mtf_profile_rejects_missing_editor_contract(monkeypatch):
    profile = _profile_with_role("score_engine")
    profile.profile_type = "MTF_LAYER"
    profile.is_shadow_only = True
    session = _ProfileSession(profile)

    async def _unexpected_activate(_db, **_kwargs):
        raise AssertionError("MTF write without an immutable contract must fail closed")

    monkeypatch.setattr(profiles_api, "activate_profile_config", _unexpected_activate)

    with pytest.raises(HTTPException) as exc_info:
        await profiles_api.update_profile(
            profile.id,
            {
                "config": {
                    "default_timeframe": "15m",
                    "signals": {"logic": "AND", "conditions": []},
                },
                "profile_role": "score_engine",
            },
            db=session,
            user_id=profile.user_id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"code": "MTF_PROFILE_EDITOR_CONTRACT_REQUIRED"}
    assert session.commits == 0


def test_profile_update_contract_is_exposed_to_the_editor():
    profile = _profile_with_role("primary_filter")
    version_id = uuid4()
    config_hash = "c" * 64

    result = profiles_api._profile_to_dict_with_update_contract(
        profile,
        {
            profile.id: {
                "contract": {
                    "profile_version_id": str(version_id),
                    "profile_projection_hash": config_hash,
                }
            }
        },
    )

    assert result["expected_profile_version_id"] == str(version_id)
    assert result["expected_profile_config_hash"] == config_hash


@pytest.mark.asyncio
async def test_update_l3_legacy_profile_allows_parameter_change_without_widening_identity_debt(monkeypatch):
    profile = _profile_with_role("acquisition_queue")
    profile.config = {
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000}
            ],
        },
    }
    session = _ProfileSession(profile)
    activation_calls = []

    async def _activate(_db, **kwargs):
        activation_calls.append(kwargs)
        kwargs["profile"].config = kwargs["config"]
        return {}

    monkeypatch.setattr(profiles_api, "activate_profile_config", _activate)

    result = await profiles_api.update_profile(
        profile.id,
        {
            "config": {
                "default_timeframe": "5m",
                "filters": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "volume_24h", "operator": ">=", "value": 2_000_000}
                    ],
                },
            },
            "profile_role": "acquisition_queue",
        },
        db=session,
        user_id=profile.user_id,
    )

    assert result["config"]["filters"]["conditions"][0]["value"] == 2_000_000
    assert result["warnings"][0]["code"] == "L3_FEATURE_IDENTITY_PENDING"
    assert activation_calls[0]["require_feature_identity"] is False
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_l3_legacy_profile_rejects_new_identity_debt(monkeypatch):
    profile = _profile_with_role("acquisition_queue")
    profile.config = {
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [
                {"field": "volume_24h", "operator": ">=", "value": 1_000_000}
            ],
        },
    }
    session = _ProfileSession(profile)

    async def _unexpected_activate(_db, **_kwargs):
        raise AssertionError("new L3 identity debt must not be activated")

    monkeypatch.setattr(profiles_api, "activate_profile_config", _unexpected_activate)

    with pytest.raises(HTTPException) as exc_info:
        await profiles_api.update_profile(
            profile.id,
            {
                "config": {
                    "default_timeframe": "5m",
                    "filters": {
                        "logic": "AND",
                        "conditions": [
                            {"field": "volume_24h", "operator": ">=", "value": 2_000_000},
                            {"field": "spread_pct", "operator": "<=", "value": 0.5},
                        ],
                    },
                },
                "profile_role": "acquisition_queue",
            },
            db=session,
            user_id=profile.user_id,
        )

    assert exc_info.value.status_code == 422
    assert "filters.conditions[1]" in str(exc_info.value.detail)
    assert "SOURCE_REQUIRED" in str(exc_info.value.detail)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_update_valid_l3_profile_cannot_remove_feature_identity(monkeypatch):
    profile = _profile_with_role("acquisition_queue")
    profile.config = {
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [
                {
                    "field": "volume_24h",
                    "operator": ">=",
                    "value": 1_000_000,
                    "source": "decision_context",
                    "source_provider": "market_metadata",
                    "provider_policy_id": "spot_decision_context_v1",
                }
            ],
        },
    }
    session = _ProfileSession(profile)

    async def _unexpected_activate(_db, **_kwargs):
        raise AssertionError("valid L3 identity must never be removable")

    monkeypatch.setattr(profiles_api, "activate_profile_config", _unexpected_activate)

    with pytest.raises(HTTPException) as exc_info:
        await profiles_api.update_profile(
            profile.id,
            {
                "config": {
                    "default_timeframe": "5m",
                    "filters": {
                        "logic": "AND",
                        "conditions": [
                            {"field": "volume_24h", "operator": ">=", "value": 2_000_000}
                        ],
                    },
                },
                "profile_role": "acquisition_queue",
            },
            db=session,
            user_id=profile.user_id,
        )

    assert exc_info.value.status_code == 422
    assert "SOURCE_REQUIRED" in str(exc_info.value.detail)
    assert session.commits == 0
