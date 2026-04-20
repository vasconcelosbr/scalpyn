from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement


_CANONICAL_EXCHANGE_NAMES = {
    "gateio": "gate.io",
    "gate.io": "gate.io",
    "binance": "binance",
    "bybit": "bybit",
    "okx": "okx",
}

_DISPLAY_EXCHANGE_NAMES = {
    "gate.io": "Gate.io",
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
}


def normalize_exchange_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return _CANONICAL_EXCHANGE_NAMES.get(normalized, normalized)


def display_exchange_name(value: str) -> str:
    normalized = normalize_exchange_name(value)
    return _DISPLAY_EXCHANGE_NAMES.get(normalized, normalized)


def exchange_name_matches(column: ColumnElement[str], value: str) -> ColumnElement[bool]:
    return func.lower(column) == normalize_exchange_name(value)
