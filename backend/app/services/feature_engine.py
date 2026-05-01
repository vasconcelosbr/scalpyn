"""Feature Engine — calculates technical indicators dynamically from config."""

import logging
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class FeatureEngine:
    """Calculates technical indicators dynamically based on user configuration."""

    def __init__(self, indicators_config: Dict[str, Any]):
        self.config = indicators_config

    def calculate(
        self,
        df: pd.DataFrame,
        market_data: Optional[Dict[str, Any]] = None,
        group: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate enabled indicators for the given OHLCV DataFrame.

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
                and optional [time, quote_volume].
            market_data: Live market-data dict (orderbook, spread, taker data).
            group: Optional filter — 'structural' computes only slow/OHLCV-based
                indicators; 'microstructure' computes only fast/live-data
                indicators.  None (default) computes everything (legacy path).

        Returns:
            Dictionary of indicator_name -> value (latest value).
        """
        from .indicator_classifier import STRUCTURAL_CALC_KEYS, MICROSTRUCTURE_CALC_KEYS

        def _want(key: str) -> bool:
            """Return True when this calc-key should run for the requested group.

            "ema" is in BOTH sets because each group computes different period
            subsets; post-compute filtering strips the irrelevant periods.
            "stochastic" is microstructure (fast signal on 5m candles).
            """
            if group is None or group == "all":
                return True
            if group == "structural":
                return key in STRUCTURAL_CALC_KEYS
            if group == "microstructure":
                return key in MICROSTRUCTURE_CALC_KEYS
            return True
        if df is None or df.empty or len(df) < 2:
            logger.warning("Insufficient data for indicator calculation")
            return {}

        results: Dict[str, Any] = {}

        try:
            if _want("rsi") and self.config.get("rsi", {}).get("enabled"):
                results.update(self._calc_rsi(df))

            if _want("adx") and self.config.get("adx", {}).get("enabled"):
                results.update(self._calc_adx(df))

            if _want("ema") and self.config.get("ema", {}).get("enabled"):
                results.update(self._calc_ema(df))

            if _want("atr") and self.config.get("atr", {}).get("enabled"):
                results.update(self._calc_atr(df))

            if _want("macd") and self.config.get("macd", {}).get("enabled"):
                results.update(self._calc_macd(df))

            if _want("vwap") and self.config.get("vwap", {}).get("enabled"):
                results.update(self._calc_vwap(df))

            if _want("stochastic") and self.config.get("stochastic", {}).get("enabled"):
                results.update(self._calc_stochastic(df))

            if _want("obv") and self.config.get("obv", {}).get("enabled"):
                results.update(self._calc_obv(df))

            if _want("bollinger") and self.config.get("bollinger", {}).get("enabled"):
                results.update(self._calc_bollinger(df))

            if _want("parabolic_sar") and self.config.get("parabolic_sar", {}).get("enabled"):
                results.update(self._calc_parabolic_sar(df))

            if _want("zscore") and self.config.get("zscore", {}).get("enabled"):
                results.update(self._calc_zscore(df))

            if _want("volume_delta") and self.config.get("volume_delta", {}).get("enabled"):
                results.update(self._calc_volume_delta(df))

            if _want("volume_metrics") and self.config.get("volume_metrics", {}).get("enabled", True):
                results.update(self._calc_volume_metrics(df))

            if _want("volume_spike") and self.config.get("volume_spike", {}).get("enabled", True):
                results.update(self._calc_volume_spike(df))

            if _want("taker_ratio") and self.config.get("taker_ratio", {}).get("enabled", True):
                results.update(self._calc_taker_ratio(df))

            # ── Post-compute: EMA period filtering by group ────────────────────
            # "ema" calc key runs in both groups but each stores only its subset:
            #   structural   → EMA50, EMA200 (slow, anchor for trend structure)
            #   microstructure → EMA5, EMA9, EMA21 (fast, entry timing)
            # Hybrid derived values (ema9_gt_ema50, ema_full_alignment) are
            # computed at query-merge time when both groups' data is available;
            # each scheduler only stores what it can compute independently.
            _EMA_STRUCT_KEYS = frozenset({
                "ema50", "ema200", "ema50_gt_ema200",
            })
            _EMA_MICRO_KEYS = frozenset({
                "ema5", "ema9", "ema21",
                "ema9_gt_ema21",   # EMA9 vs EMA21 — both in micro
                "ema9_distance_pct",
            })

            if group == "structural":
                # Strip fast-EMA keys — structural scheduler only stores EMA50/200
                for _k in list(_EMA_MICRO_KEYS):
                    results.pop(_k, None)
                # Also strip ema-vs-micro derived hybrids (need merge to compute)
                results.pop("ema9_gt_ema50", None)
                results.pop("ema_full_alignment", None)
            elif group == "microstructure":
                # Strip slow-EMA keys — micro scheduler only stores EMA5/9/21
                for _k in list(_EMA_STRUCT_KEYS):
                    results.pop(_k, None)
                # Strip hybrids — need structural EMA50/200 to be meaningful
                results.pop("ema9_gt_ema50", None)
                results.pop("ema_full_alignment", None)
                results.pop("ema50_gt_ema200", None)

            # ── EMA-derived alignment flags (group=None / "all" only) ─────────
            if group is None or group == "all":
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

            # EMA9-vs-EMA21 alignment (within microstructure group)
            if group == "microstructure":
                if "ema9" in results and "ema21" in results:
                    results["ema9_gt_ema21"] = results["ema9"] > results["ema21"]

            # EMA50-vs-EMA200 alignment (within structural group)
            if group == "structural":
                if "ema50" in results and "ema200" in results:
                    results["ema50_gt_ema200"] = results["ema50"] > results["ema200"]

            # close/price: structural and combined keep the 1h close
            if group is None or group == "all" or group == "structural":
                results["close"] = float(df["close"].iloc[-1])
                results["price"] = results["close"]
            # Microstructure keeps the 5m close for distance computation
            if group == "microstructure":
                _close_5m = float(df["close"].iloc[-1])
                results["close_5m"] = _close_5m

            # ATR as percentage of price
            if results.get("atr") is not None and results.get("close"):
                results["atr_pct"] = round(
                    (results["atr"] / results["close"]) * 100, 4
                ) if results["close"] > 0 else 0
            if "atr_pct" in results:
                results["atr_percent"] = results["atr_pct"]

            # Derived: EMA 9 distance as percentage of current price
            _close_for_dist = results.get("close") or results.get("close_5m")
            if "ema9" in results and _close_for_dist and _close_for_dist > 0 and results["ema9"] > 0:
                results["ema9_distance_pct"] = round(
                    (_close_for_dist - results["ema9"]) / results["ema9"] * 100, 4
                )

            if market_data:
                results.update(self._apply_market_data_overrides(market_data))

        except Exception as e:
            logger.exception(f"Error calculating indicators: {e}")

        return results

    # ── Individual indicator calculations ──────────────────────────

    @staticmethod
    def _base_volume(df: pd.DataFrame) -> pd.Series:
        if "volume" in df.columns:
            return pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
        if "base_volume" in df.columns:
            return pd.to_numeric(df["base_volume"], errors="coerce").fillna(0.0)
        return pd.Series(np.zeros(len(df)), index=df.index, dtype=float)

    @staticmethod
    def _quote_volume(df: pd.DataFrame) -> pd.Series:
        if "quote_volume" in df.columns:
            return pd.to_numeric(df["quote_volume"], errors="coerce").fillna(0.0)
        base_volume = FeatureEngine._base_volume(df)
        closes = pd.to_numeric(df["close"], errors="coerce").fillna(0.0)
        return base_volume * closes

    def _calc_volume_metrics(self, df: pd.DataFrame) -> Dict[str, Any]:
        base_volume = self._base_volume(df)
        quote_volume = self._quote_volume(df)
        min_coverage_hours = float(
            self.config.get("volume_metrics", {}).get("min_coverage_hours", 23.5)
        )

        result: Dict[str, Any] = {
            "volume_last_candle_base": round(float(base_volume.iloc[-1]), 8),
            "volume_last_candle_usdt": round(float(quote_volume.iloc[-1]), 8),
        }

        if "time" not in df.columns:
            return result

        times = pd.to_datetime(df["time"], utc=True, errors="coerce")
        if times.isna().all():
            return result

        valid_times = times.dropna()
        if len(valid_times) < 2:
            return result

        interval = valid_times.diff().dropna().median()
        if pd.isna(interval) or interval <= pd.Timedelta(0):
            return result

        window_end = valid_times.iloc[-1]
        window_start = window_end - pd.Timedelta(hours=24) + interval
        window_mask = times >= window_start
        window_times = valid_times[valid_times >= window_start]
        coverage_hours = (
            ((window_end - window_times.iloc[0]) + interval).total_seconds() / 3600
            if not window_times.empty else 0.0
        )

        result["volume_24h_candles"] = int(window_mask.sum())
        result["volume_24h_coverage_hours"] = round(float(coverage_hours), 4)

        if coverage_hours < min_coverage_hours:
            logger.debug(
                "Skipping 24h volume aggregation: only %.2f h of coverage available (need ≥ %.2f h)",
                coverage_hours,
                min_coverage_hours,
            )
            return result

        # Diagnostic-only candle sums. The canonical 24h volume comes from the
        # Gate.io ticker; these can undercount when OHLCV has gaps.
        result["volume_24h_base_aggregated"] = round(float(base_volume.loc[window_mask].sum()), 8)
        result["volume_24h_usdt_aggregated"] = round(float(quote_volume.loc[window_mask].sum()), 8)
        return result

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
            min_required = period * 2  # two sequential rolling(period) windows
            logger.warning(
                "ADX returned null — insufficient candles for period=%d "
                "(have %d rows, need ≥%d for two rolling windows).",
                period, len(df), min_required,
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

        hist_prev = histogram.iloc[-2] if len(histogram) >= 2 else None
        # Use the 10 candles prior to (excluding) the current one so that
        # the stored mean/std match the baseline used by validate_macd_histogram.
        hist_prior = histogram.iloc[-11:-1] if len(histogram) >= 11 else histogram.iloc[:-1]
        hist_mean = hist_prior.mean() if len(hist_prior) >= 1 else float("nan")
        hist_std = hist_prior.std(ddof=0) if len(hist_prior) >= 2 else 0.0

        hist_slope: Optional[float] = (
            round(float(hist_val - hist_prev), 8)
            if hist_prev is not None and pd.notna(hist_val) and pd.notna(hist_prev)
            else None
        )

        # Robust indicators (Phase 1): macd_histogram_pct expresses the
        # raw histogram as a percentage of the latest close price so it is
        # comparable across symbols of wildly different price scales.
        close_val = df["close"].iloc[-1] if len(df) else float("nan")
        if (
            pd.notna(hist_val)
            and pd.notna(close_val)
            and float(close_val) != 0.0
        ):
            macd_histogram_pct = round(float(hist_val) / float(close_val) * 100.0, 6)
        else:
            macd_histogram_pct = None

        return {
            "macd": round(float(macd_val), 8) if pd.notna(macd_val) else None,
            "macd_signal_line": round(float(sig_val), 8) if pd.notna(sig_val) else None,
            "macd_histogram": round(float(hist_val), 8) if pd.notna(hist_val) else None,
            "macd_histogram_pct": macd_histogram_pct,
            "macd_signal": "positive" if pd.notna(macd_val) and macd_val > sig_val else "negative",
            "macd_histogram_prev": round(float(hist_prev), 8) if hist_prev is not None and pd.notna(hist_prev) else None,
            "macd_histogram_slope": hist_slope,
            "macd_histogram_mean_10": round(float(hist_mean), 8) if pd.notna(hist_mean) else None,
            "macd_histogram_std_10": round(float(hist_std), 8) if pd.notna(hist_std) else None,
        }

    def _calc_vwap(self, df: pd.DataFrame) -> Dict[str, Any]:
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        volume = self._base_volume(df)
        cumulative_tp_vol = (typical_price * volume).cumsum()
        cumulative_vol = volume.cumsum()
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
        obv = (np.sign(df["close"].diff()) * self._base_volume(df)).fillna(0).cumsum()
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
        """Approximated volume delta using candle direction.

        The candle approximation is a coarse proxy for real taker buy minus
        sell flow. Robust indicators (Phase 1) opt out by setting
        ``allow_candle_fallback=False`` in the indicator config — when the
        flag is explicitly False we return ``None`` so the envelope tags it
        as ``NO_DATA`` instead of a fake signal.

        Backward compatibility: when the key is **missing** from the config
        (i.e. an existing user whose ConfigProfile predates this change)
        we fall back to the legacy candle approximation so end-user
        behaviour does not change. New users seeded after this commit get
        ``allow_candle_fallback=False`` from ``DEFAULT_INDICATORS``.
        """
        cfg = self.config.get("volume_delta", {}) or {}
        if cfg.get("allow_candle_fallback", True) is False:
            return {"volume_delta": None}
        direction = np.sign(df["close"] - df["open"])
        delta = (direction * self._base_volume(df)).iloc[-1]
        return {"volume_delta": round(float(delta), 2) if pd.notna(delta) else 0}

    def _calc_volume_spike(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Volume relative to 20-period average."""
        lookback = max(int(self.config.get("volume_spike", {}).get("lookback", 20)), 1)
        volume = self._base_volume(df)
        avg_vol = volume.rolling(window=lookback).mean()
        val = avg_vol.iloc[-1]
        if pd.notna(val) and val > 0:
            spike = volume.iloc[-1] / val
            return {"volume_spike": round(float(spike), 2)}
        return {"volume_spike": 1.0}

    def _calc_taker_ratio(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Approximate taker buy ratio from candle direction over last 20 periods.

        IMPORTANT: This is a LOW-CONFIDENCE approximation. Real taker data from
        MarketDataService (Binance trades) should override this value.

        Uses bullish candle volume / total volume as a proxy for buy pressure.
        Returns value between 0.0 and 1.0 where > 0.5 indicates net buying.

        Robust indicators (Phase 1): when ``allow_candle_fallback`` is
        explicitly False we return ``None`` so the envelope tags the
        indicator as ``NO_DATA`` instead of producing an unreliable proxy
        that masquerades as real flow.

        Backward compatibility: a missing key (existing ConfigProfiles
        from before this change) defaults to True so legacy behaviour is
        preserved. New seed_service.DEFAULT_INDICATORS sets it False.
        """
        cfg = self.config.get("taker_ratio", {}) or {}
        if cfg.get("allow_candle_fallback", True) is False:
            return {"taker_ratio": None}
        lookback = min(max(int(cfg.get("lookback", 20)), 1), len(df))
        recent = df.tail(lookback)
        volume = self._base_volume(recent)
        if recent.empty or volume.sum() == 0:
            return {"taker_ratio": 0.5}

        bullish_mask = recent["close"] >= recent["open"]
        buy_volume = volume.loc[bullish_mask].sum()
        total_volume = volume.sum()
        ratio = buy_volume / total_volume if total_volume > 0 else 0.5
        return {"taker_ratio": round(float(ratio), 4)}

    def _apply_market_data_overrides(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        # Canonical ticker values from MarketDataService override candle sums.
        overrides: Dict[str, Any] = {}
        for key in (
            "volume_24h_base",
            "volume_24h_usdt",
            "orderbook_depth_usdt",
            "spread_pct",
            "taker_buy_volume",
            "taker_sell_volume",
            "taker_ratio",
            "volume_delta",
            "market_data_symbol",
            "market_data_source",
            "market_data_confidence",
        ):
            value = market_data.get(key)
            if value is not None:
                overrides[key] = value
        return overrides
