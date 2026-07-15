from pathlib import Path


def test_optional_market_metadata_query_is_isolated_before_fallback():
    """A missing optional column must not poison the request transaction."""
    source = (
        Path(__file__).parents[1] / "app" / "api" / "watchlists.py"
    ).read_text(encoding="utf-8")
    function = source.split(
        "async def _get_watchlist_rejections_payload", 1
    )[1].split("async def get_watchlist_rejected", 1)[0]

    savepoint = function.index("async with db.begin_nested():")
    refresh = function.index("await _auto_refresh_watchlist_assets_if_needed")
    optional_query = function.index("spread_pct, orderbook_depth_usdt")
    fallback_query = function.index(
        "SELECT symbol, price, price_change_24h, volume_24h, market_cap\n"
        "                        FROM market_metadata"
    )

    assert savepoint < refresh < optional_query < fallback_query
    assert function.count("async with db.begin_nested():") >= 2
