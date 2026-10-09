"""Supply missing declared L3 identities without changing profile rules."""
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)


def closed_di_candidates(rows, config, identity, *, now, required):
    """Use the canonical ADX formula only on a complete governed candle series."""
    import pandas as pd
    from .feature_engine import FeatureEngine

    series = sorted(rows, key=lambda row: row["time"])
    duration = timedelta(minutes=1)
    if len(series) < required:
        return []
    if any(row.get("is_closed") is not True or not row.get("capture_contract_version")
           or row.get("ingested_at") is None or row["ingested_at"] > now
           or row["time"] + duration > now
           or any(row.get(key) is None or not math.isfinite(float(row[key]))
                  for key in ("high", "low", "close")) for row in series):
        return []
    if any(b["time"] - a["time"] != duration for a, b in zip(series, series[1:])):
        return []
    if {row.get("exchange") for row in series} != {"gate.io"}:
        return []
    values = FeatureEngine(config)._calc_adx(pd.DataFrame(
        [{key: float(row[key]) for key in ("high", "low", "close")} for row in series]
    ))
    if any(values.get(key) is None for key in ("di_plus", "di_minus")):
        return []
    values = {key: values[key] for key in ("di_plus", "di_minus")}
    values["di_trend"] = values["di_plus"] > values["di_minus"]
    latest = series[-1]
    return [{"indicator": key, "actual": value, "source": "ohlcv",
             "source_provider": latest["exchange"], "provider_policy_id": "spot_gate_closed_ohlcv_v1",
             "timeframe": "1m", "period": config["adx"]["period"], "parameters": {},
             "candle_policy": "CLOSED_ONLY", "candle_closed": True, "request_bound": True,
             "source_timestamp": latest["time"], "computed_at": now, "available_at": now,
             "age_seconds": (now - latest["time"]).total_seconds(), "stale": False,
             "producer_version": "l3_declared_di_closed_1m_v1",
             "capture_contract_version": latest["capture_contract_version"],
             "source_ingested_at": latest["ingested_at"], "sample_count": len(series), **identity}
            for key, value in values.items()]


async def load_closed_di_inputs(db, symbols):
    from ..tasks.compute_mtf_indicators import _load_governed_indicator_config
    from ..tasks.compute_indicators import _derive_min_candles

    config, identity = await _load_governed_indicator_config(db)
    adx = config.get("adx") or {}
    if not adx.get("enabled") or not adx.get("period"):
        return {}
    required = max(_derive_min_candles(config, "1m"), int(adx["period"]) * 2 + 3)
    now = datetime.now(timezone.utc)
    rows = (await db.execute(text("""
        SELECT symbols.symbol, candles.*
          FROM unnest(CAST(:symbols AS text[])) AS symbols(symbol)
          CROSS JOIN LATERAL (
            SELECT time, high, low, close, exchange, is_closed, ingested_at, capture_contract_version
              FROM ohlcv WHERE symbol = symbols.symbol AND market_type = 'spot'
               AND timeframe = '1m' AND time <= :latest_closed_open
             ORDER BY time DESC LIMIT :limit
          ) candles
    """), {"symbols": symbols, "latest_closed_open": now - timedelta(minutes=1),
            "limit": required})).mappings().all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["symbol"], []).append(dict(row))
    return {symbol: closed_di_candidates(series, config, identity, now=now, required=required)
            for symbol, series in grouped.items()}


def declared_references(profile):
    for section in ("filters", "signals", "entry_triggers", "_global_entry_triggers"):
        for condition in (profile.get(section) or {}).get("conditions") or []:
            if condition.get("enabled", True) is not False:
                yield from ((condition.get("resolved_operands") or {}).values()
                            if condition.get("type") == "comparison" else [condition])


async def prepare_l3_contract_inputs(db, assets, profile, runtime_policy):
    """L3_DECLARED_INPUT_PROVENANCE: keep values and source evidence together.

    Scoped to the existing resolver allowlist. Missing observations remain
    missing and are rejected by the unchanged authorization contract.
    """
    resolver = runtime_policy.get("l3_v3_provenance_resolver") or {}
    if (db is None or not assets or not resolver.get("enabled")
            or str(runtime_policy.get("profile_id")) not in
            {str(value) for value in resolver.get("profile_allowlist") or []}):
        return assets
    references = list(declared_references(profile))
    wants_di = any(ref.get("source") == "ohlcv" and ref.get("timeframe") == "1m"
                   and (ref.get("indicator") or ref.get("field")) in {"di_trend", "di_plus", "di_minus"}
                   for ref in references)
    wants_spread = any(ref.get("source") == "live_order_book"
                       and (ref.get("indicator") or ref.get("field")) == "spread_pct"
                       for ref in references)
    if not wants_di and not wants_spread:
        return assets
    prepared = [{**asset, "indicators": dict(asset.get("indicators") or {}),
                 "_indicators_by_tf": {tf: dict(values) for tf, values in (asset.get("_indicators_by_tf") or {}).items()},
                 "_l3_contract_ohlcv_candidates": []} for asset in assets]
    spot = [asset for asset in prepared if not asset.get("is_futures")]
    if wants_di and spot:
        try:
            # A savepoint keeps a failed read from poisoning the scan transaction.
            async with db.begin_nested():
                candidates = await load_closed_di_inputs(db, [asset["symbol"] for asset in spot])
            for asset in spot:
                rows = candidates.get(asset["symbol"]) or []
                asset["_l3_contract_ohlcv_candidates"] = rows
                asset["_indicators_by_tf"].setdefault("1m", {}).update(
                    {row["indicator"]: row["actual"] for row in rows})
        except Exception:
            logger.exception("[L3_DECLARED_INPUTS] closed DI unavailable; contract remains fail-closed")
    if wants_spread:
        from .market_data_service import MarketDataService
        service = MarketDataService()
        for asset in spot:
            try:
                # Spread needs only best bid/ask, not a configured depth band.
                book = await service.fetch_raw_orderbook(asset["symbol"], limit=1)
                metrics = service._extract_orderbook_metrics(book, depth=1)
                timestamp = metrics.get("_source_timestamp")
                spread = metrics.get("spread_pct")
                if timestamp and spread is not None and math.isfinite(spread) and spread >= 0:
                    asset["_l3_live_order_book_snapshot"] = {
                        "values": {"spread_pct": spread},
                        "meta": {"source_provider": "gate", "provider_policy_id": "observed_order_book_v1",
                                 "snapshot": True, "source_timestamp": timestamp,
                                 "computed_at": timestamp, "available_at": timestamp}}
                    asset["indicators"]["spread_pct"] = asset["spread_pct"] = spread
            except Exception:
                logger.exception("[L3_DECLARED_INPUTS] book unavailable symbol=%s", asset.get("symbol"))
    return prepared
