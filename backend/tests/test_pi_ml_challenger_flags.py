from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.profile_intelligence import (
    _ml_challenger_status,
    _normalize_unimplemented_ml_flags,
    update_settings,
)
from app.schemas.profile_intelligence import PISettingsUpdate
from app.services.config_service import config_service


def test_unimplemented_ml_flags_are_normalized_to_false():
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
    assert warnings == [
        "LightGBM is not implemented",
        "CatBoost is not implemented",
    ]


def test_ml_challenger_overview_reports_zero_operational_capability():
    challengers = _ml_challenger_status()

    for model in ("lightgbm", "catboost"):
        status = challengers[model]
        assert status["available"] is False
        assert status["implemented"] is False
        assert status["installed"] is False
        assert status["operational"] is False
        assert status["effective_contribution"] == 0
        assert status["can_train"] is False
        assert status["can_infer"] is False
        assert status["can_generate_suggestions"] is False
        assert status["influences_autopilot"] is False


@pytest.mark.asyncio
async def test_settings_endpoint_never_persists_ml_challengers_as_enabled(monkeypatch):
    saved = {}

    async def _update_config(**kwargs):
        saved.update(kwargs["new_json"])

    monkeypatch.setattr(
        config_service,
        "get_config",
        AsyncMock(
            return_value={
                "enable_lightgbm": True,
                "enable_catboost": True,
            }
        ),
    )
    monkeypatch.setattr(config_service, "update_config", _update_config)

    response = await update_settings(
        PISettingsUpdate(enable_lightgbm=True, enable_catboost=True),
        db=object(),
        user_id=uuid4(),
    )

    assert saved["enable_lightgbm"] is False
    assert saved["enable_catboost"] is False
    assert response["settings"]["enable_lightgbm"] is False
    assert response["settings"]["enable_catboost"] is False
    assert response["warnings"] == [
        "LightGBM is not implemented",
        "CatBoost is not implemented",
    ]


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


def test_ui_marks_ml_challengers_as_not_implemented_and_disabled():
    frontend = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "app"
        / "profile-intelligence"
        / "page.tsx"
    ).read_text(encoding="utf-8")

    assert '["LightGBM", "CatBoost"]' in frontend
    assert "Status: Não implementado" in frontend
    assert "Contribuição atual: zero" in frontend
    assert "Não treina, não executa inferência" in frontend
    assert "disabled" in frontend
