from app._critical_schema import CRITICAL_COLUMNS


def test_indicators_columns_are_covered_by_critical_schema_gate() -> None:
    assert ("indicators", "scheduler_group") in CRITICAL_COLUMNS
    assert ("indicators", "market_type") in CRITICAL_COLUMNS
