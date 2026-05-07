"""Spot Scanner — main async loop for the Spot Engine.

Cycle:
  1. Fetch tickers for universe
  2. Fetch OHLCV + calculate indicators (FeatureEngine)
  3. Score each symbol (ScoreEngine)
  4. Rank by score, apply filters, buy top N
  5. Monitor active positions → sell layer evaluation
  6. Monitor underwater positions → DCA
  7. Sleep scan_interval, repeat

All thresholds from SpotEngineConfig. Zero hardcode.
Engine state (running/paused) is managed in-process via asyncio.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..models.trade import Trade
from ..models.exchange_connection import ExchangeConnection
from ..models.config_profile import ConfigProfile
from ..exchange_adapters.gate_adapter import GateAdapter, InsufficientBalanceError
from ..services.feature_engine import FeatureEngine
from ..services.score_engine import ScoreEngine
from ..utils.encryption import decrypt
from ..utils.exchange_names import exchange_name_matches
from ..schemas.spot_engine_config import SpotEngineConfig
from .spot_capital_manager import SpotCapitalManager
from .spot_position_manager import SpotPositionManager
from .spot_sell_manager import SpotSellManager

logger = logging.getLogger(__name__)

# ── Engine state (singleton per user_id) ─────────────────────────────────────

_running_engines: Dict[str, "SpotScanner"] = {}


def get_engine(user_id: str) -> Optional["SpotScanner"]:
    return _running_engines.get(user_id)


def register_engine(user_id: str, engine: "SpotScanner") -> None:
    _running_engines[user_id] = engine


def unregister_engine(user_id: str) -> None:
    _running_engines.pop(user_id, None)


# ── Scanner ───────────────────────────────────────────────────────────────────

class SpotScanner:
    """
    Async Spot Engine scanner.
    Instantiated per user; one asyncio Task per running engine.
    """

    def __init__(
        self,
        user_id: str,
        config: SpotEngineConfig,
        adapter: GateAdapter,
        feature_config: dict,
        score_config: dict,
    ):
        self.user_id  = user_id
        self.cfg      = config
        self.adapter  = adapter

        self._feature_engine = FeatureEngine(feature_config)
        self._score_engine   = ScoreEngine(score_config)
        self._capital_mgr    = SpotCapitalManager(config.buying)
        self._position_mgr   = SpotPositionManager(config)
        self._sell_mgr       = SpotSellManager(config)

        self._task: Optional[asyncio.Task] = None
        self._running  = False
        self._paused   = False
        self._cycle    = 0
        self._last_buy: Dict[str, float] = {}   # symbol → timestamp
        self._started_at: Optional[datetime] = None
        self._last_error: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            logger.warning("SpotScanner already running for user %s", self.user_id)
            return
        self._running    = True
        self._paused     = False
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._loop(), name=f"spot-scanner-{self.user_id}")
        register_engine(self.user_id, self)
        logger.info("SpotScanner started for user %s", self.user_id)

    def pause(self) -> None:
        self._paused = True
        logger.info("SpotScanner paused for user %s", self.user_id)

    def resume(self) -> None:
        self._paused = False
        logger.info("SpotScanner resumed for user %s", self.user_id)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        unregister_engine(self.user_id)
        logger.info("SpotScanner stopped for user %s", self.user_id)

    def status(self) -> dict:
        return {
            "running":     self._running,
            "paused":      self._paused,
            "cycle":       self._cycle,
            "started_at":  self._started_at.isoformat() if self._started_at else None,
            "last_error":  self._last_error,
            "user_id":     self.user_id,
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                if self._paused:
                    await asyncio.sleep(5)
                    continue

                self._cycle += 1
                logger.debug("SpotScanner cycle %d — user %s", self._cycle, self.user_id)

                async with AsyncSessionLocal() as db:
                    await self._run_cycle(db)

                self._last_error = None

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                logger.exception("SpotScanner cycle error (user %s): %s", self.user_id, e)

            await asyncio.sleep(self.cfg.scanner.scan_interval_seconds)

    async def _run_cycle(self, db: AsyncSession) -> None:
        # ── 1. Fetch tickers ─────────────────────────────────────────────────
        tickers = await self.adapter.get_tickers(market="spot")
        prices  = {t["currency_pair"]: float(t.get("last", 0)) for t in tickers if t.get("last")}

        if not prices:
            logger.warning("No ticker data received — skipping cycle")
            return

        # ── 2. Update position statuses ──────────────────────────────────────
        transitions = await self._position_mgr.update_position_statuses(
            db, self.user_id, prices
        )
        for trade, old_s, new_s in transitions:
            logger.info("Position status change: %s %s → %s", trade.symbol, old_s, new_s)

        # ── 3. Evaluate sell layers for ACTIVE positions ──────────────────────
        await self._process_sells(db, prices)

        # ── 4. Macro filter check ─────────────────────────────────────────────
        if self.cfg.macro_filter.enabled and self.cfg.macro_filter.block_in_risk_off:
            if await self._is_risk_off(prices):
                logger.info("Macro filter: risk-off detected — skipping buys")
                return

        # ── 5. Score universe ─────────────────────────────────────────────────
        scored = await self._score_universe(tickers)
        if not scored:
            return

        # ── 6. Get capital state ──────────────────────────────────────────────
        balance_data = await self.adapter.get_spot_balance()
        usdt_balance = next(
            (float(a["available"]) for a in balance_data if a.get("currency") == "USDT"), 0.0
        )
        capital_state = await self._capital_mgr.get_state(usdt_balance, db, self.user_id)

        # ── 7. Process DCA on underwater positions ────────────────────────────
        scores_map = {s["symbol"]: s["score"] for s in scored}
        await self._position_mgr.process_dca(
            db, self.user_id, prices, scores_map,
            capital_state.available, self.adapter,
        )

        # ── 8. Buy top-N opportunities ────────────────────────────────────────
        opportunities = [
            s for s in scored
            if s["score"] >= self.cfg.scanner.buy_threshold_score
        ][: self.cfg.scanner.max_opportunities_per_scan]

        for opp in opportunities:
            allowed, reason = self._capital_mgr.can_open_new_position(capital_state)
            if not allowed:
                logger.info("Buy blocked (global): %s", reason)
                break

            await self._try_buy(opp, capital_state, prices, db)

    # ── Sell processing ───────────────────────────────────────────────────────

    async def _process_sells(self, db: AsyncSession, prices: dict) -> None:
        from sqlalchemy import select
        q = select(Trade).where(
            Trade.user_id == self.user_id,
            Trade.market_type == "spot",
            Trade.status == "ACTIVE",
        )
        result  = await db.execute(q)
        actives = result.scalars().all()

        for pos in actives:
            current_price = prices.get(pos.symbol)
            if not current_price:
                continue

            # Get fresh indicators for this symbol
            indicators = await self._get_indicators(pos.symbol, market="spot")
            score_result = self._score_engine.compute_score(indicators)
            current_score = score_result["total_score"]

            decision = self._sell_mgr.evaluate(pos, current_price, indicators, current_score)

            if decision.should_sell:
                try:
                    await self._sell_mgr.execute_sell(pos, self.adapter, decision, db)
                    logger.info(
                        "SOLD %s via layer %s  profit=%.2f%%",
                        pos.symbol, decision.layer, decision.profit_pct,
                    )
                except Exception as e:
                    logger.exception("Sell execution failed for %s: %s", pos.symbol, e)

    # ── Score universe ────────────────────────────────────────────────────────

    async def _score_universe(self, tickers: List[dict]) -> List[dict]:
        results = []
        for ticker in tickers:
            symbol = ticker.get("currency_pair", "")
            if not symbol.endswith("_USDT"):
                continue
            try:
                indicators = await self._get_indicators(symbol, market="spot")
                if not indicators:
                    continue
                score_result  = self._score_engine.compute_score(indicators)
                results.append({
                    "symbol":     symbol,
                    "score":      score_result["total_score"],
                    "indicators": indicators,
                    "score_meta": score_result,
                })
            except Exception as e:
                logger.debug("Score failed for %s: %s", symbol, e)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    async def _get_indicators(self, symbol: str, market: str = "spot") -> dict:
        klines = await self.adapter.get_klines(symbol, interval="1h", limit=200, market=market)
        if len(klines) < 20:
            return {}
        df = pd.DataFrame(klines).set_index("time")
        return self._feature_engine.calculate(df)

    # ── Buy execution ─────────────────────────────────────────────────────────

    async def _try_buy(
        self,
        opp: dict,
        capital_state,
        prices: dict,
        db: AsyncSession,
    ) -> None:
        symbol = opp["symbol"]
        score  = opp["score"]

        # Symbol cooldown check
        if self._is_in_cooldown(symbol):
            logger.debug("Symbol %s in cooldown — skipping", symbol)
            return

        trade_size = self._capital_mgr.calc_trade_size(capital_state)
        if trade_size <= 0:
            return

        # Per-asset check
        ok, reason = await self._capital_mgr.can_trade_asset(
            symbol, trade_size, capital_state, db, self.user_id
        )
        if not ok:
            logger.info("Buy blocked (asset %s): %s", symbol, reason)
            return

        current_price = prices.get(symbol, 0)
        if not current_price:
            return

        logger.info(
            "BUY %s  score=%.1f  size=%.2f USDT  price=%.6f",
            symbol, score, trade_size, current_price,
        )

        try:
            order_type = self.cfg.buying.order_type
            order = await self.adapter.place_spot_order(
                currency_pair=symbol,
                side="buy",
                order_type=order_type,
                amount=str(trade_size),
                text=f"t-scalpyn-spot",
            )
        except InsufficientBalanceError as e:
            logger.warning("Buy rejected (insufficient balance): %s", e)
            return
        except Exception as e:
            logger.exception("Buy order failed for %s: %s", symbol, e)
            return

        # Record position in DB
        fill_price = float(
            order.get("avg_deal_price") or order.get("price") or current_price
        )
        qty = trade_size / fill_price if fill_price > 0 else 0

        trade = Trade(
            id=uuid.uuid4(),
            user_id=self.user_id,
            symbol=symbol,
            side="buy",
            direction="long",
            market_type="spot",
            exchange="gate.io",
            entry_price=Decimal(str(fill_price)),
            original_entry_price=Decimal(str(fill_price)),
            quantity=Decimal(str(round(qty, 8))),
            invested_value=Decimal(str(trade_size)),
            status="ACTIVE",
            profile="spot",
            dca_layers=0,
            alpha_score_at_entry=Decimal(str(round(score, 2))),
            indicators_at_entry=opp["indicators"],
            engine_meta={
                "order_id":        order.get("id"),
                "score_at_entry":  score,
                "score_meta":      opp["score_meta"],
                "buy_layer":       "scanner",
            },
        )
        db.add(trade)
        await db.commit()

        # Mark cooldown
        self._last_buy[symbol] = datetime.now(timezone.utc).timestamp()

        logger.info(
            "Position opened: %s  qty=%.6f @ %.6f  id=%s",
            symbol, qty, fill_price, trade.id,
        )

    # ── Macro filter ──────────────────────────────────────────────────────────

    async def _is_risk_off(self, prices: dict) -> bool:
        """
        Simplified macro check: BTC below 200 EMA is a risk-off proxy.
        Full macro gate is in futures_macro_gate.py (FASE 3).
        """
        btc_price = prices.get("BTC_USDT")
        if not btc_price:
            return False
        try:
            klines = await self.adapter.get_klines("BTC_USDT", interval="1d", limit=210)
            df     = pd.DataFrame(klines)
            ema200 = df["close"].ewm(span=200, adjust=False).mean().iloc[-1]
            return btc_price < float(ema200)
        except Exception:
            return False

    # ── Cooldown ──────────────────────────────────────────────────────────────

    def _is_in_cooldown(self, symbol: str) -> bool:
        cooldown = self.cfg.scanner.symbol_cooldown_seconds
        if cooldown <= 0:
            return False
        last = self._last_buy.get(symbol)
        if last is None:
            return False
        elapsed = datetime.now(timezone.utc).timestamp() - last
        return elapsed < cooldown


# ── Factory: build scanner from DB ───────────────────────────────────────────

async def build_scanner_from_db(user_id: str) -> "SpotScanner":
    """
    Load config and credentials from DB and return a ready SpotScanner.
    Raises ValueError if required config or exchange connection is missing.
    """
    async with AsyncSessionLocal() as db:
        # Load SpotEngineConfig
        cfg_row = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "spot_engine",
                ConfigProfile.is_active == True,
            )
        )
        cfg_row = cfg_row.scalars().first()
        if not cfg_row:
            logger.warning("[spot-engine] build_scanner_from_db: missing spot_engine config for user=%s", user_id)
            raise ValueError(f"No active spot_engine config found for user {user_id}")
        spot_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)

        # Load FeatureEngine config
        feat_row = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "feature_engine",
                ConfigProfile.is_active == True,
            )
        )
        feat_row = feat_row.scalars().first()
        feature_cfg = feat_row.config_json if feat_row else _default_feature_config()

        # Load ScoreEngine config
        score_row = await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "score_engine",
                ConfigProfile.is_active == True,
            )
        )
        score_row   = score_row.scalars().first()
        score_cfg   = score_row.config_json if score_row else {}

        # Load Gate.io credentials
        exc_row = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                exchange_name_matches(ExchangeConnection.exchange_name, "gate.io"),
                ExchangeConnection.is_active == True,
            )
        )
        exc_row = exc_row.scalars().first()
        if not exc_row:
            logger.warning("[spot-engine] build_scanner_from_db: missing Gate.io connection for user=%s", user_id)
            raise ValueError(f"No active Gate.io connection found for user {user_id}")

        raw_key    = bytes(exc_row.api_key_encrypted)    if isinstance(exc_row.api_key_encrypted, memoryview) else exc_row.api_key_encrypted
        raw_secret = bytes(exc_row.api_secret_encrypted) if isinstance(exc_row.api_secret_encrypted, memoryview) else exc_row.api_secret_encrypted
        api_key    = decrypt(raw_key).strip()
        api_secret = decrypt(raw_secret).strip()

    adapter = GateAdapter(api_key, api_secret)
    return SpotScanner(
        user_id=user_id,
        config=spot_cfg,
        adapter=adapter,
        feature_config=feature_cfg,
        score_config=score_cfg,
    )


def _default_feature_config() -> dict:
    """Minimal feature config so the scanner can function without explicit config in DB."""
    return {
        "rsi":          {"enabled": True, "period": 14},
        "adx":          {"enabled": True, "period": 14},
        "ema":          {"enabled": True, "periods": [5, 9, 21, 50, 200]},
        "atr":          {"enabled": True, "period": 14},
        "macd":         {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
        "vwap":         {"enabled": True},
        "stochastic":   {"enabled": True, "k": 14, "d": 3, "smooth": 3},
        "obv":          {"enabled": True},
        "bollinger":    {"enabled": True, "period": 20, "deviation": 2.0},
        "parabolic_sar": {"enabled": True, "step": 0.02, "max_step": 0.2},
        "zscore":       {"enabled": True, "lookback": 20},
        "volume_delta": {"enabled": True},
    }
