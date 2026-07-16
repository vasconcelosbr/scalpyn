from backend.app.services.shadow_trade_service import (
    _merge_ml_shadow_config,
    _resolve_shadow_tp_pct,
)
from backend.app.services.ml_challenger_service import _filter_l3_barrier_contract
from pathlib import Path


def test_strategy_module_tp_wins_over_legacy_ml_override():
    config = {
        "tp_pct": 0.6,
        "shadow_tp_pct": 1.5,
    }

    assert _resolve_shadow_tp_pct(config) == 0.6


def test_ml_barrier_merge_removes_legacy_tp_override():
    merged = _merge_ml_shadow_config(
        {"tp_pct": 0.6, "sl_pct": 1.0},
        {
            "shadow_tp_pct": 1.5,
            "shadow_barrier_mode": "ATR_DYNAMIC",
            "shadow_atr_multiplier_sl": 1.5,
            "shadow_barrier_min_pct": 0.5,
            "shadow_barrier_max_pct": 3.0,
        },
    )

    assert merged["tp_pct"] == 0.6
    assert "shadow_tp_pct" not in merged
    assert merged["shadow_barrier_mode"] == "ATR_DYNAMIC"


def test_training_contract_atr_dynamic_keeps_only_v2():
    # Fase 1.7 (I12): sob ATR_DYNAMIC só o carimbo v2 treina; uma linha ATR_DYNAMIC
    # não-v2 (TP fixo pré-fix disfarçado) é excluída como degradada — antes entrava
    # via match de TP e contaminava o dataset L3/CatBoost.
    rows = [
        {"barrier_mode": "ATR_DYNAMIC", "tp_pct_applied": 0.6, "id": "degraded_v1"},
        {"barrier_mode": "ATR_DYNAMIC", "tp_pct_applied": 1.5,
         "barrier_contract_version": "shadow_atr_dynamic_v2", "id": "v2"},
    ]

    kept, meta = _filter_l3_barrier_contract(
        rows, expected_mode="ATR_DYNAMIC", expected_tp_pct=0.6,
    )

    assert [row["id"] for row in kept] == ["v2"]
    assert meta["barrier_contract_atr_non_v2_excluded"] == 1


def test_training_contract_non_atr_uses_tp_parity():
    # Modos NÃO-ATR_DYNAMIC ainda filtram por paridade de TP com o Strategies Module.
    rows = [
        {"barrier_mode": "FIXED", "tp_pct_applied": 1.5, "id": "legacy"},
        {"barrier_mode": "FIXED", "tp_pct_applied": 0.6, "id": "current"},
    ]

    kept, meta = _filter_l3_barrier_contract(
        rows, expected_mode="FIXED", expected_tp_pct=0.6,
    )

    assert [row["id"] for row in kept] == ["current"]
    assert meta["barrier_contract_tp_mismatch"] == 1
    assert meta["barrier_contract_expected_tp_pct"] == 0.6


def test_shadow_monitor_no_longer_imports_legacy_tp_override():
    monitor_source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "tasks"
        / "shadow_trade_monitor.py"
    ).read_text(encoding="utf-8")

    assert "_SHADOW_TP_PCT_OVERRIDE" not in monitor_source
