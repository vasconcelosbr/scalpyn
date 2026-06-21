import asyncio
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.api.profile_intelligence import (
    _ml_challenger_status,
    _normalize_unimplemented_ml_flags,
    update_settings,
)
from app.schemas.profile_intelligence import PISettingsUpdate
from app.services.config_service import config_service


# ── _normalize_unimplemented_ml_flags ─────────────────────────────────────────

def test_normalize_ml_flags_blocks_uninstalled_packages():
    """Flags are forced to False + warning when the package is NOT importable."""
    with patch("app.api.profile_intelligence.find_spec", return_value=None):
        normalized, warnings = _normalize_unimplemented_ml_flags(
            {
                "enable_lightgbm": True,
                "enable_catboost": True,
                "enable_dynamic_combinations": True,
            }
        )

    assert normalized["enable_lightgbm"] is False
    assert normalized["enable_catboost"] is False
    assert normalized["enable_dynamic_combinations"] is True
    assert len(warnings) == 2
    assert any("lightgbm" in w for w in warnings)
    assert any("catboost" in w for w in warnings)


def test_normalize_ml_flags_passes_through_when_installed():
    """When packages ARE installed, flags are preserved unchanged and no warnings are emitted."""
    if find_spec("lightgbm") is None or find_spec("catboost") is None:
        pytest.skip("LightGBM/CatBoost not installed in this environment")

    normalized, warnings = _normalize_unimplemented_ml_flags(
        {
            "enable_lightgbm": True,
            "enable_catboost": True,
            "enable_dynamic_combinations": True,
        }
    )

    assert normalized["enable_lightgbm"] is True
    assert normalized["enable_catboost"] is True
    assert normalized["enable_dynamic_combinations"] is True
    assert warnings == []


# ── _ml_challenger_status ─────────────────────────────────────────────────────

def test_ml_challenger_overview_reports_operational_when_installed():
    """With LightGBM and CatBoost installed, status is operational."""
    if find_spec("lightgbm") is None or find_spec("catboost") is None:
        pytest.skip("LightGBM/CatBoost not installed in this environment")

    challengers = _ml_challenger_status()

    for model in ("lightgbm", "catboost"):
        s = challengers[model]
        assert s["available"] is True
        assert s["implemented"] is True
        assert s["installed"] is True
        assert s["operational"] is True
        assert s["status"] == "operational"
        assert s["effective_contribution"] == 1
        assert s["can_train"] is True
        assert s["can_infer"] is True
        assert s["can_generate_suggestions"] is True
        assert s["influences_autopilot"] is True


def test_ml_challenger_overview_zero_when_packages_not_installed():
    """When packages are NOT importable, all capability flags are 0/False."""
    from app.services import ml_challenger_service as _svc

    with patch.object(_svc, "_is_installed", return_value=False):
        challengers = _svc.get_challenger_status()

    for model in ("lightgbm", "catboost"):
        s = challengers[model]
        assert s["available"] is False
        assert s["installed"] is False
        assert s["operational"] is False
        assert s["effective_contribution"] == 0
        assert s["can_train"] is False
        assert s["can_infer"] is False
        assert s["can_generate_suggestions"] is False
        assert s["influences_autopilot"] is False


# ── update_settings ───────────────────────────────────────────────────────────

def test_settings_endpoint_persists_ml_challengers_when_installed(monkeypatch):
    """When packages are installed, enable_lightgbm/catboost are saved as True."""
    if find_spec("lightgbm") is None or find_spec("catboost") is None:
        pytest.skip("LightGBM/CatBoost not installed in this environment")

    saved = {}

    async def _update_config(**kwargs):
        saved.update(kwargs["new_json"])

    monkeypatch.setattr(config_service, "get_config", AsyncMock(return_value={}))
    monkeypatch.setattr(config_service, "update_config", _update_config)

    response = asyncio.run(update_settings(
        PISettingsUpdate(enable_lightgbm=True, enable_catboost=True),
        db=object(),
        user_id=uuid4(),
    ))

    assert saved["enable_lightgbm"] is True
    assert saved["enable_catboost"] is True
    assert response["settings"]["enable_lightgbm"] is True
    assert response["settings"]["enable_catboost"] is True
    assert response["warnings"] == []


def test_settings_endpoint_blocks_ml_challengers_when_not_installed(monkeypatch):
    """When packages are NOT installed, flags are normalized to False regardless of user input."""
    saved = {}

    async def _update_config(**kwargs):
        saved.update(kwargs["new_json"])

    monkeypatch.setattr(config_service, "get_config", AsyncMock(return_value={}))
    monkeypatch.setattr(config_service, "update_config", _update_config)

    with patch("app.api.profile_intelligence.find_spec", return_value=None):
        response = asyncio.run(update_settings(
            PISettingsUpdate(enable_lightgbm=True, enable_catboost=True),
            db=object(),
            user_id=uuid4(),
        ))

    assert saved["enable_lightgbm"] is False
    assert saved["enable_catboost"] is False
    assert response["settings"]["enable_lightgbm"] is False
    assert response["settings"]["enable_catboost"] is False
    assert len(response["warnings"]) == 2


# ── Frontend contract ─────────────────────────────────────────────────────────

def test_manual_run_payload_has_no_ml_challenger_options():
    frontend = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "app"
        / "profile-intelligence"
        / "page.tsx"
    ).read_text(encoding="utf-8")
    payload_block = frontend.split("const DEFAULT_RUN_PAYLOAD = {", 1)[1].split("};", 1)[0]

    assert "lightgbm" not in payload_block.lower()
    assert "catboost" not in payload_block.lower()
