from pathlib import Path


def _shadow_service_source() -> str:
    return (
        Path(__file__).parents[1] / "app" / "services" / "shadow_trade_service.py"
    ).read_text(encoding="utf-8")


def test_create_from_decision_prefilters_active_profile_shadow_before_price_lookup():
    source = _shadow_service_source()
    function = source.split("async def _create_from_decision", 1)[1].split(
        "async def safe_create_from_symbol_skip", 1
    )[0]

    preflight = function.index("await _has_active_profile_shadow(")
    price_lookup = function.index("entry_price, entry_ts = await _get_current_price_multi_tf(")
    nested_insert = function.index("async with db.begin_nested():")
    insert_sql = function.index("_INSERT_SHADOW_SQL")

    assert preflight < price_lookup
    assert nested_insert < insert_sql


def test_strategy_lab_allow_prefilters_active_symbols_and_uses_savepoint():
    source = _shadow_service_source()
    function = source.split("async def create_strategy_lab_shadows", 1)[1].split(
        "async def create_strategy_lab_rejected_shadows", 1
    )[0]

    active_load = function.index("active_symbols = await _load_active_profile_shadow_symbols(")
    active_skip = function.index("if symbol in active_symbols:")
    nested_insert = function.index("async with own_db.begin_nested():")
    insert_sql = function.index("_INSERT_STRATEGY_LAB_SQL")

    assert active_load < active_skip < nested_insert < insert_sql


def test_strategy_lab_block_prefilters_active_symbols_and_uses_savepoint():
    source = _shadow_service_source()
    function = source.split("async def create_strategy_lab_rejected_shadows", 1)[1]

    active_load = function.index("active_symbols = await _load_active_profile_shadow_symbols(")
    active_skip = function.index("if symbol in active_symbols:")
    nested_insert = function.index("async with own_db.begin_nested():")
    insert_sql = function.index("_INSERT_STRATEGY_LAB_SQL")

    assert active_load < active_skip < nested_insert < insert_sql
