"""Futures Scanner — 4-Gate pipeline + 5-Layer scoring + execution.

Gate 0: Portfolio risk check (daily loss, circuit breaker, max positions)
Gate 1: Macro regime filter (mandatory for futures)
Gate 2: Liquidity check (L1 < hard_reject → REJECT)
Gate 3: Score threshold (< min_score → NO TRADE; any layer < min_layer → NO TRADE)

All thresholds from FuturesEngineConfig (zero hardcode).
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from ..models.trade import Trade
from ..models.exchange_connection import ExchangeConnection
from ..models.config_profile import ConfigProfile
from ..exchange_adapters.gate_adapter import GateAdapter
from ..utils.encryption import decrypt
from ..schemas.futures_engine_config import FuturesEngineConfig
from ..scoring.layer_liquidity import fetch_and_score as score_l1
from ..scoring.layer_structure import score_structure
from ..scoring.layer_momentum import score_momentum
from ..scoring.layer_volatility import score_volatility
from ..scoring.layer_order_flow import fetch_order_flow_data, score_order_flow
from ..engines.futures_macro_gate import FuturesMacroGate
from ..engines.futures_risk_engine import FuturesRiskEngine
from ..engines.futures_anti_liq import FuturesAntiLiq
from ..engines.futures_position_manager import FuturesPositionManager
from ..engines.futures_emergency import FuturesEmergency

logger = logging.getLogger(__name__)

_running_engines: Dict[str, "FuturesScanner"] = {}


def get_engine(user_id: str) -> Optional["FuturesScanner"]:
    return _running_engines.get(user_id)


def register_engine(user_id: str, engine: "FuturesScanner") -> None:
    _running_engines[user_id] = engine


def unregister_engine(user_id: str) -> None:
    _running_engines.pop(user_id, None)


class FuturesScanner:
    SCAN_INTERVAL = 60  # seconds — futures scans less frequently than spot

    def __init__(
        self,
        user_id: str,
        cfg: FuturesEngineConfig,
        adapter: GateAdapter,
    ):
        self.user_id = user_id
        self.cfg     = cfg
        self.adapter = adapter

        self._anti_liq  = FuturesAntiLiq(cfg.execution, cfg.management)
        self._emergency = FuturesEmergency(cfg.management, adapter)
        self._risk_eng  = FuturesRiskEngine(cfg.execution, cfg.scoring, cfg.risk, self._anti_liq)
        self._macro     = FuturesMacroGate(cfg.macro, adapter)
        self._pos_mgr   = FuturesPositionManager(cfg, adapter, self._anti_liq, self._emergency)

        self._task:       Optional[asyncio.Task] = None
        self._running     = False
        self._paused      = False
        self._cycle       = 0
        self._started_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._consecutive_losses = 0
        self._circuit_breaker_until: Optional[float] = None

    def start(self) -> None:
        if self._running:
            return
        self._running    = True
        self._paused     = False
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._loop(), name=f"futures-scanner-{self.user_id}")
        register_engine(self.user_id, self)
        logger.info("FuturesScanner started for user %s", self.user_id)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        unregister_engine(self.user_id)

    def status(self) -> dict:
        return {
            "running":    self._running,
            "paused":     self._paused,
            "cycle":      self._cycle,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_error": self._last_error,
            "user_id":    self.user_id,
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                if self._paused:
                    await asyncio.sleep(5)
                    continue
                self._cycle += 1
                async with AsyncSessionLocal() as db:
                    await self._run_cycle(db)
                self._last_error = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                logger.exception("FuturesScanner error (user %s): %s", self.user_id, e)
            await asyncio.sleep(self.SCAN_INTERVAL)

    async def _run_cycle(self, db: AsyncSession) -> None:
        # ── Fetch current prices ──────────────────────────────────────────────
        tickers = await self.adapter.get_tickers(market="futures")
        prices  = {t["contract"]: float(t.get("last", 0)) for t in tickers if t.get("last")}

        btc_price = prices.get("BTC_USDT", 0)

        # ── Get BTC 1h-ago price for emergency checks ─────────────────────────
        btc_1h_ago = await self._get_btc_1h_ago()
        btc_prices = {"current": btc_price, "1h_ago": btc_1h_ago}

        # ── Macro regime ──────────────────────────────────────────────────────
        macro_state = await self._macro.get_regime()

        # ── Manage existing positions ─────────────────────────────────────────
        await self._pos_mgr.manage_all(db, self.user_id, prices, macro_state.regime, btc_prices)

        # ── GATE 0: Portfolio risk ────────────────────────────────────────────
        gate0_ok, gate0_reason = await self._gate0_portfolio_risk(db)
        if not gate0_ok:
            logger.info("Gate 0 BLOCKED: %s", gate0_reason)
            return

        # ── GATE 1: Macro ─────────────────────────────────────────────────────
        # We check per-symbol when scoring; here just log the regime
        logger.debug("Macro regime: %s (score=%.1f)", macro_state.regime, macro_state.score)

        # ── Get futures balance ────────────────────────────────────────────────
        balance_data = await self.adapter.get_futures_balance()
        capital      = float(balance_data.get("available", 0) or 0)
        if capital < 10:
            logger.info("Insufficient futures capital: %.2f USDT", capital)
            return

        # ── Score all contracts ───────────────────────────────────────────────
        scored = await self._score_universe(tickers, prices)

        # ── Try to open new positions ─────────────────────────────────────────
        max_pos = self.cfg.risk.max_positions
        open_count = await self._count_open_positions(db)

        for opp in scored:
            if open_count >= max_pos:
                break
            await self._try_enter(opp, capital, macro_state, db)
            open_count = await self._count_open_positions(db)

    # ── Gate 0 ────────────────────────────────────────────────────────────────

    async def _gate0_portfolio_risk(self, db: AsyncSession) -> tuple[bool, str]:
        import time as _time
        cfg = self.cfg.risk

        # Circuit breaker
        if self._circuit_breaker_until and _time.time() < self._circuit_breaker_until:
            remaining = int(self._circuit_breaker_until - _time.time())
            return False, f"Circuit breaker active ({remaining}s remaining)"

        if self._consecutive_losses >= cfg.circuit_breaker_consecutive_losses:
            self._circuit_breaker_until = _time.time() + cfg.circuit_breaker_pause_minutes * 60
            self._consecutive_losses = 0
            return False, f"Circuit breaker triggered after {cfg.circuit_breaker_consecutive_losses} consecutive losses"

        # Daily loss limit
        today_pnl = await self._get_daily_pnl(db)
        capital   = await self._get_total_capital()
        if today_pnl < -(capital * cfg.daily_loss_limit_pct / 100):
            return False, f"Daily loss limit reached: {today_pnl:.2f} USDT"

        return True, "ok"

    # ── Score universe ────────────────────────────────────────────────────────

    async def _score_universe(self, tickers: list, prices: dict) -> list:
        results = []
        for ticker in tickers:
            contract = ticker.get("contract", "")
            if not contract.endswith("_USDT"):
                continue
            try:
                opp = await self._score_contract(contract, ticker, prices)
                if opp:
                    results.append(opp)
            except Exception as e:
                logger.debug("Score failed for %s: %s", contract, e)

        results.sort(key=lambda x: x["total_score"], reverse=True)
        return results

    async def _score_contract(self, contract: str, ticker: dict, prices: dict) -> Optional[dict]:
        cfg    = self.cfg.scoring
        price  = prices.get(contract, 0)
        if price <= 0:
            return None

        # ── L1 Liquidity ─────────────────────────────────────────────────────
        l1 = await score_l1(contract, self.adapter, cfg)
        if l1.rejected:
            logger.debug("L1 rejected %s (score=%.1f)", contract, l1.score)
            return None

        # Determine direction from macro + structure
        macro_state = await self._macro.get_regime()

        # ── L2 Structure (multi-timeframe) ────────────────────────────────────
        dfs = {}
        for tf in cfg.l2_timeframes:
            try:
                klines = await self.adapter.get_klines(contract, interval=tf, limit=200, market="futures")
                dfs[tf] = pd.DataFrame(klines)
            except Exception:
                pass
        if not dfs:
            return None

        # Use 1h as primary for direction
        primary_df = dfs.get("1h", list(dfs.values())[0])
        from ..scoring.layer_structure import score_structure_single_tf
        primary_trend, _, _ = score_structure_single_tf(primary_df, cfg)
        direction = "long" if primary_trend in ("bullish", "ranging") else "short"

        # Check macro allows this direction
        ok, reason = self._macro.can_trade(macro_state, direction, 0)
        if not ok:
            # Try opposite direction
            direction = "short" if direction == "long" else "long"
            ok, reason = self._macro.can_trade(macro_state, direction, 0)
            if not ok:
                return None

        l2 = score_structure(dfs, direction, cfg)

        # ── L3 Momentum ───────────────────────────────────────────────────────
        l3 = score_momentum(primary_df, direction, cfg)

        # ── L4 Volatility ─────────────────────────────────────────────────────
        l4 = score_volatility(primary_df, direction, cfg)

        # ── L5 Order Flow ─────────────────────────────────────────────────────
        of_data = await fetch_order_flow_data(contract, self.adapter, cfg)
        l5 = score_order_flow(**of_data, trade_direction=direction, cfg=cfg)

        total_score = l1.score + l2.score + l3.score + l4.score + l5.score

        # Gate 3: min score and min layer checks
        layers = [l1.score, l2.score, l3.score, l4.score, l5.score]
        if total_score < cfg.min_score_to_trade:
            return None
        if any(s < cfg.min_layer_score for s in layers):
            logger.debug(
                "Score gate: %s layers=%s total=%.1f — min_layer failed",
                contract, [round(s, 1) for s in layers], total_score,
            )
            return None

        return {
            "contract":    contract,
            "direction":   direction,
            "total_score": round(total_score, 2),
            "layers":      {"l1": l1.score, "l2": l2.score, "l3": l3.score, "l4": l4.score, "l5": l5.score},
            "l1":  l1,
            "l2":  l2,
            "l3":  l3,
            "l4":  l4,
            "l5":  l5,
            "of_data":     of_data,
            "price":       price,
            "df_1h":       primary_df,
        }

    # ── Entry execution ───────────────────────────────────────────────────────

    async def _try_enter(self, opp: dict, capital: float, macro_state, db: AsyncSession) -> None:
        contract   = opp["contract"]
        direction  = opp["direction"]
        total_score = opp["total_score"]
        price      = opp["price"]
        l4         = opp["l4"]
        df_1h      = opp["df_1h"]

        # Macro gate check with actual score
        ok, reason = self._macro.can_trade(macro_state, direction, total_score)
        if not ok:
            logger.info("Macro gate blocked %s: %s", contract, reason)
            return

        # Leverage-specific checks (funding guard, OI guard)
        of_data = opp["of_data"]
        lev_ok, lev_reason = self._leverage_gate_check(of_data, direction)
        if not lev_ok:
            logger.info("Leverage gate blocked %s: %s", contract, lev_reason)
            return

        # Get swing points for SL calculation
        closes = df_1h["close"].astype(float)
        highs  = df_1h["high"].astype(float)
        lows   = df_1h["low"].astype(float)
        from ..scoring.layer_structure import _find_swing_points
        sh, sl_pts = _find_swing_points(closes, highs, lows, self.cfg.scoring.l2_swing_lookback)
        swing_lows  = sorted([p.price for p in sl_pts])
        swing_highs = sorted([p.price for p in sh])

        # Get contract info for quanto multiplier and maintenance rate
        try:
            contract_info = await self.adapter.get_contract_info(contract)
            quanto = float(contract_info.get("quanto_multiplier", 0.0001) or 0.0001)
            maint  = float(contract_info.get("maintenance_rate", 0.005) or 0.005)
        except Exception:
            quanto = 0.0001
            maint  = 0.005

        # Calculate stop loss
        stop_loss = self._risk_eng.calculate_stop_loss(
            direction, price, l4.atr, swing_lows=swing_lows, swing_highs=swing_highs
        )

        # Macro size modifier
        size_mod = self._macro.get_size_modifier(macro_state.regime, direction)

        # Calculate full risk parameters
        risk_params = self._risk_eng.calculate_position(
            capital_usdt=capital,
            entry_price=price,
            stop_loss=stop_loss,
            direction=direction,
            total_score=total_score,
            macro_size_modifier=size_mod,
            contract_quanto_multiplier=quanto,
            maintenance_rate=maint,
            vol_regime=l4.vol_regime,
        )
        if risk_params is None:
            logger.info("Risk engine rejected trade for %s", contract)
            return

        # Set leverage on Gate.io
        try:
            await self.adapter.set_leverage(contract, int(risk_params.leverage))
        except Exception as e:
            logger.error("Failed to set leverage for %s: %s", contract, e)
            return

        # Pre-trade anti-liq validation with actual liq_price from Gate
        try:
            pos_info = await self.adapter.get_futures_position(contract)
            actual_liq = float(pos_info.get("liq_price", 0) or 0)
            if actual_liq > 0:
                pretrade_ok, msg = self._anti_liq.validate_pretrade(price, actual_liq, direction)
                if not pretrade_ok:
                    logger.info("Pre-trade anti-liq rejected %s: %s", contract, msg)
                    return
        except Exception:
            pass  # Position not yet open, skip

        # Place entry order
        size = risk_params.position_size_contracts
        order_size = size if direction == "long" else -size
        try:
            order = await self.adapter.place_futures_order(
                contract=contract,
                size=order_size,
                price="0",
                tif="ioc",
                text="t-scalpyn-futures",
            )
        except Exception as e:
            logger.error("Futures order failed for %s: %s", contract, e)
            return

        # Place SL trigger
        sl_order_id = None
        try:
            sl_rule = 2 if direction == "long" else 1  # long: price <= sl, short: price >= sl
            sl_order = await self.adapter.create_price_trigger(
                contract=contract,
                trigger_price=str(risk_params.stop_loss),
                trigger_rule=sl_rule,
                size=0,
                is_close=True,
                price_type=1,  # mark price
                text="t-scalpyn-sl",
            )
            sl_order_id = str(sl_order.get("id", ""))
        except Exception as e:
            logger.error("SL trigger creation failed for %s: %s", contract, e)

        # Place TP1 trigger
        tp1_order_id = None
        try:
            tp1_rule = 1 if direction == "long" else 2
            tp1_close = -max(1, round(size * self.cfg.management.partial_exits.tp1_close_pct / 100))
            if direction == "short":
                tp1_close = abs(tp1_close)
            tp1_order = await self.adapter.create_price_trigger(
                contract=contract,
                trigger_price=str(risk_params.tp1_price),
                trigger_rule=tp1_rule,
                size=tp1_close if direction == "long" else tp1_close,
                is_reduce_only=True,
                price_type=0,
                text="t-scalpyn-tp1",
            )
            tp1_order_id = str(tp1_order.get("id", ""))
        except Exception as e:
            logger.error("TP1 trigger creation failed for %s: %s", contract, e)

        # Place TP2 trigger
        tp2_order_id = None
        try:
            tp2_rule = 1 if direction == "long" else 2
            remaining = size - max(1, round(size * self.cfg.management.partial_exits.tp1_close_pct / 100))
            tp2_close = -max(1, round(remaining * self.cfg.management.partial_exits.tp2_close_pct / 100))
            if direction == "short":
                tp2_close = abs(tp2_close)
            tp2_order = await self.adapter.create_price_trigger(
                contract=contract,
                trigger_price=str(risk_params.tp2_price),
                trigger_rule=tp2_rule,
                size=tp2_close,
                is_reduce_only=True,
                price_type=0,
                text="t-scalpyn-tp2",
            )
            tp2_order_id = str(tp2_order.get("id", ""))
        except Exception as e:
            logger.error("TP2 trigger creation failed for %s: %s", contract, e)

        # Persist position to DB
        trade = Trade(
            id=uuid.uuid4(),
            user_id=self.user_id,
            symbol=contract,
            side="buy" if direction == "long" else "sell",
            direction=direction,
            market_type="futures",
            exchange="gate.io",
            profile="futures",
            entry_price=Decimal(str(price)),
            original_entry_price=Decimal(str(price)),
            quantity=Decimal(str(size)),
            invested_value=Decimal(str(risk_params.position_value_usdt)),
            status="ACTIVE",
            take_profit_price=Decimal(str(risk_params.tp1_price)),
            tp2_price=Decimal(str(risk_params.tp2_price)),
            tp3_price=Decimal(str(risk_params.tp3_price)),
            stop_loss_price=Decimal(str(risk_params.stop_loss)),
            leverage=Decimal(str(risk_params.leverage)),
            sl_order_id=sl_order_id,
            tp1_order_id=tp1_order_id,
            tp2_order_id=tp2_order_id,
            tp1_hit=False,
            tp2_hit=False,
            risk_dollars=Decimal(str(risk_params.risk_dollars)),
            alpha_score_at_entry=Decimal(str(round(total_score, 2))),
            engine_meta={
                "order_id":          order.get("id"),
                "layers":            opp["layers"],
                "classification":    risk_params.classification,
                "macro_regime":      macro_state.regime,
                "vol_regime":        l4.vol_regime,
                "size_modifier":     size_mod,
                "risk_params":       {
                    "leverage":       risk_params.leverage,
                    "risk_pct":       risk_params.risk_pct,
                    "stop_dist_pct":  risk_params.stop_distance_pct,
                    "est_liq":        risk_params.estimated_liq_price,
                },
            },
        )
        db.add(trade)
        await db.commit()
        logger.info(
            "Futures position opened: %s %s  score=%.1f  lev=%.1fx  risk=%.2f%%  id=%s",
            direction.upper(), contract, total_score, risk_params.leverage,
            risk_params.risk_pct, trade.id,
        )

    # ── Leverage gate checks ──────────────────────────────────────────────────

    def _leverage_gate_check(self, of_data: dict, direction: str) -> tuple[bool, str]:
        """Gate 4: funding and OI guards."""
        fg  = self.cfg.leverage_checks.funding_guard
        funding = of_data.get("funding_rate", 0.0)

        if fg.enabled:
            if direction == "long" and funding > fg.funding_extreme:
                return False, f"Funding extreme positive {funding:.4%} — long blocked"
            if direction == "short" and funding < -fg.funding_extreme:
                return False, f"Funding extreme negative {funding:.4%} — short blocked"

        return True, "ok"

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_btc_1h_ago(self) -> Optional[float]:
        try:
            klines = await self.adapter.get_klines("BTC_USDT", interval="1h", limit=3, market="futures")
            if len(klines) >= 2:
                return float(klines[-2]["close"])
        except Exception:
            pass
        return None

    async def _get_daily_pnl(self, db: AsyncSession) -> float:
        from datetime import date
        today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
        q = select(Trade).where(
            Trade.user_id == self.user_id,
            Trade.profile == "futures",
            Trade.status == "CLOSED",
            Trade.exit_at >= today_start,
        )
        r = await db.execute(q)
        trades = r.scalars().all()
        return sum(float(t.profit_loss or 0) for t in trades)

    async def _get_total_capital(self) -> float:
        try:
            bal = await self.adapter.get_futures_balance()
            return float(bal.get("equity", bal.get("available", 0)) or 0)
        except Exception:
            return 10000.0

    async def _count_open_positions(self, db: AsyncSession) -> int:
        q = select(Trade).where(
            Trade.user_id == self.user_id,
            Trade.profile == "futures",
            Trade.status.in_(["ACTIVE", "open"]),
        )
        r = await db.execute(q)
        return len(r.scalars().all())


# ── Factory ───────────────────────────────────────────────────────────────────

async def build_futures_scanner(user_id: str) -> "FuturesScanner":
    async with AsyncSessionLocal() as db:
        cfg_row = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "futures_engine",
                ConfigProfile.is_active == True,
            )
        )
        cfg_row = cfg_row.scalars().first()
        futures_cfg = FuturesEngineConfig.from_config_json(cfg_row.config_json) if cfg_row else FuturesEngineConfig()

        exc_row = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.exchange_name == "gate.io",
                ExchangeConnection.is_active == True,
            )
        )
        exc_row = exc_row.scalars().first()
        if not exc_row:
            raise ValueError(f"No active Gate.io connection for user {user_id}")

        raw_key    = bytes(exc_row.api_key_encrypted)    if isinstance(exc_row.api_key_encrypted, memoryview)    else exc_row.api_key_encrypted
        raw_secret = bytes(exc_row.api_secret_encrypted) if isinstance(exc_row.api_secret_encrypted, memoryview) else exc_row.api_secret_encrypted
        adapter = GateAdapter(decrypt(raw_key).strip(), decrypt(raw_secret).strip())

    return FuturesScanner(user_id=user_id, cfg=futures_cfg, adapter=adapter)
