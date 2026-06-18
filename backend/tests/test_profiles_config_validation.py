import pytest

from app.api.profiles import _validate_profile_config


def test_signal_conditions_accept_legacy_indicator_and_store_field():
    config = _validate_profile_config({
        "signals": {
            "logic": "AND",
            "conditions": [
                {"indicator": "rsi", "operator": ">=", "value": 72},
                {"indicator": "volume_spike", "operator": ">=", "value": 1.2},
                {
                    "indicator": "orderbook_depth_usdt",
                    "operator": ">=",
                    "value": 20000,
                },
            ],
        },
    })

    assert [c["field"] for c in config["signals"]["conditions"]] == [
        "rsi",
        "volume_spike",
        "orderbook_depth_usdt",
    ]
    assert all("indicator" not in c for c in config["signals"]["conditions"])


def test_signal_condition_without_field_or_indicator_is_rejected():
    with pytest.raises(ValueError, match="Signal condition missing 'field'"):
        _validate_profile_config({
            "signals": {
                "conditions": [{"operator": ">=", "value": 72}],
            },
        })
