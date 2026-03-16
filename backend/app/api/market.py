from fastapi import APIRouter, HTTPException
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["Market"])

GATE_IO_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"


def _format_volume(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.2f}"


@router.get("/spot-currencies")
async def get_spot_currencies():
    """Fetch spot USDT trading pairs from Gate.io public API ranked by 24h volume."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(GATE_IO_TICKERS_URL)
            response.raise_for_status()
            tickers = response.json()

        # Keep only USDT quote pairs
        usdt_tickers = [
            t for t in tickers
            if isinstance(t, dict) and t.get("currency_pair", "").endswith("_USDT")
        ]

        # Rank by 24h USD volume (quote_volume)
        usdt_tickers.sort(
            key=lambda x: float(x.get("quote_volume", 0) or 0),
            reverse=True,
        )

        result = []
        for rank, ticker in enumerate(usdt_tickers, 1):
            currency_pair = ticker.get("currency_pair", "")
            base = currency_pair.replace("_USDT", "")
            quote_volume = float(ticker.get("quote_volume", 0) or 0)
            last_price = float(ticker.get("last", 0) or 0)
            change_pct = float(ticker.get("change_percentage", 0) or 0)

            result.append({
                "rank": rank,
                "symbol": currency_pair.replace("_", ""),  # e.g. BTCUSDT
                "base": base,
                "last_price": last_price,
                "change_24h": change_pct,
                "volume_24h": quote_volume,
                "volume_24h_formatted": _format_volume(quote_volume),
            })

        return {"status": "success", "currencies": result, "total": len(result)}

    except httpx.HTTPStatusError as e:
        logger.error(f"Gate.io API returned error: {e.response.status_code}")
        raise HTTPException(
            status_code=502,
            detail=f"Gate.io API error: {e.response.status_code}",
        )
    except Exception as e:
        logger.exception(f"Failed to fetch spot currencies: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch market data: {str(e)}",
        )
