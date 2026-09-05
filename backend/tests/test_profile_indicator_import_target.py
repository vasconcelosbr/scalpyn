from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api import profiles as profiles_api
from app.api.profiles import (
    _prepare_indicator_update_items,
    _replace_execution_sections,
)
from app.services.profile_execution_contract import ProfileContractConflict


PROFILE_ID = "11111111-1111-1111-1111-111111111111"
VERSION_ID = "22222222-2222-2222-2222-222222222222"


def _item(**overrides):
    item = {
        "profile_id": PROFILE_ID,
        "name": "DUPLICATE_DISPLAY_NAME",
        "expected_profile_version_id": VERSION_ID,
        "expected_profile_config_hash": "a" * 64,
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
    }
    item.update(overrides)
    return item


def test_indicator_update_targets_canonical_profile_id_not_name():
    prepared = _prepare_indicator_update_items([_item()])

    assert prepared[0][2] == UUID(PROFILE_ID)
    assert prepared[0][1]["name"] == "DUPLICATE_DISPLAY_NAME"


def test_execution_contract_audit_route_precedes_dynamic_profile_route():
    paths = [route.path for route in profiles_api.router.routes]

    assert paths.index("/api/profiles/execution-contract/audit") < paths.index(
        "/api/profiles/{profile_id}"
    )


def test_indicator_update_rejects_name_only_payload():
    item = _item()
    item.pop("profile_id")

    with pytest.raises(ValueError, match="profile_id is required"):
        _prepare_indicator_update_items([item])


@pytest.mark.parametrize(
    "missing_section", ["filters", "signals", "entry_triggers", "block_rules"]
)
def test_indicator_update_requires_all_execution_sections(missing_section):
    item = _item()
    item.pop(missing_section)

    with pytest.raises(ValueError, match=missing_section):
        _prepare_indicator_update_items([item])


def test_indicator_update_rejects_duplicate_ids_before_writing():
    duplicate = deepcopy(_item(name="ANOTHER_DISPLAY_NAME"))

    with pytest.raises(ValueError, match="duplicate profile_id"):
        _prepare_indicator_update_items([_item(), duplicate])


@pytest.mark.parametrize(
    "missing_field",
    ["expected_profile_version_id", "expected_profile_config_hash"],
)
def test_indicator_update_requires_optimistic_concurrency_fields(missing_field):
    item = _item()
    item.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        _prepare_indicator_update_items([item])


def test_indicator_update_accepts_all_eight_canonical_indicators():
    block_indicators = [
        "adx_acceleration",
        "adx_slope_3",
        "macd_hist_slope_3",
        "rsi_slope_3",
        "entry_exhaustion_score",
        "rsi_6",
        "breakout_distance_pct",
    ]
    item = _item(
        block_rules={
            "blocks": [{
                "id": "b1",
                "conditions": [
                    {
                        "type": "threshold",
                        "indicator": indicator,
                        "operator": ">=",
                        "value": 0,
                        **({"period": 6} if indicator == "rsi_6" else {}),
                        **(
                            {"reference_window": "15m"}
                            if indicator == "breakout_distance_pct"
                            else {}
                        ),
                    }
                    for indicator in block_indicators
                ],
            }],
        },
        entry_triggers={
            "logic": "AND",
            "conditions": [{
                "type": "threshold",
                "indicator": "macd_hist_slope_5",
                "operator": ">=",
                "value": 0,
                "required": True,
                "enabled": True,
            }],
        },
    )

    assert _prepare_indicator_update_items([item])[0][2] == UUID(PROFILE_ID)


def test_indicator_update_rejects_unknown_indicator_with_exact_path():
    item = _item(
        signals={
            "logic": "AND",
            "conditions": [{"field": "future_indicator", "operator": ">=", "value": 1}],
        }
    )

    with pytest.raises(ValueError, match=r"UNKNOWN_INDICATOR.*profiles\[0\]\.signals\.conditions\[0\]\.field"):
        _prepare_indicator_update_items([item])


def test_indicator_update_rejects_breakout_without_reference_window():
    item = _item(
        block_rules={
            "blocks": [{
                "conditions": [{
                    "type": "threshold",
                    "indicator": "breakout_distance_pct",
                    "operator": ">",
                    "value": 0.8,
                }],
            }],
        }
    )

    with pytest.raises(ValueError, match=r"REFERENCE_WINDOW_REQUIRED.*reference_window"):
        _prepare_indicator_update_items([item])


