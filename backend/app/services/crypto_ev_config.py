from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


DEFAULT_CRYPTO_EV_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "window_policy": "current_month_to_date",
    "window_hours": 168,
    "shrinkage_k": 30,
    "min_trades_for_state": 15,
    "max_unreplayable_ratio": 0.20,
    "fee_roundtrip_pct_source": "ml_fee_roundtrip_pct",
    "atr_buckets": [
        {"name": "LOW", "atr_pct_max": 1.0},
        {"name": "MID", "atr_pct_max": 2.0},
        {"name": "HIGH", "atr_pct_max": None},
    ],
    "score_normalization": {
        "method": "linear_clamp",
        "ev_at_score_0": -0.010,
        "ev_at_score_100": 0.010,
    },
    "states": {
        "favorable_enter": 65,
        "favorable_exit": 60,
        "risky_enter": 40,
        "risky_exit": 45,
        "avoid_enter": 25,
        "avoid_exit": 30,
    },
    "views": {"operational_view": "spectrum"},
    "ml_component": {
        "user_enabled": False,
        "weight_pct": 0,
        "health_gate": {
            "require_status": "promoted",
            "min_oos_auc": 0.62,
            "min_clean_days": 15,
            "require_canary_passed": True,
        },
    },
    "recalibration": {
        "auto_recompute_priors": True,
        "prior_refresh_hours": 24,
    },
    "task": {"interval_seconds": 900},
}


def default_crypto_ev_config() -> Dict[str, Any]:
    return deepcopy(DEFAULT_CRYPTO_EV_CONFIG)
