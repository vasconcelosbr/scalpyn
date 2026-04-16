from fastapi import APIRouter, HTTPException
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["Market"])

GATE_IO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/tickers"
GATE_IO_FUTURES_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


def _format_value(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.2f}"


async def _fetch_market_caps() -> dict:
    """Fetch market cap data for top 250 coins from CoinGecko."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                COINGECKO_MARKETS_URL,
                params={
                    "vs_currency": "usd",
                    "per_page": 250,
                    "page": 1,
                    "order": "market_cap_desc",
                },
            )
            resp.raise_for_status()
            coins = resp.json()
            return {
                c["symbol"].upper(): c.get("market_cap")
                for c in coins
                if c.get("market_cap")
            }
    except Exception as e:
        logger.warning(f"Failed to fetch market caps from CoinGecko: {e}")
        return {}


@router.get("/spot-currencies")
async def get_spot_currencies():
    """Fetch spot USDT trading pairs from Gate.io public API ranked by 24h volume."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(GATE_IO_SPOT_URL)
            response.raise_for_status()
            tickers = response.json()

        market_caps = await _fetch_market_caps()

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
            mcap = market_caps.get(base.upper())

            result.append({
                "rank": rank,
                "symbol": currency_pair,  # e.g. BTC_USDT (keep underscore for market_metadata consistency)
                "base": base,
                "last_price": last_price,
                "change_24h": change_pct,
                "volume_24h": quote_volume,
                "volume_24h_formatted": _format_value(quote_volume),
                "market_cap": mcap,
                "market_cap_formatted": _format_value(mcap) if mcap else None,
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


@router.get("/futures-currencies")
async def get_futures_currencies():
    """Fetch USDT perpetual futures contracts from Gate.io ranked by 24h volume."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(GATE_IO_FUTURES_URL)
            response.raise_for_status()
            tickers = response.json()

        market_caps = await _fetch_market_caps()

        # Rank by 24h volume in USD
        tickers.sort(
            key=lambda x: float(x.get("volume_24h_settle", 0) or 0),
            reverse=True,
        )

        result = []
        for rank, ticker in enumerate(tickers, 1):
            contract = ticker.get("contract", "")  # e.g. BTC_USDT
            base = contract.replace("_USDT", "")
            volume_usd = float(ticker.get("volume_24h_settle", 0) or 0)
            last_price = float(ticker.get("last", 0) or 0)
            change_pct = float(ticker.get("change_percentage", 0) or 0)
            mcap = market_caps.get(base.upper())

            result.append({
                "rank": rank,
                "symbol": contract,  # e.g. BTC_USDT (keep underscore for market_metadata consistency)
                "base": base,
                "last_price": last_price,
                "change_24h": change_pct,
                "volume_24h": volume_usd,
                "volume_24h_formatted": _format_value(volume_usd),
                "market_cap": mcap,
                "market_cap_formatted": _format_value(mcap) if mcap else None,
                "is_futures": True,
            })

        return {"status": "success", "currencies": result, "total": len(result)}

    except httpx.HTTPStatusError as e:
        logger.error(f"Gate.io futures API returned error: {e.response.status_code}")
        raise HTTPException(
            status_code=502,
            detail=f"Gate.io API error: {e.response.status_code}",
        )
    except Exception as e:
        logger.exception(f"Failed to fetch futures currencies: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch futures data: {str(e)}",
        )