def test_indicator_update_rejects_indicator_in_wrong_section():
    item = _item(
        filters={
            "logic": "AND",
            "conditions": [{"field": "adx_acceleration", "operator": ">=", "value": 0}],
        }
    )

    with pytest.raises(ValueError, match="INDICATOR_SECTION_NOT_ALLOWED"):
        _prepare_indicator_update_items([item])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_profile_version_id", "not-a-uuid"),
        ("expected_profile_config_hash", "not-a-hash"),
    ],
)
def test_indicator_update_rejects_invalid_optimistic_concurrency_values(field, value):
    with pytest.raises(ValueError, match=field):
        _prepare_indicator_update_items([_item(**{field: value})])


@pytest.mark.asyncio
async def test_structurally_invalid_batch_is_rejected_before_lock_or_write(monkeypatch):
    lock = AsyncMock()
    activate = AsyncMock()
    monkeypatch.setattr(profiles_api, "lock_profiles_for_update", lock)
    monkeypatch.setattr(profiles_api, "activate_profile_config", activate)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    invalid = _item(
        block_rules={
            "blocks": [{
                "conditions": [{
                    "indicator": "breakout_distance_pct",
                    "operator": ">",
                    "value": 0.8,
                }],
            }],
        }
    )

    with pytest.raises(HTTPException) as exc:
        await profiles_api.bulk_import_profiles(
            {"update_indicators_only": True, "profiles": [_item(), invalid]},
            db=db,
            user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        )

    assert exc.value.status_code == 422
    lock.assert_not_awaited()
    activate.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_indicator_update_replaces_all_sections_and_preserves_everything_else():
    current = {
        "default_timeframe": "5m",
        "reference_window": "20_candles",
        "risk": {"stop_loss_atr_multiplier": 1.5},
        "filters": {"conditions": [{"id": "remove-me"}]},
        "signals": {"conditions": [{"id": "old"}]},
        "entry_triggers": {"conditions": [{"id": "old-trigger"}]},
        "block_rules": {"blocks": [{"id": "old-block"}]},
    }
    item = _item(
        filters={"logic": "AND", "conditions": [{"id": "new-filter"}]},
        signals={"logic": "AND", "conditions": []},
        entry_triggers={
            "logic": "AND",
            "conditions": [{"id": "new-trigger"}],
        },
        block_rules={"blocks": []},
    )

    replaced = _replace_execution_sections(current, item)

    assert replaced["filters"]["conditions"] == [{"id": "new-filter"}]
    assert replaced["signals"]["conditions"] == []
    assert replaced["entry_triggers"]["conditions"] == [{"id": "new-trigger"}]
    assert replaced["block_rules"]["blocks"] == []
    assert replaced["reference_window"] == "20_candles"
    assert replaced["risk"] == {"stop_loss_atr_multiplier": 1.5}
    assert current["filters"]["conditions"] == [{"id": "remove-me"}]


