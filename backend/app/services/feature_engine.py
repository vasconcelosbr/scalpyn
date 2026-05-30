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

        # ── Per-indicator isolation: each calc runs in its own try/except so
        # that a failure in one indicator does not abort the remaining ones.
        if _want("rsi") and self.config.get("rsi", {}).get("enabled"):
            try:
                results.update(self._calc_rsi(df))
            except Exception as e:
                logger.exception("rsi calculation failed: %s", e)

        if _want("adx") and self.config.get("adx", {}).get("enabled"):
            try:
                results.update(self._calc_adx(df))
            except Exception as e:
                logger.exception("adx calculation failed: %s", e)

        if _want("ema") and self.config.get("ema", {}).get("enabled"):
            try:
                results.update(self._calc_ema(df))
            except Exception as e:
                logger.exception("ema calculation failed: %s", e)

        if _want("atr") and self.config.get("atr", {}).get("enabled"):
            try:
                results.update(self._calc_atr(df))
            except Exception as e:
                logger.exception("atr calculation failed: %s", e)

        if _want("macd") and self.config.get("macd", {}).get("enabled"):
            try:
                results.update(self._calc_macd(df))
            except Exception as e:
                logger.exception("macd calculation failed: %s", e)

        if _want("vwap") and self.config.get("vwap", {}).get("enabled"):
            try:
                results.update(self._calc_vwap(df))
            except Exception as e:
                logger.exception("vwap calculation failed: %s", e)

        if _want("stochastic") and self.config.get("stochastic", {}).get("enabled"):
            try:
                results.update(self._calc_stochastic(df))
            except Exception as e:
                logger.exception("stochastic calculation failed: %s", e)

        if _want("obv") and self.config.get("obv", {}).get("enabled"):
            try:
                results.update(self._calc_obv(df))
            except Exception as e:
                logger.exception("obv calculation failed: %s", e)

        if _want("bollinger") and self.config.get("bollinger", {}).get("enabled"):
            try:
                results.update(self._calc_bollinger(df))
            except Exception as e:
                logger.exception("bollinger calculation failed: %s", e)

        if _want("parabolic_sar") and self.config.get("parabolic_sar", {}).get("enabled"):
            try:
                results.update(self._calc_parabolic_sar(df))
            except Exception as e:
                logger.exception("parabolic_sar calculation failed: %s", e)

        if _want("zscore") and self.config.get("zscore", {}).get("enabled"):
            try:
                results.update(self._calc_zscore(df))
            except Exception as e:
                logger.exception("zscore calculation failed: %s", e)

        if _want("volume_delta") and self.config.get("volume_delta", {}).get("enabled"):
            try:
                results.update(self._calc_volume_delta(df))
            except Exception as e:
                logger.exception("volume_delta calculation failed: %s", e)

        if _want("volume_metrics") and self.config.get("volume_metrics", {}).get("enabled", True):
            try:
                results.update(self._calc_volume_metrics(df))
            except Exception as e:
                logger.exception("volume_metrics calculation failed: %s", e)

        if _want("volume_spike") and self.config.get("volume_spike", {}).get("enabled", True):
            try:
                results.update(self._calc_volume_spike(df))
            except Exception as e:
                logger.exception("volume_spike calculation failed: %s", e)

        if _want("taker_ratio") and self.config.get("taker_ratio", {}).get("enabled", True):
            try:
                results.update(self._calc_taker_ratio(df))
            except Exception as e:
                logger.exception("taker_ratio calculation failed: %s", e)

        if _want("entry_exhaustion") and self.config.get("entry_exhaustion", {}).get("enabled", True):
            try:
                results.update(self._calc_entry_exhaustion(df))
            except Exception as e:
                logger.exception("entry_exhaustion calculation failed: %s", e)

        try:
            # ── Post-compute: EMA period filtering by group ────────────────────
            # "ema" calc key runs in both groups but each stores only its subset:
            #   structural   → EMA50, EMA200 (slow, anchor for trend structure)
            #   microstructure → EMA5, EMA9, EMA21 (fast, entry timing)
            # Hybrid derived values (ema9_gt_ema50, ema_full_alignment) are
            # computed at query-merge time when both groups' data is available;
            # each scheduler only stores what it can compute independently.
            # EMA30 (period 22-49 → struct/hybrid) + EMA10 (period ≤21 → micro)
            # mantêm o mesmo padrão de filtragem por período já aplicado em
            # indicator_classifier (Priority 2: EMA period rule).
            _EMA_STRUCT_KEYS = frozenset({
                "ema30", "ema50", "ema200", "ema50_gt_ema200",
            })
            _EMA_MICRO_KEYS = frozenset({
                "ema5", "ema9", "ema10", "ema21",
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
        """RSI canônico (`rsi`, período `config["rsi"]["period"]`, default 14)
        + RSI multi-período aditivo (`rsi_6`, `rsi_12`, `rsi_24`) quando
        `config["rsi"]["periods"]` é uma lista. Cada período roda em try/except
        isolado: falha em rsi_24 nunca afeta rsi_6/12 nem o `rsi` legado.
        Retorna None pra qualquer período sem candles suficientes
        (mín = period + 1) — nunca lança.
        """
        cfg = self.config["rsi"]
        result: Dict[str, Any] = {}

        # ── RSI canônico (período único, retrocompat) ────────────────────────
        try:
            period = int(cfg.get("period", 14))
            if len(df) >= period + 1:
                delta = df["close"].diff()
                gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
                loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
                rs = gain / loss.replace(0, np.nan)
                rsi = 100 - (100 / (1 + rs))
                val = rsi.iloc[-1]
                result["rsi"] = round(float(val), 2) if pd.notna(val) else None
            else:
                result["rsi"] = None
        except Exception as exc:
            logger.warning("[FEATURE_ENGINE] rsi (legado) falhou: %s", exc)
            result["rsi"] = None

        # ── RSI multi-período (aditivo, nomes rsi_<N>) ───────────────────────
        periods = cfg.get("periods") or []
        if isinstance(periods, (list, tuple)):
            for raw_p in periods:
                try:
                    p = int(raw_p)
                    if p <= 0:
                        continue
                    key = f"rsi_{p}"
                    if len(df) < p + 1:
                        result[key] = None
                        continue
                    delta = df["close"].diff()
                    gain = delta.where(delta > 0, 0.0).rolling(window=p).mean()
                    loss = (-delta.where(delta < 0, 0.0)).rolling(window=p).mean()
                    rs = gain / loss.replace(0, np.nan)
                    rsi = 100 - (100 / (1 + rs))
                    val = rsi.iloc[-1]
                    result[key] = round(float(val), 2) if pd.notna(val) else None
                except Exception as exc:
                    logger.warning(
                        "[FEATURE_ENGINE] rsi_%s falhou: %s", raw_p, exc
                    )
                    result[f"rsi_{raw_p}"] = None
        return result

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
        result = {
            "vwap": round(float(val), 8) if pd.notna(val) else None,
            # P2-2: warm-up counter — consumers guard against < 12 candles (< 1h on 5m bars)
            "vwap_candle_count": int(len(df)),
        }
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
        result: Dict[str, Any] = {"obv": round(float(obv.iloc[-1]), 2)}
        # P2-1: obv_slope_5 = (obv[-1] - obv[-5]) / 5 — stationary derivative of OBV.
        # Raw OBV is cumulative and non-comparable cross-asset; the slope captures
        # the direction and velocity of flow without absolute scale dependence.
        n = min(5, len(obv))
        if n >= 2:
            slope = (obv.iloc[-1] - obv.iloc[-n]) / n
            result["obv_slope_5"] = round(float(slope), 4) if pd.notna(slope) else None
        else:
            result["obv_slope_5"] = None
        return result

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
        """Parabolic SAR (Wilder) estendido — canônicos + extensões.

        Retorna (todas as chaves preservam comportamento legado de `psar`/`psar_trend`):
            psar, psar_trend, psar_ep, psar_af, psar_distance_pct,
            psar_signal ("BUY" | "SELL" | "HOLD"), psar_reversal (bool)

        Anti-exaustão (bloqueia BUY → "HOLD"):
            * `psar_distance_pct > max_distance_pct`  (preço já correu demais)
            * `rsi > rsi_max`                         (RSI saturado)

        Filtro ADX opcional (bloqueia BUY+SELL → "HOLD"):
            * `adx_filter_enabled = True` E `adx < adx_min_threshold`

        Thresholds lidos de `self.config["parabolic_sar"]` (Zero Hardcode env-safe,
        hidratado por DEFAULT_INDICATORS no seed e tunável via update_config).

        Cross-reference RSI/ADX usa o último valor disponível no DataFrame se
        existir como coluna; caso contrário, anti-exaustão por RSI/ADX é skip
        (não bloqueia). O cross-reference **com `results` do mesmo ciclo** é
        feito downstream se o caller quiser — aqui mantemos a função pura.

        Retrocompat: `step`/`max_step` continuam funcionando como aliases de
        `af_start`/`af_max`. Falha em qualquer extensão NUNCA quebra `psar`/`psar_trend`.
        """
        cfg = self.config.get("parabolic_sar", {}) or {}
        # Aliases: step/max_step (legado) → af_start/af_max (canônico Wilder)
        af_start = float(cfg.get("af_start", cfg.get("step", 0.02)))
        af_increment = float(cfg.get("af_increment", cfg.get("step", 0.02)))
        af_max = float(cfg.get("af_max", cfg.get("max_step", 0.20)))

        try:
            high = df["high"].values
            low = df["low"].values
            close = df["close"].values
            n = len(close)
            if n < 3:
                return {"psar": None, "psar_trend": None}

            psar = np.zeros(n)
            af = af_start
            bull = True
            ep = float(low[0])
            psar[0] = float(high[0])
            reversal_at_last = False

            for i in range(1, n):
                prev_bull = bull
                if bull:
                    psar[i] = psar[i - 1] + af * (ep - psar[i - 1])
                    # Anti-repaint: SAR não invade as 2 mínimas anteriores
                    psar[i] = min(psar[i], low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
                    if low[i] < psar[i]:
                        bull = False
                        psar[i] = ep                # SAR reinicia no EP da tendência anterior
                        ep = float(low[i])         # EP reinicia na mínima do candle atual
                        af = af_start
                    else:
                        if high[i] > ep:
                            ep = float(high[i])
                            af = min(af + af_increment, af_max)
                else:
                    psar[i] = psar[i - 1] + af * (ep - psar[i - 1])
                    psar[i] = max(psar[i], high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
                    if high[i] > psar[i]:
                        bull = True
                        psar[i] = ep
                        ep = float(high[i])
                        af = af_start
                    else:
                        if low[i] < ep:
                            ep = float(low[i])
                            af = min(af + af_increment, af_max)
                if i == n - 1:
                    reversal_at_last = (prev_bull != bull)

            sar_value = float(psar[-1])
            close_last = float(close[-1])
            distance_pct = (
                round(abs(close_last - sar_value) / close_last * 100.0, 4)
                if close_last > 0 else None
            )
        except Exception as exc:
            logger.warning("[FEATURE_ENGINE] _calc_parabolic_sar core falhou: %s", exc)
            return {"psar": None, "psar_trend": None}

        result: Dict[str, Any] = {
            "psar": round(sar_value, 8),
            "psar_trend": "bullish" if bull else "bearish",
            "psar_ep": round(float(ep), 8),
            "psar_af": round(float(af), 6),
            "psar_distance_pct": distance_pct,
            "psar_reversal": bool(reversal_at_last),
        }

        # ── Sinal base: trend bullish e (acabou de reverter ou tendência fresca)
        # → BUY; trend bearish + reversão → SELL; senão HOLD.
        try:
            # Sinal canônico: SAR é um indicador de evento (gatilho de
            # reversal), não de estado. Emitir BUY/SELL em TODOS os candles
            # da mesma tendência geraria spam para o SignalEngine. Sinal
            # ativo só no candle onde a reversal acontece; demais candles
            # ficam HOLD (consumidores que precisam do estado podem ler
            # `psar_trend` diretamente).
            if reversal_at_last and bull:
                signal = "BUY"
            elif reversal_at_last and not bull:
                signal = "SELL"
            else:
                signal = "HOLD"

            # ── Anti-exaustão (bloqueia só BUY) ─────────────────────────────
            max_distance_pct = float(cfg.get("max_distance_pct", 3.0))
            rsi_max = float(cfg.get("rsi_max", 75.0))

            if signal == "BUY" and distance_pct is not None and distance_pct > max_distance_pct:
                logger.debug(
                    "[PSAR] BUY bloqueado: distance_pct=%.4f > max=%.4f",
                    distance_pct, max_distance_pct,
                )
                signal = "HOLD"

            # RSI cross-reference: usa coluna 'rsi' se já existir no DataFrame
            # (algumas pipelines pré-anotam). Caso contrário, anti-exaustão
            # por RSI é skip — bloqueio adicional pode ser feito downstream
            # (compute_indicators) com base no `results` do mesmo ciclo.
            if signal == "BUY" and "rsi" in df.columns:
                try:
                    rsi_now = float(df["rsi"].iloc[-1])
                    if pd.notna(rsi_now) and rsi_now > rsi_max:
                        logger.debug(
                            "[PSAR] BUY bloqueado: rsi=%.2f > rsi_max=%.2f",
                            rsi_now, rsi_max,
                        )
                        signal = "HOLD"
                except (TypeError, ValueError):
                    pass

            # ── Filtro ADX (opcional, bloqueia BUY E SELL) ──────────────────
            if bool(cfg.get("adx_filter_enabled", False)):
                adx_min = float(cfg.get("adx_min_threshold", 20.0))
                if "adx" in df.columns:
                    try:
                        adx_now = float(df["adx"].iloc[-1])
                        if pd.notna(adx_now) and adx_now < adx_min:
                            logger.debug(
                                "[PSAR] %s bloqueado: adx=%.2f < min=%.2f",
                                signal, adx_now, adx_min,
                            )
                            signal = "HOLD"
                    except (TypeError, ValueError):
                        pass

            result["psar_signal"] = signal
        except Exception as exc:
            logger.warning("[FEATURE_ENGINE] _calc_parabolic_sar signal falhou: %s", exc)
            result["psar_signal"] = "HOLD"

        return result

    def _calc_zscore(self, df: pd.DataFrame) -> Dict[str, Any]:
        lookback = self.config.get("zscore", {}).get("lookback", 20)
        mean = df["close"].rolling(window=lookback).mean()
        std = df["close"].rolling(window=lookback).std()
        zscore = (df["close"] - mean) / std.replace(0, np.nan)
        val = zscore.iloc[-1]
        return {"zscore": round(float(val), 4) if pd.notna(val) else None}

    def _calc_volume_delta(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Volume delta requires real taker buy/sell flow.

        Robust-indicators contract: never approximate from candle direction.
        When the primary order-flow source has not provided ``volume_delta``,
        return ``None`` so the envelope tags the indicator as ``NO_DATA``
        instead of producing a fake signal.
        """
        return {"volume_delta": None}

    def _calc_volume_spike(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Volume relative to 20-period average."""
        lookback = max(int(self.config.get("volume_spike", {}).get("lookback", 20)), 1)
        volume = self._base_volume(df)
        avg_vol = volume.rolling(window=lookback).mean()
        val = avg_vol.iloc[-1]
        if pd.notna(val) and val > 0:
            spike = volume.iloc[-1] / val
            return {"volume_spike": round(float(spike), 2)}
        # Return None (not 1.0) so the envelope tags this as NO_DATA instead of
        # silently reporting "volume exactly at average" when data is absent.
        return {"volume_spike": None}

    def _calc_taker_ratio(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Taker ratio requires real taker buy/sell flow.

        Robust-indicators contract: never approximate from candle direction.
        When the primary order-flow source has not provided ``taker_ratio``,
        return ``None`` so the envelope tags the indicator as ``NO_DATA``
        instead of producing a misleading proxy.
        """
        return {"taker_ratio": None}

    def _calc_entry_exhaustion(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Entry Exhaustion Score — observational Shadow Mode metric (Fase 1).

        Detects potential overextension at entry by combining 5 sub-scores:
          1. acceleration_5  (20%): 5-candle price ROC — fast momentum
          2. acceleration_20 (20%): 20-candle price ROC — broad momentum
          3. distance_from_local_high_50 (30%): proximity to 50-candle high
          4. candle_expansion_ratio (15%): current range vs ATR(14)
          5. volume_percentile (15%): volume rank in 50-candle window

        Returns entry_exhaustion_score in [0, 100]:
          0  = no exhaustion (price cooling, far from high, normal volume)
          100 = maximum exhaustion (fast rise, at the high, expanding candle, high volume)

        Returns None when insufficient candles (< 50).
        Naming: "entry_exhaustion" (not "exhaustion") to differentiate from
        spot_sell_manager._check_exhaustion (exit-side concept).
        """
        MIN_CANDLES = 50
        if len(df) < MIN_CANDLES:
            return {"entry_exhaustion_score": None}

        close = pd.to_numeric(df["close"], errors="coerce")
        high  = pd.to_numeric(df["high"],  errors="coerce")
        low   = pd.to_numeric(df["low"],   errors="coerce")
        vol   = self._base_volume(df)

        c_last = float(close.iloc[-1])
        if not np.isfinite(c_last) or c_last <= 0:
            return {"entry_exhaustion_score": None}

        # 1. acceleration_5 — 5-candle ROC capped at ±20%
        # Higher ROC → higher score (faster rise = more exhausted)
        acc5_score = 50.0  # neutral default
        if len(df) >= 6:
            c_5 = float(close.iloc[-6])
            if np.isfinite(c_5) and c_5 > 0:
                acc5 = max(-20.0, min(20.0, (c_last - c_5) / c_5 * 100))
                acc5_score = (acc5 + 20.0) / 40.0 * 100

        # 2. acceleration_20 — 20-candle ROC capped at ±50%
        acc20_score = 50.0
        if len(df) >= 21:
            c_20 = float(close.iloc[-21])
            if np.isfinite(c_20) and c_20 > 0:
                acc20 = max(-50.0, min(50.0, (c_last - c_20) / c_20 * 100))
                acc20_score = (acc20 + 50.0) / 100.0 * 100

        # 3. distance_from_local_high_50 — proximity to 50-candle rolling high
        # dist_pct ≤ 0: at the high → score=100; -20% below → score=0
        rolling_high = float(high.iloc[-50:].max())
        if np.isfinite(rolling_high) and rolling_high > 0:
            dist_pct = max(-20.0, min(0.0, (c_last - rolling_high) / rolling_high * 100))
            dist_score = (dist_pct + 20.0) / 20.0 * 100
        else:
            dist_score = 50.0

        # 4. candle_expansion_ratio — current H-L range vs ATR(14)
        # Larger candle relative to ATR → more exhaustion
        atr_period = max(1, int(self.config.get("atr", {}).get("period", 14)))
        range_series = (high - low).clip(lower=0)
        current_range = float(range_series.iloc[-1])
        atr_val = float(range_series.rolling(window=min(atr_period, len(df))).mean().iloc[-1])
        if np.isfinite(atr_val) and atr_val > 0:
            expansion = max(0.0, min(5.0, current_range / atr_val))
            expansion_score = expansion / 5.0 * 100
        else:
            expansion_score = 50.0

        # 5. volume_percentile — rank of current volume in 50-candle window
        vol_window = vol.iloc[-50:].values
        v_current = float(vol.iloc[-1])
        if len(vol_window) > 0 and np.isfinite(v_current):
            vol_pct_score = float(np.mean(vol_window <= v_current)) * 100
        else:
            vol_pct_score = 50.0

        # Weighted composite: dist=30%, acc5=20%, acc20=20%, expansion=15%, vol=15%
        score = (
            0.30 * dist_score +
            0.20 * acc5_score +
            0.20 * acc20_score +
            0.15 * expansion_score +
            0.15 * vol_pct_score
        )
        return {"entry_exhaustion_score": round(score, 1)}

    def _apply_market_data_overrides(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        # Canonical ticker values from MarketDataService override candle sums.
        overrides: Dict[str, Any] = {}
        for key in (
            "volume_24h_base",
            "volume_24h_usdt",
            "orderbook_depth_usdt",
            "spread_pct",
            "bid_ask_imbalance",
            "orderbook_pressure",
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
