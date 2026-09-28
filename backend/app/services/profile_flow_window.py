"""One explicit trade-flow window per profile, shared by every flow consumer."""
from typing import Any

PROFILE_FLOW_WINDOW_KEY = "l3_order_flow_window_seconds"
# Producer capabilities, not strategy defaults. Both fit the retained WS buffer.
SUPPORTED_FLOW_WINDOWS = frozenset({60, 300})
FLOW_INDICATORS = frozenset({
    "taker_ratio", "volume_delta", "buy_pressure", "taker_buy_volume", "taker_sell_volume",
})


def profile_flow_window(config: dict | None) -> int | None:
    value = (config or {}).get(PROFILE_FLOW_WINDOW_KEY)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value not in SUPPORTED_FLOW_WINDOWS:
        raise ValueError("PROFILE_FLOW_WINDOW_UNSUPPORTED")
    return value


def validate_profile_flow_window(config: dict) -> None:
    window = profile_flow_window(config)
    if window is None:
        return  # Legacy profiles retain their current collection policy.

    def visit(value: Any):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            indicator = value.get("indicator") or value.get("field")
            if indicator in FLOW_INDICATORS:
                if value.get("window_seconds") != window:
                    raise ValueError(f"PROFILE_FLOW_WINDOW_MISMATCH:{indicator}")
                if any(value.get(key) is not None for key in ("period", "timeframe", "candle_policy")):
                    raise ValueError(f"PROFILE_FLOW_CANDLE_IDENTITY_INVALID:{indicator}")
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    for section in ("filters", "signals", "entry_triggers", "block_rules"):
        visit(config.get(section, {}))