@pytest.mark.asyncio
async def test_invalid_batch_rolls_back_every_profile(monkeypatch):
    second_id = UUID("33333333-3333-3333-3333-333333333333")
    profiles = {
        UUID(PROFILE_ID): SimpleNamespace(
            id=UUID(PROFILE_ID), name="DUPLICATE_DISPLAY_NAME", config={},
        ),
        second_id: SimpleNamespace(
            id=second_id, name="SECOND", config={},
        ),
    }
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(
        profiles_api,
        "lock_profiles_for_update",
        AsyncMock(return_value=profiles),
    )
    activate = AsyncMock(
        side_effect=[
            {"profile_version_id": VERSION_ID},
            ValueError("invalid second profile"),
        ]
    )
    monkeypatch.setattr(profiles_api, "activate_profile_config", activate)
    payload = {
        "update_indicators_only": True,
        "profiles": [
            _item(),
            _item(
                profile_id=str(second_id),
                name="SECOND",
                expected_profile_version_id=(
                    "44444444-4444-4444-4444-444444444444"
                ),
            ),
        ],
    }

    with pytest.raises(HTTPException) as exc:
        await profiles_api.bulk_import_profiles(
            payload, db=db, user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        )

    assert exc.value.status_code == 422
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_optimistic_conflict_returns_409_and_rolls_back(monkeypatch):
    profile_id = UUID(PROFILE_ID)
    profile = SimpleNamespace(
        id=profile_id, name="DUPLICATE_DISPLAY_NAME", config={},
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(
        profiles_api,
        "lock_profiles_for_update",
        AsyncMock(return_value={profile_id: profile}),
    )
    monkeypatch.setattr(
        profiles_api,
        "activate_profile_config",
        AsyncMock(
            side_effect=ProfileContractConflict("PROFILE_VERSION_CONFLICT")
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await profiles_api.bulk_import_profiles(
            {"update_indicators_only": True, "profiles": [_item()]},
            db=db,
            user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        )

    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_display_names_update_only_requested_profile_id(monkeypatch):
    requested_id = UUID(PROFILE_ID)
    other_id = UUID("55555555-5555-5555-5555-555555555555")
    requested = SimpleNamespace(
        id=requested_id, name="DUPLICATE_DISPLAY_NAME", config={},
    )
    other = SimpleNamespace(
        id=other_id, name="DUPLICATE_DISPLAY_NAME", config={},
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    lock = AsyncMock(return_value={requested_id: requested})
    activate = AsyncMock(
        return_value={"profile_version_id": VERSION_ID, "version_created": True}
    )
    monkeypatch.setattr(profiles_api, "lock_profiles_for_update", lock)
    monkeypatch.setattr(profiles_api, "activate_profile_config", activate)

    result = await profiles_api.bulk_import_profiles(
        {"update_indicators_only": True, "profiles": [_item()]},
        db=db,
        user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert result["updated"] == 1
    assert activate.await_args.kwargs["profile"] is requested
    assert activate.await_args.kwargs["profile"] is not other
    lock.assert_awaited_once()
    assert lock.await_args.kwargs["profile_ids"] == [requested_id]
    db.commit.assert_awaited_once()


def _mtf_profile_payload(*, activation_mode="DRAFT"):
    return {
        "name": "MTF L1 TEST",
        "profile_kind": "MTF_LAYER",
        "layer": "L1",
        "activation_mode": activation_mode,
        "default_timeframe": "1h",
        "source_identity": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_policy": "CLOSED_ONLY",
            "validity_margin_seconds": None,
        },
        "calibration": {
            "status": "CONFIG_REQUIRED",
            "method": "WALK_FORWARD",
            "min_samples": None,
            "thresholds_emitted": False,
        },
        "mtf_semantics": {
            "candidate_features": ["adx", "di_plus", "di_minus"],
            "adx_strong_min": None,
        },
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
        "scoring": {
            "enabled": False,
            "weights": {},
            "rules": [],
            "selected_rule_ids": [],
            "thresholds": {},
        },
    }


@pytest.mark.asyncio
async def test_mtf_draft_import_is_inactive_shadow_only_and_never_live(monkeypatch):
    db = SimpleNamespace(
        add=MagicMock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    monkeypatch.setattr(
        profiles_api, "_find_duplicate_names", AsyncMock(return_value=[])
    )
    activate = AsyncMock(return_value={"profile_version_id": VERSION_ID})
    monkeypatch.setattr(profiles_api, "activate_profile_config", activate)

    result = await profiles_api.bulk_import_profiles(
        {"profiles": [_mtf_profile_payload()]},
        db=db,
        user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert result["created"] == 1
    profile = activate.await_args.kwargs["profile"]
    assert profile.profile_type == "MTF_LAYER"
    assert profile.is_active is False
    assert profile.is_shadow_only is True
    assert profile.live_trading_enabled is False
    assert profile.config["mtf_layer"]["operational_effect"] is False


@pytest.mark.asyncio
async def test_mtf_import_rejects_active_mode_before_write(monkeypatch):
    db = SimpleNamespace(
        add=MagicMock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    monkeypatch.setattr(
        profiles_api, "_find_duplicate_names", AsyncMock(return_value=[])
    )
    activate = AsyncMock()
    monkeypatch.setattr(profiles_api, "activate_profile_config", activate)

    result = await profiles_api.bulk_import_profiles(
        {"profiles": [_mtf_profile_payload(activation_mode="ACTIVE")]},
        db=db,
        user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert result["failed"] == 1
    assert "MTF_LAYER_ACTIVE_FORBIDDEN" in result["results"][0]["error"]
    db.add.assert_not_called()
    activate.assert_not_awaited()
