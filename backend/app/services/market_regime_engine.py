"""
Market Regime Engine — Unified regime detection for the Market Skills Engine.

Bridges the two previously disconnected regime systems:
  1. FuturesMacroGate (sophisticated macro analysis via Redis)
  2. Per-asset technical indicators (from indicators table)

Provides a single `get_effective_regime()` call that combines both sources
with confidence scoring and indicator evidence.

Author: Market Skills Engine v1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Market Regime Enum ────────────────────────────────────────────────────────

class MarketRegime(str, Enum):
    """
    Formalizes the 6 market regimes used for skill selection.
    Each regime maps to one or more optimal trading strategies (skills).
    """
    SIDEWAYS         = "SIDEWAYS"
    TRENDING_BULL    = "TRENDING_BULL"
    TRENDING_BEAR    = "TRENDING_BEAR"
    BREAKOUT         = "BREAKOUT"
    HIGH_VOLATILITY  = "HIGH_VOLATILITY"
    LOW_VOLATILITY   = "LOW_VOLATILITY"

    @classmethod
    def from_string(cls, s: str) -> "MarketRegime":
        """Converts legacy string regimes to enum, with fallback."""
        _MAP = {
            "BULL": cls.TRENDING_BULL,
            "BEAR": cls.TRENDING_BEAR,
            "SIDEWAYS": cls.SIDEWAYS,
            "HIGH_VOLATILITY": cls.HIGH_VOLATILITY,
            "TRENDING": cls.TRENDING_BULL,
            "TRENDING_BULL": cls.TRENDING_BULL,
            "TRENDING_BEAR": cls.TRENDING_BEAR,
            "BREAKOUT": cls.BREAKOUT,
            "LOW_VOLATILITY": cls.LOW_VOLATILITY,
        }
        return _MAP.get(s.upper().strip(), cls.SIDEWAYS)


# ── Regime Signal (result of detection) ───────────────────────────────────────

@dataclass
class RegimeSignal:
    """Result of regime detection with confidence and evidence."""
    regime: MarketRegime
    confidence: float = 1.0         # 0.0-1.0
    source: str = "hybrid"          # "macro" | "per_asset" | "hybrid"
    indicators: Dict[str, Any] = field(default_factory=dict)
    details: str = ""
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "indicators": self.indicators,
            "details": self.details,
            "detected_at": self.detected_at.isoformat(),
        }


# ── Thresholds (configurable via DB in future) ───────────────────────────────

_REGIME_THRESHOLDS = {
    # Trend detection
    "adx_trending": 25,          # ADX > 25 = trending market
    "adx_strong_trend": 35,      # ADX > 35 = strong trend
    "adx_no_trend": 20,          # ADX < 20 = no trend (sideways)

    # Volatility detection
    "atr_pct_high": 5.0,         # ATR% > 5 = high volatility
    "atr_pct_low": 1.0,          # ATR% < 1 = low volatility

    # Breakout detection
    "volume_spike_breakout": 2.0, # Volume > 2x average = potential breakout
    "atr_expansion_ratio": 1.5,   # ATR expanding > 1.5x = breakout confirmation

    # Macro weight vs per-asset weight
    "macro_weight": 0.4,          # 40% macro, 60% per-asset
    "per_asset_weight": 0.6,
}


# ── MarketRegimeEngine ───────────────────────────────────────────────────────

class MarketRegimeEngine:
    """
    Unified regime detection engine that bridges macro and per-asset analysis.

    Usage:
        engine = MarketRegimeEngine()
        regime = await engine.get_effective_regime(db, redis_client, symbol, indicators)
    """

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        self.thresholds = {**_REGIME_THRESHOLDS, **(thresholds or {})}

    # ── Global Regime (from FuturesMacroGate via Redis) ───────────────────

    async def detect_global_regime(
        self,
        db: AsyncSession,
        redis_client: Optional[Any] = None,
    ) -> RegimeSignal:
        """
        Reads the macro regime from Redis (set by macro_regime_update task).
        Falls back to DB-based detection if Redis is unavailable.
        """
        # Try Redis first (FuturesMacroGate output, updated every 30min)
        if redis_client:
            try:
                import json
                raw = await self._read_redis(redis_client, "macro:regime")
                if raw:
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    macro_regime = data.get("regime", "SIDEWAYS")
                    macro_score = float(data.get("score", 50))
                    components = data.get("components", {})

                    regime = MarketRegime.from_string(macro_regime)
                    confidence = min(1.0, macro_score / 100.0)

                    return RegimeSignal(
                        regime=regime,
                        confidence=confidence,
                        source="macro",
                        indicators={
                            "macro_regime": macro_regime,
                            "macro_score": macro_score,
                            "components": components,
                            "allows_long": data.get("allows_long", True),
                            "allows_short": data.get("allows_short", False),
                        },
                        details=f"Macro regime from FuturesMacroGate: {macro_regime} (score={macro_score:.0f})",
                    )
            except Exception as exc:
                logger.warning("[RegimeEngine] Redis macro read failed: %s", exc)

        # Fallback: use shadow_trades heuristic (legacy detect_regime behavior)
        return await self._detect_from_shadow_trades(db)

    # ── Per-Asset Regime (from technical indicators) ──────────────────────

    def detect_asset_regime(
        self,
        symbol: str,
        indicators: Dict[str, Any],
    ) -> RegimeSignal:
        """
        Detects regime for a specific asset based on its technical indicators.
        Uses ADX, EMA alignment, ATR%, volume spike, MACD.
        """
        t = self.thresholds
        evidence: Dict[str, Any] = {}
        scores: Dict[MarketRegime, float] = {r: 0.0 for r in MarketRegime}

        # ── Extract indicators (safe gets) ─────────────────────────────
        adx = _safe_float(indicators.get("adx"))
        rsi = _safe_float(indicators.get("rsi"))
        macd = _safe_float(indicators.get("macd_value") or indicators.get("macd"))
        atr_pct = _safe_float(indicators.get("atr_pct"))
        volume_spike = _safe_float(indicators.get("volume_spike") or indicators.get("rvol"))
        ema20 = _safe_float(indicators.get("ema20") or indicators.get("ema_20"))
        ema50 = _safe_float(indicators.get("ema50") or indicators.get("ema_50"))
        ema200 = _safe_float(indicators.get("ema200") or indicators.get("ema_200"))
        macd_histogram = _safe_float(indicators.get("macd_histogram") or indicators.get("histogram"))

        evidence["adx"] = adx
        evidence["rsi"] = rsi
        evidence["macd"] = macd
        evidence["atr_pct"] = atr_pct
        evidence["volume_spike"] = volume_spike

        # ── Trend Analysis ─────────────────────────────────────────────
        has_trend = adx is not None and adx > t["adx_trending"]
        strong_trend = adx is not None and adx > t["adx_strong_trend"]
        no_trend = adx is not None and adx < t["adx_no_trend"]

        # EMA Alignment
        ema_bullish = (
            ema20 is not None and ema50 is not None and ema200 is not None
            and ema20 > ema50 > ema200
        )
        ema_bearish = (
            ema20 is not None and ema50 is not None and ema200 is not None
            and ema20 < ema50 < ema200
        )

        evidence["ema_alignment"] = (
            "bullish" if ema_bullish else "bearish" if ema_bearish else "mixed"
        )

        # ── Score each regime ──────────────────────────────────────────

        # SIDEWAYS: no trend, EMAs converging
        if no_trend:
            scores[MarketRegime.SIDEWAYS] += 30
        if not ema_bullish and not ema_bearish:
            scores[MarketRegime.SIDEWAYS] += 15
        if atr_pct is not None and atr_pct < t["atr_pct_high"]:
            scores[MarketRegime.SIDEWAYS] += 10

        # TRENDING_BULL: strong trend + bullish alignment + MACD positive
        if has_trend and ema_bullish:
            scores[MarketRegime.TRENDING_BULL] += 30
        if strong_trend and ema_bullish:
            scores[MarketRegime.TRENDING_BULL] += 15
        if macd is not None and macd > 0:
            scores[MarketRegime.TRENDING_BULL] += 15
        if rsi is not None and 50 < rsi < 80:
            scores[MarketRegime.TRENDING_BULL] += 10

        # TRENDING_BEAR: strong trend + bearish alignment + MACD negative
        if has_trend and ema_bearish:
            scores[MarketRegime.TRENDING_BEAR] += 30
        if strong_trend and ema_bearish:
            scores[MarketRegime.TRENDING_BEAR] += 15
        if macd is not None and macd < 0:
            scores[MarketRegime.TRENDING_BEAR] += 15
        if rsi is not None and rsi < 40:
            scores[MarketRegime.TRENDING_BEAR] += 10

        # BREAKOUT: volume spike + ATR expansion + trend emerging
        if volume_spike is not None and volume_spike > t["volume_spike_breakout"]:
            scores[MarketRegime.BREAKOUT] += 30
        if atr_pct is not None and atr_pct > t["atr_pct_high"]:
            scores[MarketRegime.BREAKOUT] += 10
        if adx is not None and 20 < adx < 35:  # trend emerging, not established
            scores[MarketRegime.BREAKOUT] += 10
        if macd_histogram is not None and macd_histogram > 0:
            scores[MarketRegime.BREAKOUT] += 10

        # HIGH_VOLATILITY: ATR% very high
        if atr_pct is not None and atr_pct > t["atr_pct_high"]:
            scores[MarketRegime.HIGH_VOLATILITY] += 35
        if volume_spike is not None and volume_spike > 1.5:
            scores[MarketRegime.HIGH_VOLATILITY] += 10

        # LOW_VOLATILITY: ATR% very low
        if atr_pct is not None and atr_pct < t["atr_pct_low"]:
            scores[MarketRegime.LOW_VOLATILITY] += 35

        # ── Select winner ──────────────────────────────────────────────
        best_regime = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_regime]
        total_score = sum(scores.values()) or 1
        confidence = min(1.0, best_score / max(total_score * 0.5, 1))

        # If no strong signal, default to SIDEWAYS
        if best_score < 15:
            best_regime = MarketRegime.SIDEWAYS
            confidence = 0.3

        evidence["regime_scores"] = {r.value: round(s, 1) for r, s in scores.items()}

        return RegimeSignal(
            regime=best_regime,
            confidence=round(confidence, 3),
            source="per_asset",
            indicators=evidence,
            details=(
                f"Asset regime for {symbol}: {best_regime.value} "
                f"(score={best_score:.0f}, confidence={confidence:.2f})"
            ),
        )

    # ── Effective Regime (hybrid: macro + per-asset) ──────────────────────

    async def get_effective_regime(
        self,
        db: AsyncSession,
        symbol: str,
        indicators: Dict[str, Any],
        redis_client: Optional[Any] = None,
    ) -> RegimeSignal:
        """
        Combines global macro regime with per-asset regime for a hybrid signal.

        The macro regime provides the "global backdrop" (is the market overall
        bullish/bearish/volatile?) while per-asset indicators detect the specific
        regime of the individual asset.

        Weighting: 40% macro + 60% per-asset (configurable via thresholds).
        """
        macro_signal = await self.detect_global_regime(db, redis_client)
        asset_signal = self.detect_asset_regime(symbol, indicators)

        # If both agree → high confidence
        if macro_signal.regime == asset_signal.regime:
            return RegimeSignal(
                regime=macro_signal.regime,
                confidence=min(1.0, (macro_signal.confidence + asset_signal.confidence) / 2 + 0.15),
                source="hybrid",
                indicators={
                    "macro": macro_signal.indicators,
                    "per_asset": asset_signal.indicators,
                    "agreement": True,
                },
                details=(
                    f"Hybrid regime: {macro_signal.regime.value} "
                    f"(macro + asset agree, high confidence)"
                ),
            )

        # If they disagree → weighted decision
        macro_w = self.thresholds["macro_weight"]
        asset_w = self.thresholds["per_asset_weight"]

        macro_score = macro_signal.confidence * macro_w
        asset_score = asset_signal.confidence * asset_w

        if asset_score >= macro_score:
            winner = asset_signal
            winner_source = "per_asset"
        else:
            winner = macro_signal
            winner_source = "macro"

        combined_confidence = max(
            winner.confidence * 0.7,  # reduce confidence on disagreement
            0.3,
        )

        return RegimeSignal(
            regime=winner.regime,
            confidence=round(combined_confidence, 3),
            source="hybrid",
            indicators={
                "macro": macro_signal.indicators,
                "per_asset": asset_signal.indicators,
                "agreement": False,
                "macro_regime": macro_signal.regime.value,
                "asset_regime": asset_signal.regime.value,
                "winner_source": winner_source,
            },
            details=(
                f"Hybrid regime: {winner.regime.value} from {winner_source} "
                f"(macro={macro_signal.regime.value}, asset={asset_signal.regime.value}, "
                f"confidence={combined_confidence:.2f})"
            ),
        )

    # ── Persist regime to history ─────────────────────────────────────────

    async def log_regime(
        self,
        signal: RegimeSignal,
        db: AsyncSession,
    ) -> None:
        """Persists regime signal to regime_history table for analysis."""
        try:
            import json
            await db.execute(text("""
                INSERT INTO regime_history (regime, confidence, source, indicators_snapshot, detected_at)
                VALUES (:regime, :confidence, :source, :snapshot, :detected_at)
            """), {
                "regime": signal.regime.value,
                "confidence": signal.confidence,
                "source": signal.source,
                "snapshot": json.dumps(signal.indicators, default=str),
                "detected_at": signal.detected_at,
            })
        except Exception as exc:
            logger.warning("[RegimeEngine] Failed to log regime: %s", exc)

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _detect_from_shadow_trades(self, db: AsyncSession) -> RegimeSignal:
        """
        Legacy fallback: detects regime from shadow_trades performance.
        Mirrors the original autopilot_engine.detect_regime() behavior.
        """
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        try:
            result = await db.execute(text("""
                SELECT
                    COUNT(*) AS n,
                    AVG(pnl_pct) AS ev,
                    AVG(CASE WHEN outcome = 'TP_HIT' THEN 1.0 ELSE 0.0 END) AS wr
                FROM shadow_trades
                WHERE outcome IN ('TP_HIT', 'SL_HIT')
                  AND pnl_pct IS NOT NULL
                  AND created_at >= :cutoff
            """), {"cutoff": cutoff})
            row = dict(result.mappings().one())
            n = int(row["n"] or 0)
            ev = float(row["ev"] or 0)
            wr = float(row["wr"] or 0)

            if n < 5:
                return RegimeSignal(
                    regime=MarketRegime.SIDEWAYS,
                    confidence=0.3,
                    source="shadow_trades",
                    indicators={"n": n, "ev": ev, "wr": wr},
                    details=f"Insufficient shadow trades (n={n}), defaulting to SIDEWAYS",
                )

            if ev > 1.5 or ev < -2.0:
                regime = MarketRegime.HIGH_VOLATILITY
            elif ev > 0 and wr > 0.55:
                regime = MarketRegime.TRENDING_BULL
            elif ev < -0.5 or wr < 0.35:
                regime = MarketRegime.TRENDING_BEAR
            else:
                regime = MarketRegime.SIDEWAYS

            return RegimeSignal(
                regime=regime,
                confidence=0.5,
                source="shadow_trades",
                indicators={"n": n, "ev": round(ev, 4), "wr": round(wr, 4)},
                details=f"Shadow trades regime: {regime.value} (n={n}, ev={ev:.3f}%, wr={wr:.1%})",
            )
        except Exception as exc:
            logger.error("[RegimeEngine] Shadow trades fallback failed: %s", exc)
            return RegimeSignal(
                regime=MarketRegime.SIDEWAYS,
                confidence=0.2,
                source="fallback",
                details=f"Fallback to SIDEWAYS due to error: {exc}",
            )

    @staticmethod
    async def _read_redis(redis_client: Any, key: str) -> Optional[str]:
        """Read from Redis, supporting both sync and async clients."""
        try:
            if hasattr(redis_client, "get"):
                result = redis_client.get(key)
                # If it's a coroutine (async redis), await it
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, bytes):
                    return result.decode("utf-8")
                return result
        except Exception:
            return None
        return None


# ── Helper ────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    """Safely convert any value to float, returning None on failure."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None
