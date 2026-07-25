"""Migração L3_LAB → contrato de barreira shadow_atr_dynamic_v2 (2026-07-25).

Antes desta migração, TODOS os shadows do Strategy Lab nasciam com
barrier_mode=FIXED / shadow_fixed_v1 (TP fixo do spot engine), tornando a fonte
L3_LAB economicamente incompatível com a lane de treino L3 (ATR-dinâmica) — o
filtro de paridade descartava 100% das linhas. Estes testes fixam o novo
contrato: sob v2 ativo o lab espelha o write-path canônico do L3.
"""
import pytest

from backend.app.services.shadow_trade_service import (
    BARRIER_CONTRACT_ATR_DYNAMIC_V2,
    _apply_barrier_params,
    _resolve_lab_barrier,
)


def _v2_cfg(**overrides):
    ml = {
        "ml_active_barrier_contract_version": "shadow_atr_dynamic_v2",
        "shadow_barrier_mode": "ATR_DYNAMIC",
        "shadow_atr_multiplier_tp": 1.5,
        "shadow_atr_multiplier_sl": 1.5,
        "shadow_barrier_min_pct": 0.5,
        "shadow_barrier_max_pct": 3.0,
    }
    ml.update(overrides)
    return _apply_barrier_params({}, ml)


def test_lab_barrier_v2_scales_tp_sl_from_atr():
    resolved = _resolve_lab_barrier(
        {"atr_percent": 0.9086}, 0.6, 1.0, _v2_cfg(),
        symbol="CRV_USDT", log_tag="test",
    )
    assert resolved is not None
    mode, tp, sl, version = resolved
    assert mode == "ATR_DYNAMIC"
    assert version == BARRIER_CONTRACT_ATR_DYNAMIC_V2
    assert tp == pytest.approx(1.5 * 0.9086)
    assert sl == pytest.approx(1.5 * 0.9086)


def test_lab_barrier_v2_clamps_to_min_and_max():
    # ATR baixo → clamp no piso 0.5
    _, tp_low, sl_low, _ = _resolve_lab_barrier(
        {"atr_percent": 0.10}, 0.6, 1.0, _v2_cfg(), symbol="PAXG_USDT", log_tag="test",
    )
    assert tp_low == 0.5 and sl_low == 0.5
    # ATR alto → clamp no teto 3.0
    _, tp_high, sl_high, _ = _resolve_lab_barrier(
        {"atr_percent": 9.0}, 0.6, 1.0, _v2_cfg(), symbol="PUMP_USDT", log_tag="test",
    )
    assert tp_high == 3.0 and sl_high == 3.0


def test_lab_barrier_v2_fail_closed_without_atr():
    # Sob contrato v2, ATR ausente/zero/inválido → linha NÃO criada (None),
    # nunca degrada para FIXED carimbado como v2 (mesma regra do L3 canônico).
    cfg = _v2_cfg()
    for feats in ({}, {"atr_percent": 0.0}, {"atr_percent": None}, {"atr_pct": "x"}):
        assert _resolve_lab_barrier(
            feats, 0.6, 1.0, cfg, symbol="PEPE_USDT", log_tag="test",
        ) is None


def test_lab_barrier_v2_falls_back_to_atr_pct_key():
    resolved = _resolve_lab_barrier(
        {"atr_pct": 0.3491}, 0.6, 1.0, _v2_cfg(), symbol="ENA_USDT", log_tag="test",
    )
    assert resolved is not None
    _, tp, _, version = resolved
    assert tp == pytest.approx(1.5 * 0.3491)
    assert version == BARRIER_CONTRACT_ATR_DYNAMIC_V2


def test_lab_barrier_legacy_without_v2_contract_unchanged():
    # Sem contrato v2 ativo: comportamento legado intacto (FIXED, carimbo v1),
    # inclusive quando há ATR disponível.
    cfg = _apply_barrier_params({}, {"shadow_barrier_mode": "FIXED"})
    resolved = _resolve_lab_barrier(
        {"atr_percent": 1.0}, 0.6, 1.0, cfg, symbol="BTC_USDT", log_tag="test",
    )
    assert resolved == ("FIXED", 0.6, 1.0, "shadow_fixed_v1")
