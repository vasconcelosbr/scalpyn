from app._critical_schema import CRITICAL_COLUMNS


def test_critical_schema_includes_indicators_columns() -> None:
    assert ("indicators", "scheduler_group") in CRITICAL_COLUMNS
    assert ("indicators", "market_type") in CRITICAL_COLUMNS
