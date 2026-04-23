"""Feature Engine — calculates technical indicators dynamically from config."""

import logging
from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class FeatureEngine:
    """Calculates technical indicators dynamically based on user configuration."""

    def __init__(self, indicators_config: Dict[str, Any]):
        self.config = indicators_config

    def calculate(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate all enabled indicators for the given OHLCV DataFrame.

        Args:
            df: DataFrame with columns [open, high, low, close, volume] indexed by time.

        Returns:
            Dictionary of indicator_name -> value (latest value).
        """
        if df is None or df.empty or len(df) < 2:
            logger.warning("Insufficient data for indicator calculation")
            return {}

        results: Dict[str, Any] = {}

        try:
            if self.config.get("rsi", {}).get("enabled"):
                results.update(self._calc_rsi(df))

            if self.config.get("adx", {}).get("enabled"):
                results.update(self._calc_adx(df))

            if self.config.get("ema", {}).get("enabled"):
                results.update(self._calc_ema(df))

            if self.config.get("atr", {}).get("enabled"):
                results.update(self._calc_atr(df))

            if self.config.get("macd", {}).get("enabled"):
                results.update(self._calc_macd(df))

            if self.config.get("vwap", {}).get("enabled"):
                results.update(self._calc_vwap(df))

            if self.config.get("stochastic", {}).get("enabled"):
                results.update(self._calc_stochastic(df))

            if self.config.get("obv", {}).get("enabled"):
                results.update(self._calc_obv(df))

            if self.config.get("bollinger", {}).get("enabled"):
                results.update(self._calc_bollinger(df))

            if self.config.get("parabolic_sar", {}).get("enabled"):
                results.update(self._calc_parabolic_sar(df))

            if self.config.get("zscore", {}).get("enabled"):
                results.update(self._calc_zscore(df))

            if self.config.get("volume_delta", {}).get("enabled"):
                results.update(self._calc_volume_delta(df))

            # Derived: volume spike (always useful)
            results.update(self._calc_volume_spike(df))

            # Derived: taker buy ratio (proxy from candle direction)
            results.update(self._calc_taker_ratio(df))

            # Derived: EMA trend alignment
            if "ema9" in results and "ema21" in results:
                results["ema9_gt_ema21"] = results["ema9"] > results["ema21"]
            if "ema9" in results and "ema50" in results:
                results["ema9_gt_ema50"] = results["ema9"] > results["ema50"]
            if "ema50" in results and "ema200" in results:
                results["ema50_gt_ema200"] = results["ema50"] > results["ema200"]
            if "ema9" in results and "ema50" in results and "ema200" in results:
                results["ema_full_alignment"] = (
                    results["ema9"] > results["ema50"] > results["ema200"]
                )

            results["close"] = float(df["close"].iloc[-1])
            results["price"] = results["close"]
            # ATR as percentage of price
            if "atr" in results:
                results["atr_pct"] = round((results["atr"] / results["close"]) * 100, 4) if results["close"] > 0 else 0
            if "atr_pct" in results:
                results["atr_percent"] = results["atr_pct"]

            # Derived: EMA 9 distance as percentage of current price
            if "ema9" in results and results["close"] > 0 and results["ema9"] > 0:
                results["ema9_distance_pct"] = round(
                    (results["close"] - results["ema9"]) / results["ema9"] * 100, 4
                )

        except Exception as e:
            logger.exception(f"Error calculating indicators: {e}")

        return results

    # ── Individual indicator calculations ──────────────────────────

    def _calc_rsi(self, df: pd.DataFrame) -> Dict[str, Any]:
        period = self.config["rsi"].get("period", 14)
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return {"rsi": round(float(val), 2) if pd.notna(val) else None}

    def _calc_adx(self, df: pd.DataFrame) -> Dict[str, Any]:
        period = self.config["adx"].get("period", 14)
        high, low, close = df["high"], df["low"], df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.rolling(window=period).mean()

        # ADX acceleration (current vs previous)
        adx_prev = adx.iloc[-2] if len(adx) >= 2 and pd.notna(adx.iloc[-2]) else None
        adx_val = adx.iloc[-1]

        result = {
            "adx": round(float(adx_val), 2) if pd.notna(adx_val) else None,
            "di_plus": round(float(plus_di.iloc[-1]), 2) if pd.notna(plus_di.iloc[-1]) else None,
            "di_minus": round(float(minus_di.iloc[-1]), 2) if pd.notna(minus_di.iloc[-1]) else None,
        }
        if not pd.notna(adx_val):
            logger.warning(
                "ADX returned null — insufficient candles for period=%d "
                "(have %d rows, need ≥%d for two rolling windows).",
                period, len(df), period * 2,
            )
        if adx_prev is not None and pd.notna(adx_val):
            result["adx_acceleration"] = round(float(adx_val) - float(adx_prev), 2)
        return result

    def _calc_ema(self, df: pd.DataFrame) -> Dict[str, Any]:
        periods = self.config["ema"].get("periods", [5, 9, 21, 50, 200])
        results = {}
        for p in periods:
            if len(df) >= p:
                ema = df["close"].ewm(span=p, adjust=False).mean()
                results[f"ema{p}"] = round(float(ema.iloc[-1]), 8)
        return results

    def _calc_atr(self, df: pd.DataFrame) -> Dict[str, Any]:
        period = self.config["atr"].get("period", 14)
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        val = atr.iloc[-1]
        return {"atr": round(float(val), 8) if pd.notna(val) else None}

    def _calc_macd(self, df: pd.DataFrame) -> Dict[str, Any]:
        cfg = self.config["macd"]
        fast, slow, signal_p = cfg.get("fast", 12), cfg.get("slow", 26), cfg.get("signal", 9)
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_p, adjust=False).mean()
        histogram = macd_line - signal_line

        macd_val = macd_line.iloc[-1]
        sig_val = signal_line.iloc[-1]
        hist_val = histogram.iloc[-1]
        return {
            "macd": round(float(macd_val), 8) if pd.notna(macd_val) else None,
            "macd_signal_line": round(float(sig_val), 8) if pd.notna(sig_val) else None,
            "macd_histogram": round(float(hist_val), 8) if pd.notna(hist_val) else None,
            "macd_signal": "positive" if pd.notna(macd_val) and macd_val > sig_val else "negative",
        }

    def _calc_vwap(self, df: pd.DataFrame) -> Dict[str, Any]:
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
        cumulative_vol = df["volume"].cumsum()
        vwap = cumulative_tp_vol / cumulative_vol.replace(0, np.nan)
        val = vwap.iloc[-1]
        close = df["close"].iloc[-1]
        result = {"vwap": round(float(val), 8) if pd.notna(val) else None}
        if pd.notna(val) and val > 0:
            result["vwap_distance_pct"] = round(((close - val) / val) * 100, 4)
        return result

    def _calc_stochastic(self, df: pd.DataFrame) -> Dict[str, Any]:
        cfg = self.config["stochastic"]
        k_period = cfg.get("k", 14)
        d_period = cfg.get("d", 3)
        smooth = cfg.get("smooth", 3)

        low_min = df["low"].rolling(window=k_period).min()
        high_max = df["high"].rolling(window=k_period).max()
        fast_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
        slow_k = fast_k.rolling(window=smooth).mean()
        slow_d = slow_k.rolling(window=d_period).mean()

        return {
            "stoch_k": round(float(slow_k.iloc[-1]), 2) if pd.notna(slow_k.iloc[-1]) else None,
            "stoch_d": round(float(slow_d.iloc[-1]), 2) if pd.notna(slow_d.iloc[-1]) else None,
        }

    def _calc_obv(self, df: pd.DataFrame) -> Dict[str, Any]:
        obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
        return {"obv": round(float(obv.iloc[-1]), 2)}

    def _calc_bollinger(self, df: pd.DataFrame) -> Dict[str, Any]:
        cfg = self.config["bollinger"]
        period = cfg.get("period", 20)
        deviation = cfg.get("deviation", 2.0)

        sma = df["close"].rolling(window=period).mean()
        std = df["close"].rolling(window=period).std()
        upper = sma + deviation * std
        lower = sma - deviation * std
        width = (upper - lower) / sma.replace(0, np.nan)

        return {
            "bb_upper": round(float(upper.iloc[-1]), 8) if pd.notna(upper.iloc[-1]) else None,
            "bb_middle": round(float(sma.iloc[-1]), 8) if pd.notna(sma.iloc[-1]) else None,
            "bb_lower": round(float(lower.iloc[-1]), 8) if pd.notna(lower.iloc[-1]) else None,
            "bb_width": round(float(width.iloc[-1]), 6) if pd.notna(width.iloc[-1]) else None,
        }

    def _calc_parabolic_sar(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Simplified Parabolic SAR."""
        step = self.config.get("parabolic_sar", {}).get("step", 0.02)
        max_step = self.config.get("parabolic_sar", {}).get("max_step", 0.2)

        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        n = len(close)
        if n < 3:
            return {"psar": None}

        psar = np.zeros(n)
        af = step
        bull = True
        ep = low[0]
        psar[0] = high[0]

        for i in range(1, n):
            if bull:
                psar[i] = psar[i - 1] + af * (ep - psar[i - 1])
                psar[i] = min(psar[i], low[i - 1])
                if low[i] < psar[i]:
                    bull = False
                    psar[i] = ep
                    ep = low[i]
                    af = step
                else:
                    if high[i] > ep:
                        ep = high[i]
                        af = min(af + step, max_step)
            else:
                psar[i] = psar[i - 1] + af * (ep - psar[i - 1])
                psar[i] = max(psar[i], high[i - 1])
                if high[i] > psar[i]:
                    bull = True
                    psar[i] = ep
                    ep = high[i]
                    af = step
                else:
                    if low[i] < ep:
                        ep = low[i]
                        af = min(af + step, max_step)

        return {"psar": round(float(psar[-1]), 8), "psar_trend": "bullish" if bull else "bearish"}

    def _calc_zscore(self, df: pd.DataFrame) -> Dict[str, Any]:
        lookback = self.config.get("zscore", {}).get("lookback", 20)
        mean = df["close"].rolling(window=lookback).mean()
        std = df["close"].rolling(window=lookback).std()
        zscore = (df["close"] - mean) / std.replace(0, np.nan)
        val = zscore.iloc[-1]
        return {"zscore": round(float(val), 4) if pd.notna(val) else None}

    def _calc_volume_delta(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Approximated volume delta using candle direction."""
        direction = np.sign(df["close"] - df["open"])
        delta = (direction * df["volume"]).iloc[-1]
        return {"volume_delta": round(float(delta), 2) if pd.notna(delta) else 0}

    def _calc_volume_spike(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Volume relative to 20-period average."""
        avg_vol = df["volume"].rolling(window=20).mean()
        val = avg_vol.iloc[-1]
        if pd.notna(val) and val > 0:
            spike = df["volume"].iloc[-1] / val
            return {"volume_spike": round(float(spike), 2)}
        return {"volume_spike": 1.0}

    def _calc_taker_ratio(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Approximate taker buy ratio from candle direction over last 20 periods.

        Uses bullish candle volume / total volume as a proxy for buy pressure.
        Returns value between 0.0 and 1.0 where > 0.5 indicates net buying.
        """
        lookback = min(20, len(df))
        recent = df.tail(lookback)
        if recent.empty or recent["volume"].sum() == 0:
            return {"taker_ratio": 0.5}

        bullish_mask = recent["close"] >= recent["open"]
        buy_volume = recent.loc[bullish_mask, "volume"].sum()
        total_volume = recent["volume"].sum()
        ratio = buy_volume / total_volume if total_volume > 0 else 0.5
        return {"taker_ratio": round(float(ratio), 4)}
