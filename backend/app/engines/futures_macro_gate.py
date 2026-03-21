"""Futures Macro Gate — evaluates macro regime before any futures trade.

Macro is a MANDATORY gate for futures (not optional like spot).
Alavancagem amplifica erros macro.

Components (all weighted from config):
  BTC Trend (1D)        30%  — EMA21/50/200 + structure
  DXY Direction         20%  — DXY above/below EMA21 (proxy via BTC inverse)
  Funding Rate Market   15%  — avg funding top coins
  Liquidation Pressure  15%  — 24h liq vs 7d avg
  Stablecoin Flow       10%  — USDT market cap direction (proxy)
  VIX / Risk Appetite   10%  — external signal, default 50 if unavailable

Regime:
  score > 75  → STRONG_RISK_ON
  55-75       → RISK_ON
  40-55       → NEUTRAL
  25-40       → RISK_OFF
  < 25        → STRONG_RISK_OFF
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd

from ..schemas.futures_engine_config import MacroRegimeConfig
from ..exchange_adapters.gate_adapter import GateAdapter

logger = logging.getLogger(__name__)

MacroRegime = Literal["STRONG_RISK_ON", "RISK_ON", "NEUTRAL", "RISK_OFF", "STRONG_RISK_OFF"]


@dataclass
class MacroState:
    regime: MacroRegime
    score: float
    component_scores: Dict[str, float]
    size_modifier: float      # multiply base risk_pct by this
    allows_long: bool
    allows_short: bool
    timestamp: float          # unix timestamp of evaluation
    details: Dict[str, Any]


_BTCUSDT = "BTC_USDT"
_TOP_CONTRACTS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT"]


class FuturesMacroGate:
    """
    Evaluates macro regime from on-chain and market data.
    Caches result for `update_interval_minutes` to avoid hammering the API.
    """

    def __init__(self, cfg: MacroRegimeConfig, adapter: GateAdapter):
        self.cfg     = cfg
        self.adapter = adapter
        self._cache: Optional[MacroState] = None
        self._cache_ts: float = 0.0

    async def get_regime(self, force_refresh: bool = False) -> MacroState:
        """Return cached macro state, or refresh if stale."""
        cache_ttl = self.cfg.update_interval_minutes * 60
        if not force_refresh and self._cache and (time.time() - self._cache_ts) < cache_ttl:
            return self._cache

        state = await self._evaluate()
        self._cache    = state
        self._cache_ts = time.time()
        logger.info("Macro regime updated: %s (score=%.1f)", state.regime, state.score)
        return state

    async def _evaluate(self) -> MacroState:
        w = self.cfg.weights

        # Run component evaluations concurrently
        (btc_score, btc_details), \
        (dxy_score, dxy_details), \
        (funding_score, funding_details), \
        (liq_score, liq_details) = await asyncio.gather(
            self._score_btc_trend(),
            self._score_dxy_proxy(),
            self._score_funding_market(),
            self._score_liquidation_pressure(),
        )

        # Stablecoin flow and VIX are optional/external — default 50 (neutral)
        stable_score = 50.0
        vix_score    = 50.0

        # Weighted average (each component scored 0-100)
        total_weight = (
            w.btc_trend + w.dxy_direction + w.funding_rate_market +
            w.liquidation_pressure + w.stablecoin_flow + w.vix_risk_appetite
        ) or 100

        macro_score = (
            btc_score       * w.btc_trend +
            dxy_score       * w.dxy_direction +
            funding_score   * w.funding_rate_market +
            liq_score       * w.liquidation_pressure +
            stable_score    * w.stablecoin_flow +
            vix_score       * w.vix_risk_appetite
        ) / total_weight

        macro_score = round(macro_score, 1)
        regime      = self._classify(macro_score)
        allows_long, allows_short, size_modifier = self._regime_rules(regime, macro_score)

        return MacroState(
            regime=regime,
            score=macro_score,
            component_scores={
                "btc_trend":           round(btc_score, 1),
                "dxy_direction":       round(dxy_score, 1),
                "funding_rate_market": round(funding_score, 1),
                "liquidation_pressure": round(liq_score, 1),
                "stablecoin_flow":     stable_score,
                "vix_risk_appetite":   vix_score,
            },
            size_modifier=size_modifier,
            allows_long=allows_long,
            allows_short=allows_short,
            timestamp=time.time(),
            details={
                "btc":     btc_details,
                "dxy":     dxy_details,
                "funding": funding_details,
                "liq":     liq_details,
            },
        )

    # ── Component evaluators ──────────────────────────────────────────────────

    async def _score_btc_trend(self) -> tuple[float, dict]:
        """BTC EMA21/50/200 trend + structure. Returns 0-100 score."""
        try:
            klines = await self.adapter.get_klines(_BTCUSDT, interval=self.cfg.btc_timeframe, limit=210)
            df     = pd.DataFrame(klines)
            closes = df["close"].astype(float)

            ema21  = closes.ewm(span=21,  adjust=False).mean()
            ema50  = closes.ewm(span=50,  adjust=False).mean()
            ema200 = closes.ewm(span=200, adjust=False).mean()

            last   = float(closes.iloc[-1])
            e21    = float(ema21.iloc[-1])
            e50    = float(ema50.iloc[-1])
            e200   = float(ema200.iloc[-1])

            score = 50.0  # neutral baseline
            if last > e21:  score += 15
            if e21  > e50:  score += 15
            if e50  > e200: score += 20

            # Momentum: slope of EMA21 over 5 days
            slope = float(ema21.iloc[-1] - ema21.iloc[-5]) / e21 * 100 if len(ema21) >= 5 else 0
            if slope > 0.5:   score += 10
            elif slope < -0.5: score -= 10

            score = max(0.0, min(100.0, score))
            return score, {"last": last, "ema21": e21, "ema50": e50, "ema200": e200, "slope_pct": round(slope, 3)}
        except Exception as e:
            logger.debug("BTC trend eval failed: %s", e)
            return 50.0, {"error": str(e)}

    async def _score_dxy_proxy(self) -> tuple[float, dict]:
        """
        DXY proxy: when BTC is trending strongly, DXY is typically weak (inverse).
        We use ETH/BTC ratio as a risk-on proxy instead of real DXY data.
        Returns 0-100 score (high = risk-on = weak DXY).
        """
        try:
            klines_eth = await self.adapter.get_klines("ETH_USDT", interval="1d", limit=30)
            klines_btc = await self.adapter.get_klines(_BTCUSDT,   interval="1d", limit=30)

            eth_closes = pd.Series([k["close"] for k in klines_eth], dtype=float)
            btc_closes = pd.Series([k["close"] for k in klines_btc], dtype=float)

            if len(eth_closes) < 22 or len(btc_closes) < 22:
                return 50.0, {"note": "insufficient data"}

            eth_btc = eth_closes / btc_closes
            ema21   = eth_btc.ewm(span=21, adjust=False).mean()
            last    = float(eth_btc.iloc[-1])
            e21     = float(ema21.iloc[-1])

            # ETH/BTC above EMA21 = altcoins outperforming = risk-on
            score = 65.0 if last > e21 else 35.0
            return score, {"eth_btc_ratio": round(last, 6), "ema21": round(e21, 6)}
        except Exception as e:
            logger.debug("DXY proxy eval failed: %s", e)
            return 50.0, {"error": str(e)}

    async def _score_funding_market(self) -> tuple[float, dict]:
        """Average funding rate across top contracts. Returns 0-100."""
        try:
            rates = []
            for contract in _TOP_CONTRACTS:
                try:
                    info = await self.adapter.get_contract_info(contract)
                    rate = float(info.get("funding_rate", 0) or 0)
                    rates.append(rate)
                except Exception:
                    pass

            if not rates:
                return 50.0, {"note": "no data"}

            avg_funding = sum(rates) / len(rates)
            ext_pos = self.cfg.funding_extreme_positive
            ext_neg = self.cfg.funding_extreme_negative

            # High positive funding = longs crowded = RISK_ON but overextended
            # Negative funding = shorts crowded = potential risk-off or bottom
            if avg_funding > ext_pos:
                score = 30.0   # crowded longs — overextended risk-on
            elif avg_funding > 0.02:
                score = 60.0   # moderate positive — healthy risk-on
            elif avg_funding >= 0:
                score = 70.0   # low positive — balanced
            elif avg_funding > ext_neg:
                score = 55.0   # slightly negative — slight risk-off
            else:
                score = 25.0   # very negative — risk-off / capitulation

            return score, {"avg_funding_rate": round(avg_funding, 6), "sample_size": len(rates)}
        except Exception as e:
            logger.debug("Funding market eval failed: %s", e)
            return 50.0, {"error": str(e)}

    async def _score_liquidation_pressure(self) -> tuple[float, dict]:
        """Recent liquidation intensity vs baseline. Returns 0-100."""
        try:
            # Fetch BTC liq orders as proxy for market-wide liquidation pressure
            liq_data = await self.adapter._request(
                "GET", f"/futures/{self.adapter.SETTLE}/liq_orders",
                params={"contract": _BTCUSDT, "limit": "100"},
                base_url=self.adapter.FUTURES_BASE,
            )

            liq_longs  = sum(1 for l in liq_data if float(l.get("size", 0) or 0) < 0)
            liq_shorts = sum(1 for l in liq_data if float(l.get("size", 0) or 0) > 0)
            total_liq  = liq_longs + liq_shorts

            if total_liq == 0:
                return 60.0, {"liq_longs": 0, "liq_shorts": 0}

            long_liq_ratio = liq_longs / total_liq

            # Many long liquidations = risk-off / deleveraging
            # Many short liquidations = longs squeezing shorts = risk-on
            if long_liq_ratio > 0.7:
                score = 25.0   # heavy long liquidation = panic
            elif long_liq_ratio > 0.6:
                score = 40.0   # moderate long liq
            elif long_liq_ratio >= 0.4:
                score = 60.0   # balanced
            elif long_liq_ratio >= 0.3:
                score = 70.0   # more shorts getting squeezed = bullish
            else:
                score = 80.0   # heavy short squeeze = risk-on

            return score, {"liq_longs": liq_longs, "liq_shorts": liq_shorts, "long_liq_ratio": round(long_liq_ratio, 3)}
        except Exception as e:
            logger.debug("Liquidation pressure eval failed: %s", e)
            return 50.0, {"error": str(e)}

    # ── Classification ────────────────────────────────────────────────────────

    def _classify(self, score: float) -> MacroRegime:
        t = self.cfg.thresholds
        if score > t.strong_risk_on:
            return "STRONG_RISK_ON"
        elif score > t.risk_on:
            return "RISK_ON"
        elif score > t.neutral:
            return "NEUTRAL"
        elif score > t.risk_off:
            return "RISK_OFF"
        else:
            return "STRONG_RISK_OFF"

    def _regime_rules(
        self, regime: MacroRegime, score: float
    ) -> tuple[bool, bool, float]:
        """Returns (allows_long, allows_short, size_modifier)."""
        cfg = self.cfg

        if regime == "STRONG_RISK_ON":
            return True, False, 1.0
        elif regime == "RISK_ON":
            short_size = 1 - cfg.risk_on_short_size_reduction
            return True, cfg.risk_on_allow_short, (1.0 if True else short_size)
        elif regime == "NEUTRAL":
            return True, True, 1 - cfg.neutral_size_reduction
        elif regime == "RISK_OFF":
            # Long allowed only with very high score
            allow_long = False   # gate.py will check score override
            return allow_long, True, 0.5
        else:  # STRONG_RISK_OFF
            return False, True, 0.3

    def get_size_modifier(self, regime: MacroRegime, trade_direction: str) -> float:
        """Return the size multiplier for a given regime and direction."""
        _, _, base_modifier = self._regime_rules(regime, 0)
        if regime == "RISK_ON" and trade_direction == "short":
            return 1 - self.cfg.risk_on_short_size_reduction
        return base_modifier

    def can_trade(self, state: MacroState, direction: str, score: float) -> tuple[bool, str]:
        """Gate check: can we open a trade given current macro?"""
        if direction == "long":
            if not state.allows_long:
                # Exception: RISK_OFF with very high score
                if state.regime == "RISK_OFF" and score >= self.cfg.risk_off_allow_long_min_score:
                    return True, f"RISK_OFF long exception (score={score} >= {self.cfg.risk_off_allow_long_min_score})"
                return False, f"Macro gate BLOCKED long: regime={state.regime}"
        else:  # short
            if not state.allows_short:
                return False, f"Macro gate BLOCKED short: regime={state.regime}"
        return True, f"Macro gate PASS: {state.regime} (score={state.score:.1f})"
