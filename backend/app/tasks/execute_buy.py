"""Celery Task — Buy Execution Engine (Spot, driven by SpotEngineConfig).

Pipeline: alpha_scores → capital_check → block_check → order → trade record.

Runs every 60 seconds via Celery Beat.
"""

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import select, text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_COOLDOWN_PREFIX = "spe:cd:"   # Redis key prefix for symbol cooldown


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── async core ──────────────────────────────────────────────────────────────

async def _execute_buy_cycle_async() -> dict:
    from ..database import AsyncSessionLocal
    from ..models.user import User
    from ..models.pool import Pool
    from ..models.config_profile import ConfigProfile
    from ..models.exchange_connection import ExchangeConnection
    from ..schemas.spot_engine_config import SpotEngineConfig
    from ..engines.spot_capital_manager import SpotCapitalManager
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..services.execution_engine import execution_engine
    from ..services.block_engine import BlockEngine
    from ..services.config_service import config_service
    from ..services.notification_service import notification_service
    from ..utils.encryption import decrypt
    from ..config import settings

    stats = {"users_processed": 0, "trades_placed": 0, "skipped": 0, "errors": 0}

    # Lazy Redis client for cooldown tracking (no hard dependency)
    _redis: Optional[object] = None
    try:
        import redis as redis_lib
        _redis = redis_lib.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    except Exception as exc:
        logger.warning("Redis cooldown tracking unavailable: %s", exc)

    def _is_on_cooldown(user_id: str, symbol: str, cooldown_s: int) -> bool:
        if not _redis or cooldown_s <= 0:
            return False
        try:
            key = f"{_COOLDOWN_PREFIX}{user_id}:{symbol}"
            return bool(_redis.exists(key))
        except Exception:
            return False

    def _set_cooldown(user_id: str, symbol: str, cooldown_s: int) -> None:
        if not _redis or cooldown_s <= 0:
            return
        try:
            key = f"{_COOLDOWN_PREFIX}{user_id}:{symbol}"
            _redis.setex(key, cooldown_s, "1")
        except Exception:
            pass

    async with AsyncSessionLocal() as db:
        # 1. All active users that have a spot_engine config
        cfg_rows = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.config_type == "spot_engine",
                ConfigProfile.is_active == True,
            )
        )
        cfg_rows = cfg_rows.scalars().all()

        if not cfg_rows:
            logger.debug("No active spot_engine configs found.")
            return stats

        for cfg_row in cfg_rows:
            user_id = cfg_row.user_id
            try:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                threshold = se_cfg.scanner.buy_threshold_score
                max_opps = se_cfg.scanner.max_opportunities_per_scan
                cooldown_s = se_cfg.scanner.symbol_cooldown_seconds

                # 2. Load exchange connection (Gate.io only for now)
                exc_row_res = await db.execute(
                    select(ExchangeConnection).where(
                        ExchangeConnection.user_id == user_id,
                        ExchangeConnection.is_active == True,
                    )
                )
                exc_row = exc_row_res.scalars().first()

                adapter: Optional[GateAdapter] = None
                paper_mode = True

                if exc_row:
                    try:
                        raw_key    = bytes(exc_row.api_key_encrypted)    if isinstance(exc_row.api_key_encrypted, memoryview)    else exc_row.api_key_encrypted
                        raw_secret = bytes(exc_row.api_secret_encrypted) if isinstance(exc_row.api_secret_encrypted, memoryview) else exc_row.api_secret_encrypted
                        api_key    = decrypt(raw_key).strip()
                        api_secret = decrypt(raw_secret).strip()
                        adapter    = GateAdapter(api_key, api_secret)
                    except Exception as exc:
                        logger.warning("Failed to build adapter for user %s: %s", user_id, exc)

                # Determine mode from pool
                pool_res = await db.execute(
                    select(Pool).where(Pool.user_id == user_id, Pool.is_active == True).limit(1)
                )
                pool = pool_res.scalars().first()
                if pool:
                    paper_mode = pool.mode != "live"

                # 3. Capital state
                usdt_balance = 0.0
                if adapter and not paper_mode:
                    try:
                        spot_accounts = await adapter.get_spot_balance()
                        usdt_balance = next(
                            (float(a.get("available", 0)) for a in spot_accounts if a.get("currency") == "USDT"),
                            0.0,
                        )
                    except Exception as exc:
                        logger.warning("Could not fetch balance for user %s: %s", user_id, exc)
                else:
                    # Paper mode: use virtual balance (invested positions tracked in DB)
                    # Set a generous default so position-count / exposure limits still apply
                    usdt_balance = 100_000.0

                capital_mgr = SpotCapitalManager(se_cfg.buying)
                state = await capital_mgr.get_state(usdt_balance, db, str(user_id))

                allowed, reason = capital_mgr.can_open_new_position(state)
                if not allowed:
                    logger.info("Buy blocked for user %s — %s", user_id, reason)
                    stats["skipped"] += 1
                    continue

                trade_size_usdt = capital_mgr.calc_trade_size(state)

                # 4. Fetch top-scoring candidates from last 2 minutes
                ranked_res = await db.execute(text("""
                    SELECT DISTINCT ON (a.symbol)
                        a.symbol,
                        a.score,
                        i.indicators_json
                    FROM alpha_scores a
                    JOIN indicators i ON a.symbol = i.symbol
                    WHERE a.time   > now() - interval '2 minutes'
                      AND i.time   > now() - interval '2 minutes'
                      AND a.score >= :threshold
                    ORDER BY a.symbol, a.time DESC, a.score DESC
                    LIMIT :limit
                """), {"threshold": threshold, "limit": max_opps * 5})
                candidates = ranked_res.fetchall()

                if not candidates:
                    logger.debug("No qualifying candidates for user %s (threshold=%.1f)", user_id, threshold)
                    continue

                # Sort by score descending so best opportunities go first
                candidates = sorted(candidates, key=lambda r: float(r.score), reverse=True)

                # 5. Block engine (from "block" ConfigProfile, optional)
                block_config = await config_service.get_config(db, "block", user_id)
                block_engine = BlockEngine(block_config) if block_config else None

                buys_this_cycle = 0

                for row in candidates:
                    if buys_this_cycle >= max_opps:
                        break

                    symbol     = row.symbol
                    alpha_score = float(row.score)
                    indicators  = row.indicators_json or {}
                    current_price = float(indicators.get("close", 0))

                    if current_price <= 0:
                        logger.debug("Skipping %s — no close price", symbol)
                        stats["skipped"] += 1
                        continue

                    # 5a. Symbol cooldown
                    if _is_on_cooldown(str(user_id), symbol, cooldown_s):
                        logger.debug("Skipping %s — on cooldown", symbol)
                        stats["skipped"] += 1
                        continue

                    # 5b. Block rules
                    if block_engine:
                        block_result = block_engine.evaluate(indicators)
                        if block_result.get("blocked"):
                            logger.info(
                                "Signal for %s blocked for user %s: %s",
                                symbol, user_id, block_result.get("triggered_blocks"),
                            )
                            stats["skipped"] += 1
                            continue

                    # 5c. Per-asset capital check
                    asset_ok, asset_reason = await capital_mgr.can_trade_asset(
                        symbol, trade_size_usdt, state, db, str(user_id)
                    )
                    if not asset_ok:
                        logger.debug("Asset check failed %s for user %s: %s", symbol, user_id, asset_reason)
                        stats["skipped"] += 1
                        continue

                    # 5d. Build risk params for ExecutionEngine
                    quantity = trade_size_usdt / current_price
                    tp_pct   = se_cfg.selling.take_profit_pct
                    tp_price = round(current_price * (1 + tp_pct / 100), 8)
                    # No stop-loss hardcode — sell engine handles exits
                    risk_params = {
                        "quantity":        round(quantity, 8),
                        "invested_value":  round(trade_size_usdt, 2),
                        "order_type":      se_cfg.buying.order_type,
                        "take_profit_price": tp_price,
                        "stop_loss_price":   None,
                    }

                    # 5e. Execute trade
                    trade_result = await execution_engine.execute_trade(
                        db=db,
                        user_id=user_id,
                        pool_id=pool.id if pool else None,
                        symbol=symbol,
                        direction="long",
                        market_type="spot",
                        risk_params=risk_params,
                        indicators=indicators,
                        alpha_score=alpha_score,
                        exchange_name=exc_row.exchange_name if exc_row else "gate.io",
                        paper_mode=paper_mode,
                    )

                    if trade_result.get("success"):
                        logger.info(
                            "[BUY] %s %s @ %.8f | size=%.2f USDT | score=%.1f | %s",
                            "PAPER" if paper_mode else "LIVE",
                            symbol, current_price, trade_size_usdt, alpha_score,
                            trade_result.get("trade_id", ""),
                        )
                        _set_cooldown(str(user_id), symbol, cooldown_s)
                        buys_this_cycle += 1
                        stats["trades_placed"] += 1

                        await notification_service.send_trade_alert(
                            db, user_id, "buy",
                            {"symbol": symbol, "price": current_price, "score": alpha_score},
                        )
                    else:
                        logger.warning(
                            "Trade failed for %s user %s: %s",
                            symbol, user_id, trade_result.get("error"),
                        )
                        stats["errors"] += 1

                stats["users_processed"] += 1

            except Exception as exc:
                logger.exception("Error in buy cycle for user %s: %s", user_id, exc)
                stats["errors"] += 1
                continue

    logger.info(
        "Buy cycle complete — users=%d  trades=%d  skipped=%d  errors=%d",
        stats["users_processed"], stats["trades_placed"], stats["skipped"], stats["errors"],
    )
    return stats


# ─── Celery task ─────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.execute_buy.run_buy_cycle", bind=True, max_retries=0)
def run_buy_cycle(self):
    """Periodic buy-execution cycle driven by SpotEngineConfig."""
    logger.info("Starting buy cycle task...")
    try:
        result = _run_async(_execute_buy_cycle_async())
        logger.info("Buy cycle result: %s", result)
        return result
    except Exception as exc:
        logger.exception("Buy cycle task failed: %s", exc)
        raise
