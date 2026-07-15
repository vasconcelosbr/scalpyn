import pytest

from app.services.position_lifecycle_service import _fee_in_quote


def test_quote_fee_is_already_in_usdt() -> None:
    assert _fee_in_quote("0.0045", "USDT", "UNI_USDT", 3.68) == pytest.approx(0.0045)


def test_base_asset_fee_is_converted_at_fill_price() -> None:
    assert _fee_in_quote("0.249", "SKYAI", "SKYAI_USDT", 0.03611) == pytest.approx(
        0.00899139
    )


def test_unknown_fee_currency_is_not_treated_as_usdt() -> None:
    assert _fee_in_quote("1", "POINT", "BTC_USDT", 60_000) == 0.0
