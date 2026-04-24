import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.gate_market_data import parse_gate_spot_candle


def test_parse_gate_spot_candle_maps_base_and_quote_volume_correctly():
    candle = [
        "1777035600",
        "7204.46974000",
        "1.953",
        "1.963",
        "1.953",
        "1.96",
        "3674.87000000",
        "false",
    ]

    parsed = parse_gate_spot_candle(candle)

    assert parsed["quote_volume"] == 7204.46974
    assert parsed["volume"] == 3674.87
    assert parsed["close"] == 1.953
