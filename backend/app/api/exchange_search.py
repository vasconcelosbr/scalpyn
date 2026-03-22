"""Exchange Search API — autocomplete for Gate.io pairs/contracts."""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exchange", tags=["Exchange Search"])


@router.get("/search")
async def search_exchange_pairs(
    q: str = Query(default="", description="Search query (e.g. 'BTC', 'ETH_USDT')"),
    market: str = Query(default="spot", description="Market type: 'spot' or 'futures'"),
    _user_id=Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Autocomplete search for Gate.io trading pairs / futures contracts.

    Returns top 10 matches with symbol, base, quote, market_type.
    Uses public Gate.io endpoints — no exchange credentials required.
    """
    from ..exchange_adapters.gate_adapter import GateAdapter

    if not q.strip():
        return {"results": [], "query": q, "market": market}

    adapter = GateAdapter(api_key="", api_secret="")
    try:
        results: List[Dict[str, Any]] = await adapter.search_pairs(q.strip(), market_type=market)
    except Exception as e:
        logger.warning(f"Exchange search failed for q={q!r}: {e}")
        return {"results": [], "query": q, "market": market, "error": str(e)}

    return {"results": results, "query": q, "market": market}
