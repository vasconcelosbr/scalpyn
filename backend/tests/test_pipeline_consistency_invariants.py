import os
import sys
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.pipeline_scan import _intersect_with_upstream, _normalize_sources_for_scan
from app.api.watchlists import _normalize_and_validate_watchlist_sources


def test_rejected_pool_asset_never_appears_in_l1_l2_l3():
    execution_id = str(uuid4())
    pool_symbols = {"BTC_USDT", "ETH_USDT"}
    l1_symbols = _intersect_with_upstream(
        symbols=["BTC_USDT", "ETH_USDT", "OFC_USDT"],
        upstream_symbols=pool_symbols,
        level="L1",
        watchlist_id="wl-l1",
        execution_id=execution_id,
    )
    l2_symbols = _intersect_with_upstream(
        symbols=l1_symbols + ["OFC_USDT"],
        upstream_symbols=set(l1_symbols),
        level="L2",
        watchlist_id="wl-l2",
        execution_id=execution_id,
    )
    l3_symbols = _intersect_with_upstream(
        symbols=l2_symbols + ["OFC_USDT"],
        upstream_symbols=set(l2_symbols),
        level="L3",
        watchlist_id="wl-l3",
        execution_id=execution_id,
    )

    assert "OFC_USDT" not in l1_symbols
    assert "OFC_USDT" not in l2_symbols
    assert "OFC_USDT" not in l3_symbols
    assert set(l1_symbols).issubset(pool_symbols)
    assert set(l2_symbols).issubset(set(l1_symbols))
    assert set(l3_symbols).issubset(set(l2_symbols))


def test_asset_removed_from_pool_disappears_on_next_cycle():
    execution_id = str(uuid4())
    first_cycle = _intersect_with_upstream(
        symbols=["BTC_USDT", "ETH_USDT"],
        upstream_symbols={"BTC_USDT", "ETH_USDT"},
        level="L1",
        watchlist_id="wl-l1",
        execution_id=execution_id,
    )
    second_cycle = _intersect_with_upstream(
        symbols=first_cycle,
        upstream_symbols={"BTC_USDT"},  # ETH removed from pool between cycles
        level="L1",
        watchlist_id="wl-l1",
        execution_id=execution_id,
    )
    assert second_cycle == ["BTC_USDT"]


def test_invalid_dual_source_config_normalizes_to_watchlist_for_l2():
    pool_id, watchlist_id = _normalize_sources_for_scan(
        level="L2",
        watchlist_id="wl-l2",
        source_pool_id="pool-1",
        source_watchlist_id="wl-l1",
        execution_id=str(uuid4()),
    )
    assert pool_id is None
    assert watchlist_id == "wl-l1"


def test_api_validation_rejects_invalid_source_for_levels():
    with pytest.raises(HTTPException):
        _normalize_and_validate_watchlist_sources(
            level="POOL",
            source_pool_id=None,
            source_watchlist_id=None,
        )
    with pytest.raises(HTTPException):
        _normalize_and_validate_watchlist_sources(
            level="L1",
            source_pool_id=uuid4(),
            source_watchlist_id=uuid4(),
        )

