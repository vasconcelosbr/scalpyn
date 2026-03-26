"""Celery Task — Buy Execution Engine (Spot, driven by SpotEngineConfig).

Pipeline per candidate:
  alpha_scores (≥ threshold, last 2 min)
  → symbol cooldown check
  → profile filters (market_cap, volume, etc. from Pool's linked Profile)
  → entry triggers (SignalEngine / "signal" ConfigProfile)
  → block rules (BlockEngine / "block" ConfigProfile)
  → capital check (SpotCapitalManager — global + per-asset)
  → ExecutionEngine.execute_trade()
  → notification

Runs every 60 seconds via Celery Beat.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import select, text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_COOLDOWN_PREFIX = "spe:cd:"   # Redis key prefix for per-symbol cooldown


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── async core ──────────────────────────────────────────────────────────────

async def _execute_buy_cycle_async() -> dict:
    from ..database import AsyncSessionLocal
    from ..models.pool import Pool
    from ..models.config_profile import ConfigProfile
    from ..models.exchange_connection import ExchangeConnection
    from ..schemas.spot_engine_config import SpotEngineConfig
    from ..engines.spot_capital_manager import SpotCapitalManager
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..services.execution_engine import execution_engine
    from ..services.signal_engine import SignalEngine
    from ..services.block_engine import BlockEngine
    from ..services.config_service import config_service
    from ..services.notification_service import notification_service
    from ..utils.encryption import decrypt
    from ..config import settings

    stats = {"users_processed": 0, "trades_placed": 0, "skipped": 0, "errors": 0}

    # Redis client for symbol cooldown tracking (soft dependency — skipped if unavailable)
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
            return bool(_redis.exists(f"{_COOLDOWN_PREFIX}{user_id}:{symbol}"))
        except Exception:
            return False

    def _set_cooldown(user_id: str, symbol: str, cooldown_s: int) -> None:
        if not _redis or cooldown_s <= 0:
            return
        try:
            _redis.setex(f"{_COOLDOWN_PREFIX}{user_id}:{symbol}", cooldown_s, "1")
        except Exception:
            pass

    def _apply_profile_filters(
        market_cap: Optional[float],
        indicators: dict,
        conditions: list,
        logic: str = "AND",
    ) -> bool:
        """Evaluate profile filter conditions against a candidate.

        Fields resolved in order: market_cap (from DB join), then indicators_json.
        Returns True if the candidate passes all filters (AND) or any filter (OR).
        """
        if not conditions:
            return True

        results = []
        for cond in conditions:
            field = cond.get("field") or cond.get("indicator", "")
            operator = cond.get("operator", ">")
            threshold = cond.get("value")
            if threshold is None:
                continue

            if field == "market_cap":
                actual = market_cap
            else:
                raw = indicators.get(field)
                actual = float(raw) if raw is not None else None

            if actual is None:
                results.append(False)
                continue

            try:
                actual = float(actual)
                threshold = float(threshold)
            except (TypeError, ValueError):
                results.append(False)
                continue

            if operator in (">", "gt"):
                results.append(actual > threshold)
            elif operator in (">=", "gte"):
                results.append(actual >= threshold)
            elif operator in ("<", "lt"):
                results.append(actual < threshold)
            elif operator in ("<=", "lte"):
                results.append(actual <= threshold)
            elif operator in ("==", "=", "eq"):
                results.append(actual == threshold)
            else:
                results.append(True)

        if not results:
            return True
        return all(results) if logic.upper() == "AND" else any(results)

    async with AsyncSessionLocal() as db:
        # 1. Find all active spot_engine configs (one per user, usually)
        cfg_rows = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.config_type == "spot_engine",
                ConfigProfile.is_active == True,
            )
        )
        cfg_rows = cfg_rows.scalars().all()

        if not cfg_rows:
            logger.debug("No active spot_engine configs found — skipping buy cycle.")
            return stats

        for cfg_row in cfg_rows:
            user_id = cfg_row.user_id
            try:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                threshold   = se_cfg.scanner.buy_threshold_score
                max_opps    = se_cfg.scanner.max_opportunities_per_scan
                cooldown_s  = se_cfg.scanner.symbol_cooldown_seconds

                # 2. Exchange connection (Gate.io)
                exc_res = await db.execute(
                    select(ExchangeConnection).where(
                        ExchangeConnection.user_id == user_id,
                        ExchangeConnection.is_active == True,
                    )
                )
                exc_row = exc_res.scalars().first()

                adapter: Optional[GateAdapter] = None
                if exc_row:
                    try:
                        raw_key    = bytes(exc_row.api_key_encrypted)    if isinstance(exc_row.api_key_encrypted,    memoryview) else exc_row.api_key_encrypted
                        raw_secret = bytes(exc_row.api_secret_encrypted) if isinstance(exc_row.api_secret_encrypted, memoryview) else exc_row.api_secret_encrypted
                        api_key    = decrypt(raw_key).strip()
                        api_secret = decrypt(raw_secret).strip()
                        adapter    = GateAdapter(api_key, api_secret)
                    except Exception as exc:
                        logger.warning("Failed to build GateAdapter for user %s: %s", user_id, exc)

                # 3. Pool → paper/live mode
                pool_res  = await db.execute(
                    select(Pool).where(Pool.user_id == user_id, Pool.is_active == True).limit(1)
                )
                pool       = pool_res.scalars().first()
                paper_mode = True if not pool else (pool.mode != "live")

                # 3b. Load profile filter conditions (market_cap, volume, etc.)
                profile_filter_conditions: list = []
                profile_filter_logic: str = "AND"
                if pool and pool.profile_id:
                    from ..models.profile import Profile
                    prof_res = await db.execute(
                        select(Profile).where(Profile.id == pool.profile_id)
                    )
                    prof = prof_res.scalars().first()
                    if prof and prof.config_json:
                        pf = prof.config_json.get("filters", {})
                        profile_filter_conditions = pf.get("conditions", [])
                        profile_filter_logic = pf.get("logic", "AND")

                # 4. USDT balance → capital state
                usdt_balance = 0.0
                if adapter and not paper_mode:
                    try:
                        spot_accounts = await adapter.get_spot_balance()
                        usdt_balance  = next(
                            (float(a.get("available", 0)) for a in spot_accounts if a.get("currency") == "USDT"),
                            0.0,
                        )
                    except Exception as exc:
                        logger.warning("Could not fetch balance for user %s: %s", user_id, exc)
                else:
                    # Paper mode: use a virtual balance large enough that position-count
                    # and exposure limits (from SpotEngineConfig) remain the binding factors.
                    usdt_balance = 100_000.0

                capital_mgr     = SpotCapitalManager(se_cfg.buying)
                state           = await capital_mgr.get_state(usdt_balance, db, str(user_id))
                allowed, reason = capital_mgr.can_open_new_position(state)

                if not allowed:
                    logger.info("Buy blocked for user %s — %s", user_id, reason)
                    stats["skipped"] += 1
                    continue

                trade_size_usdt = capital_mgr.calc_trade_size(state)

                # 5. Top-scoring candidates (last 2 min) — includes market_cap from metadata
                ranked_res = await db.execute(text("""
                    SELECT DISTINCT ON (a.symbol)
                        a.symbol,
                        a.score,
                        i.indicators_json,
                        mm.market_cap
                    FROM alpha_scores a
                    JOIN indicators i ON a.symbol = i.symbol
                    LEFT JOIN market_metadata mm ON mm.symbol = a.symbol
                    WHERE a.time > now() - interval '2 minutes'
                      AND i.time > now() - interval '2 minutes'
                      AND a.score >= :threshold
                    ORDER BY a.symbol, a.time DESC, a.score DESC
                    LIMIT :limit
                """), {"threshold": threshold, "limit": max_opps * 5})
                candidates = ranked_res.fetchall()

                if not candidates:
                    logger.debug(
                        "No qualifying candidates for user %s (threshold=%.1f)", user_id, threshold
                    )
                    continue

                # Sort best score first
                candidates = sorted(candidates, key=lambda r: float(r.score), reverse=True)

                # 6. Load entry-trigger + block config from the L3 pipeline watchlist profile
                # Chain: Pool → L1 pipeline watchlist (source_pool_id) → L2 → L3
                from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
                from ..models.profile import Profile as UserProfile

                signal_engine: Optional[SignalEngine] = None
                block_engine:  Optional[BlockEngine]  = None
                l3_symbols: Optional[set] = None  # restrict candidates to L3 assets

                try:
                    l1_res = await db.execute(
                        select(PipelineWatchlist).where(
                            PipelineWatchlist.source_pool_id == pool.id,
                            PipelineWatchlist.user_id == user_id,
                        ).limit(1)
                    )
                    l1_wl = l1_res.scalars().first()

                    if l1_wl:
                        l2_res = await db.execute(
                            select(PipelineWatchlist).where(
                                PipelineWatchlist.source_watchlist_id == l1_wl.id,
                                PipelineWatchlist.user_id == user_id,
                            ).limit(1)
                        )
                        l2_wl = l2_res.scalars().first()

                        if l2_wl:
                            l3_res = await db.execute(
                                select(PipelineWatchlist).where(
                                    PipelineWatchlist.source_watchlist_id == l2_wl.id,
                                    PipelineWatchlist.user_id == user_id,
                                ).limit(1)
                            )
                            l3_wl = l3_res.scalars().first()

                            if l3_wl:
                                # L3 candidate symbols
                                l3_assets_res = await db.execute(
                                    select(PipelineWatchlistAsset.symbol).where(
                                        PipelineWatchlistAsset.watchlist_id == l3_wl.id
                                    )
                                )
                                l3_symbols = {r[0] for r in l3_assets_res.fetchall()}

                                # L3 profile → signals + block_rules
                                if l3_wl.profile_id:
                                    l3_prof_res = await db.execute(
                                        select(UserProfile).where(UserProfile.id == l3_wl.profile_id)
                                    )
                                    l3_prof = l3_prof_res.scalars().first()
                                    if l3_prof and l3_prof.config:
                                        cfg = l3_prof.config
                                        sig_cfg = cfg.get("entry_triggers") or cfg.get("signals")
                                        blk_cfg = cfg.get("block_rules")
                                        if sig_cfg:
                                            signal_engine = SignalEngine(sig_cfg)
                                        if blk_cfg:
                                            block_engine = BlockEngine(blk_cfg)
                except Exception as _pipeline_exc:
                    logger.warning("Could not load L3 pipeline profile for user %s: %s", user_id, _pipeline_exc)

                # Fallback: load from legacy ConfigProfile records if pipeline not found
                if signal_engine is None:
                    signal_config = await config_service.get_config(db, "signal", user_id)
                    if signal_config:
                        signal_engine = SignalEngine(signal_config)
                if block_engine is None:
                    block_config = await config_service.get_config(db, "block", user_id)
                    if block_config:
                        block_engine = BlockEngine(block_config)

                buys_this_cycle = 0

                # Restrict candidates to L3 pipeline watchlist symbols (if pipeline is configured)
                if l3_symbols is not None:
                    before_l3 = len(candidates)
                    candidates = [r for r in candidates if r.symbol in l3_symbols]
                    logger.info(
                        "L3 pipeline filter for user %s: %d → %d candidates (%d removed)",
                        user_id, before_l3, len(candidates), before_l3 - len(candidates),
                    )

                for row in candidates:
                    if buys_this_cycle >= max_opps:
                        break

                    symbol      = row.symbol
                    alpha_score = float(row.score)
                    indicators  = row.indicators_json or {}
                    candidate_market_cap = float(row.market_cap) if row.market_cap else None
                    current_price = float(indicators.get("close", 0))

                    if current_price <= 0:
                        logger.debug("Skipping %s — missing close price", symbol)
                        stats["skipped"] += 1
                        continue

                    # 6a. Symbol cooldown
                    if _is_on_cooldown(str(user_id), symbol, cooldown_s):
                        logger.debug("Skipping %s — on cooldown", symbol)
                        stats["skipped"] += 1
                        continue

                    # 6a2. Profile filter conditions (market_cap, volume_24h, etc.)
                    if profile_filter_conditions:
                        if not _apply_profile_filters(
                            candidate_market_cap,
                            indicators,
                            profile_filter_conditions,
                            profile_filter_logic,
                        ):
                            logger.debug(
                                "Profile filters not met for %s (user %s) — market_cap=%.0f",
                                symbol, user_id, candidate_market_cap or 0,
                            )
                            stats["skipped"] += 1
                            continue

                    # 6b. Entry triggers (SignalEngine)
                    if signal_engine:
                        signal_result = signal_engine.evaluate(indicators, alpha_score)
                        if not signal_result.get("signal"):
                            logger.debug(
                                "Entry triggers not met for %s (user %s): failed=%s",
                                symbol, user_id, signal_result.get("failed_required"),
                            )
                            stats["skipped"] += 1
                            continue

                    # 6c. Block rules (BlockEngine)
                    if block_engine:
                        block_result = block_engine.evaluate(indicators)
                        if block_result.get("blocked"):
                            logger.info(
                                "Buy for %s blocked (user %s): %s",
                                symbol, user_id, block_result.get("triggered_blocks"),
                            )
                            stats["skipped"] += 1
                            continue

                    # 6d. Per-asset capital check
                    asset_ok, asset_reason = await capital_mgr.can_trade_asset(
                        symbol, trade_size_usdt, state, db, str(user_id)
                    )
                    if not asset_ok:
                        logger.debug(
                            "Asset capital check failed %s (user %s): %s",
                            symbol, user_id, asset_reason,
                        )
                        stats["skipped"] += 1
                        continue

                    # 6e. Compute risk params
                    quantity = trade_size_usdt / current_price
                    tp_pct   = se_cfg.selling.take_profit_pct
                    tp_price = round(current_price * (1 + tp_pct / 100), 8)
                    risk_params = {
                        "quantity":          round(quantity, 8),
                        "invested_value":    round(trade_size_usdt, 2),
                        "order_type":        se_cfg.buying.order_type,
                        "take_profit_price": tp_price,
                        "stop_loss_price":   None,  # sell engine manages exits
                    }

                    # 6f. Execute
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
                            "[BUY][%s] %s @ %.8f | size=%.2f USDT | score=%.1f | id=%s",
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
                            "Trade failed %s (user %s): %s",
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

@celery_app.task(name="app.tasks.execute_buy.execute_buy_cycle", bind=True, max_retries=0)
def execute_buy_cycle(self):
    """Periodic buy-execution cycle (60 s) driven by SpotEngineConfig.

    Pipeline: alpha_scores → entry triggers → block rules →
              capital check → ExecutionEngine → notification.
    """
    logger.info("Starting buy execution cycle...")
    try:
        result = _run_async(_execute_buy_cycle_async())
        logger.info("Buy cycle result: %s", result)
        return result
    except Exception as exc:
        logger.exception("Buy execution cycle failed: %s", exc)
        raise
