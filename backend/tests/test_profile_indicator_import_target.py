from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from datetime import datetime, timezone
from uuid import uuid4

from app.api.profiles import export_profiles, _profile_to_export_dict, _select_indicator_update_profile


def _profile(*, is_active: bool):
    return SimpleNamespace(is_active=is_active)


def test_indicator_update_keeps_unique_inactive_match_supported():
    profile = _profile(is_active=False)

    assert _select_indicator_update_profile([profile], "L3_TEST") is profile


def test_indicator_update_prefers_sole_active_duplicate():
    inactive = _profile(is_active=False)
    active = _profile(is_active=True)

    assert _select_indicator_update_profile([inactive, active], "L3_TEST") is active


def test_indicator_update_rejects_multiple_active_duplicates():
    with pytest.raises(ValueError, match="2 active profiles named 'L3_TEST'"):
        _select_indicator_update_profile(
            [_profile(is_active=True), _profile(is_active=True)],
            "L3_TEST",
        )


def test_indicator_update_rejects_multiple_inactive_duplicates():
    with pytest.raises(ValueError, match="2 inactive profiles named 'L3_TEST'"):
        _select_indicator_update_profile(
            [_profile(is_active=False), _profile(is_active=False)],
            "L3_TEST",
        )


def test_profile_export_is_lossless_and_correction_ready():
    profile_id = uuid4()
    now = datetime.now(timezone.utc)
    config = {
        "default_timeframe": "15m",
        "filters": {"logic": "AND", "conditions": [{"field": "adx", "operator": ">", "value": 20}]},
        "scoring": {"selected_rule_ids": ["adx_score"]},
        "signals": {"logic": "OR", "conditions": []},
        "block_rules": {
            "blocks": [{"indicator": "atr_pct", "operator": ">", "value": 8, "unit": "ATR %"}],
        },
        "entry_triggers": {"logic": "AND", "conditions": []},
        "future_config_key": {"preserved": True},
    }
    profile = SimpleNamespace(
        id=profile_id,
        name="L3_AUDIT",
        description="Audit profile",
        is_active=True,
        config=config,
        profile_role="acquisition_queue",
        pipeline_order="3",
        pipeline_label="L3",
        profile_type="STANDARD",
        profile_version=now,
        generated_by="manual",
        generated_from_suggestion_id=None,
        is_shadow_only=False,
        live_trading_enabled=True,
        auto_pilot_enabled=False,
        auto_pilot_config={"window": "daily"},
        preset_ia_last_run=None,
        preset_ia_config={"model": "governed"},
        created_at=now,
        updated_at=now,
    )

    exported = _profile_to_export_dict(profile)

    assert exported["profile_id"] == str(profile_id)
    assert exported["name"] == "L3_AUDIT"
    assert exported["funnel_role"] == "acquisition_queue"
    assert exported["default_timeframe"] == "15m"
    assert exported["block_rules"]["blocks"][0]["unit"] == "ATR %"
    assert exported["audit_metadata"]["config_snapshot"] == config
    assert exported["audit_metadata"]["auto_pilot_config"] == {"window": "daily"}


@pytest.mark.asyncio
async def test_export_envelope_flags_duplicate_names_and_safe_reimport_mode():
    now = datetime.now(timezone.utc)

    def exported_profile(name: str, *, is_active: bool):
        return SimpleNamespace(
            id=uuid4(),
            name=name,
            description="",
            is_active=is_active,
            config={},
            profile_role="acquisition_queue",
            pipeline_order="3",
            pipeline_label="L3",
            profile_type="STANDARD",
            profile_version=now,
            generated_by=None,
            generated_from_suggestion_id=None,
            is_shadow_only=False,
            live_trading_enabled=False,
            auto_pilot_enabled=False,
            auto_pilot_config={},
            preset_ia_last_run=None,
            preset_ia_config=None,
            created_at=now,
            updated_at=now,
        )

    profiles = [
        exported_profile("L3_DUPLICATE", is_active=True),
        exported_profile("l3_duplicate", is_active=False),
        exported_profile("L3_UNIQUE", is_active=True),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = profiles
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    payload = await export_profiles(db=db, user_id=uuid4())

    assert payload["export_type"] == "scalpyn_strategy_profiles"
    assert payload["schema_version"] == 1
    assert payload["profiles_count"] == 3
    assert payload["update_indicators_only"] is True
    assert len(payload["duplicate_name_groups"]) == 1
    assert len(payload["duplicate_name_groups"][0]["profiles"]) == 2
