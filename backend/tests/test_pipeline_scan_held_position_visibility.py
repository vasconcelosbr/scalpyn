from pathlib import Path


def _pipeline_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "app"
        / "tasks"
        / "pipeline_scan.py"
    ).read_text(encoding="utf-8")


def test_pool_query_reincludes_held_symbols_with_an_open_l3_shadow():
    """2026-09-27 regression: the POOL-level candidate query excluded every
    held_for_open_position=true PoolCoin unconditionally. Since this is the
    query _run_pipeline_scan uses to compute `symbols`, and `symbols`
    propagates through `_intersect_with_upstream` from POOL -> L1 -> L2 ->
    L3, excluding a held symbol here forced level_direction='down' at every
    level -- hiding it from Aprovado/L3 Consolidado even while it had a
    RUNNING L3 shadow trade. This is the exact symptom #209 fixed for the
    radar-cascade path (cascade_invalidate_removed_symbols), recurring here
    through this independent query. Confirmed live for ZEC_USDT,
    ONDO_USDT and LINK_USDT, each hidden despite a RUNNING L3 shadow.

    The fix re-includes a held symbol in the POOL query when it has an
    open (PENDING/RUNNING) L3 shadow trade, so it keeps propagating and
    stays visible. This cannot reopen a second position: consolidation's
    own ACTIVE_TRADE_ALREADY_EXISTS / find_active_l3_shadow() check already
    blocks that regardless of whether the symbol is visible upstream. A
    held symbol with no open L3 shadow (a stale/transient flag) still stays
    excluded.
    """
    source = _pipeline_source()
    start = source.index("elif source_pool_id:")
    end = source.index("symbols = filter_real_assets([_normalize_sym(c.symbol) for c in coin_rows])", start)
    snippet = source[start:end]

    assert "ShadowTrade.source == \"L3\"" in snippet
    assert "PENDING" in snippet and "RUNNING" in snippet
    assert "held_with_open_trade" in snippet
    assert "PoolCoin.held_for_open_position == False" in snippet
    assert "PoolCoin.symbol.in_(held_with_open_trade)" in snippet
    # the two conditions must be OR'd, not AND'd -- an AND would still
    # exclude every held symbol regardless of an open shadow trade.
    or_idx = snippet.index("or_(")
    held_false_idx = snippet.index("PoolCoin.held_for_open_position == False")
    symbol_in_idx = snippet.index("PoolCoin.symbol.in_(held_with_open_trade)")
    assert or_idx < held_false_idx < symbol_in_idx
