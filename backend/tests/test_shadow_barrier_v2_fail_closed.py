"""P1 Fase 1.6 — fail-closed do resolver de barreiras.

Contrato ativo ``shadow_atr_dynamic_v2`` exige TODAS as chaves de barreira e
ATR>0. Chave ausente/inválida ou ATR indisponível → ValueError e linha NÃO
criada (zero INSERT), nunca degradar para barreira fixa carimbada como v2.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.shadow_trade_service import (
    BARRIER_CONTRACT_ATR_DYNAMIC_V2,
    _apply_barrier_params,
    _create_from_decision,
    _require_v2_barrier_config,
    _resolve_barrier_contract_version,
)

_ML_V2_FULL = {
    "ml_active_barrier_contract_version": "shadow_atr_dynamic_v2",
    "shadow_barrier_mode": "ATR_DYNAMIC",
    "shadow_atr_multiplier_tp": 1.5,
    "shadow_atr_multiplier_sl": 1.5,
    "shadow_barrier_min_pct": 0.5,
    "shadow_barrier_max_pct": 3.0,
    "ml_win_fast_threshold_seconds": 14400,
    "ml_fee_roundtrip_pct": 0.2,
}


def _base_user_config():
    return {"tp_pct": 2.0, "sl_pct": 1.0}


# ── _apply_barrier_params: sem defaults silenciosos sob v2 ───────────────────

def test_apply_barrier_params_v2_no_silent_defaults():
    ml_config = dict(_ML_V2_FULL)
    del ml_config["shadow_atr_multiplier_sl"]
    del ml_config["shadow_barrier_min_pct"]
    del ml_config["shadow_barrier_max_pct"]
    merged = _apply_barrier_params(_base_user_config(), ml_config)
    # Contrato ativo v2 → chaves ausentes permanecem None (nunca 1.5/0.5/3.0).
    assert merged["sl_atr_multiplier"] is None
    assert merged["sl_min_pct"] is None
    assert merged["sl_max_pct"] is None
    assert merged["ml_active_barrier_contract_version"] == "shadow_atr_dynamic_v2"


def test_apply_barrier_params_legacy_keeps_defaults():
    # Sem contrato ativo declarado → comportamento v1/legacy inalterado.
    merged = _apply_barrier_params(_base_user_config(), {"shadow_barrier_mode": "FIXED"})
    assert merged["sl_atr_multiplier"] == 1.5
    assert merged["sl_min_pct"] == 0.5
    assert merged["sl_max_pct"] == 3.0
    assert merged["ml_active_barrier_contract_version"] is None


# ── validador puro ───────────────────────────────────────────────────────────

def test_require_v2_barrier_config_raises_on_missing_tp():
    cfg = _apply_barrier_params(_base_user_config(), {**_ML_V2_FULL, "shadow_atr_multiplier_tp": None})
    with pytest.raises(ValueError, match="barrier_v2_missing_shadow_atr_multiplier_tp"):
        _require_v2_barrier_config(cfg, "BTC_USDT")


def test_require_v2_barrier_config_raises_on_mode_mismatch():
    cfg = _apply_barrier_params(_base_user_config(), {**_ML_V2_FULL, "shadow_barrier_mode": "FIXED"})
    with pytest.raises(ValueError, match="barrier_v2_mode_mismatch"):
        _require_v2_barrier_config(cfg, "BTC_USDT")


def test_require_v2_barrier_config_passes_when_complete():
    cfg = _apply_barrier_params(_base_user_config(), _ML_V2_FULL)
    _require_v2_barrier_config(cfg, "BTC_USDT")  # não levanta
    assert _resolve_barrier_contract_version("ATR_DYNAMIC", cfg["tp_atr_multiplier"]) == (
        BARRIER_CONTRACT_ATR_DYNAMIC_V2
    )


# ── _create_from_decision: raise antes de qualquer INSERT ────────────────────

def _decision():
    return SimpleNamespace(
        id=123,
        user_id=uuid4(),
        symbol="BTC_USDT",
        strategy="profile-signal",
        direction="SPOT",
        created_at=datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc),
        metrics={},
        profile_id=None,
        profile_version=None,
        profile_name=None,
    )


@pytest.mark.asyncio
async def test_create_from_decision_v2_missing_tp_raises_zero_insert():
    db = AsyncMock()
    db.begin_nested = MagicMock()
    cfg = _apply_barrier_params(_base_user_config(), {**_ML_V2_FULL, "shadow_atr_multiplier_tp": None})
    with pytest.raises(ValueError, match="barrier_v2_missing_shadow_atr_multiplier_tp"):
        await _create_from_decision(db, _decision(), "NOT_TRADABLE", cfg)
    assert db.execute.await_count == 0  # nenhum INSERT


@pytest.mark.asyncio
async def test_create_from_decision_v2_atr_zero_raises_zero_insert():
    db = AsyncMock()
    db.begin_nested = MagicMock()
    cfg = _apply_barrier_params(_base_user_config(), _ML_V2_FULL)
    with patch(
        "app.services.shadow_trade_service._build_features_snapshot",
        return_value={"atr_percent": 0.0},
    ):
        with pytest.raises(ValueError, match="barrier_v2_atr_unavailable"):
            await _create_from_decision(db, _decision(), "NOT_TRADABLE", cfg)
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_create_from_decision_v2_happy_path_stamps_v2():
    entry_time = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
    result = SimpleNamespace(
        fetchone=lambda: (uuid4(),),
        first=lambda: None,
        mappings=lambda: SimpleNamespace(first=lambda: None),
    )
    db = AsyncMock()
    db.execute.return_value = result
    db.begin_nested = MagicMock()
    cfg = _apply_barrier_params(_base_user_config(), _ML_V2_FULL)
    with (
        patch(
            "app.services.shadow_trade_service._get_current_price_multi_tf",
            new=AsyncMock(return_value=(100.0, entry_time)),
        ),
        patch(
            "app.services.shadow_trade_service._build_features_snapshot",
            return_value={"atr_percent": 1.0},
        ),
    ):
        created_id = await _create_from_decision(db, _decision(), "NOT_TRADABLE", cfg)

    assert created_id is not None
    params = db.execute.await_args.args[1]
    assert params["barrier_contract_version"] == BARRIER_CONTRACT_ATR_DYNAMIC_V2
    # ATR 1.0 * mult 1.5 = 1.5, dentro do clamp [0.5, 3.0]
    assert params["tp_pct_applied"] == pytest.approx(1.5)
    assert params["sl_pct_applied"] == pytest.approx(1.5)
