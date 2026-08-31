"""Pure, deterministic Shadow barrier evaluation over closed OHLCV candles.

This module is deliberately independent from SQLAlchemy and Celery so the
monitor, fast scan, replay and measurement paths can share one first-touch
contract.  It never accepts ticker/current-price samples as outcome authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Mapping


CONTRACT_VERSION = "shadow_closed_ohlcv_first_touch_v1"
SOURCE = "ohlcv"
TIMEFRAME = "1m"
CANDLE_POLICY = "CLOSED_ONLY"
INTRABAR_CONVENTION = "SL_FIRST"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _input_hash(candles: list[dict[str, Any]], inputs: Mapping[str, Any]) -> str:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "inputs": {str(key): _iso(value) for key, value in inputs.items()},
        "candles": [
            {
                "time": _iso(row.get("time")),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
            }
            for row in candles
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_closed_candles(
    candles: Iterable[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_timestamp: datetime,
    tp_price: float,
    sl_price: float,
    timeout_candles: int | None = None,
    candles_seen_before: int = 0,
    prior_high_water_mark: float | None = None,
    trailing_activation_profit_pct: float | None = None,
    trailing_hwm_pct: float | None = None,
    trailing_never_sell_at_loss: bool = False,
    trailing_protected_profit_pct: float = 0.0,
) -> dict[str, Any]:
    """Evaluate closed 1m candles chronologically using conservative ordering.

    A touch in the partial entry candle is not ordered relative to the entry,
    therefore it is returned as ``BARRIER_PATH_UNRESOLVED`` instead of being
    guessed.  For later candles, trailing then SL then TP are evaluated.  When
    SL and TP occur in the same candle the outcome is SL and the evidence is
    explicitly marked ``BOTH_SAME_CANDLE``.
    """

    ordered = [dict(row) for row in candles]
    ordered.sort(key=lambda row: row.get("time"))
    entry_bucket = entry_timestamp.replace(second=0, microsecond=0)
    entry_boundary_partial = entry_timestamp != entry_bucket
    hwm = max(float(entry_price), float(prior_high_water_mark or entry_price))
    min_price: float | None = None
    max_price: float | None = None
    min_price_at: datetime | None = None
    max_price_at: datetime | None = None
    last_candle_at: datetime | None = None
    result: dict[str, Any] = {
        "status": "PENDING",
        "outcome": None,
        "reason_code": "NO_CLOSED_CANDLE_AVAILABLE" if not ordered else None,
        "barrier_touched": None,
        "barrier_touched_at": None,
        "exit_price_nominal": None,
        "exit_price_observed": None,
        "exit_price_semantics": None,
        "entry_boundary_partial": entry_boundary_partial,
        "intrabar_convention": INTRABAR_CONVENTION,
        "source": SOURCE,
        "timeframe": TIMEFRAME,
        "candle_policy": CANDLE_POLICY,
        "contract_version": CONTRACT_VERSION,
    }

    for index, candle in enumerate(ordered, start=1):
        candle_at = candle.get("time")
        high = candle.get("high")
        low = candle.get("low")
        if candle_at is None or high is None or low is None:
            continue
        high = float(high)
        low = float(low)
        last_candle_at = candle_at
        if min_price is None or low < min_price:
            min_price, min_price_at = low, candle_at
        if max_price is None or high > max_price:
            max_price, max_price_at = high, candle_at

        trailing_stop: float | None = None
        if (
            trailing_activation_profit_pct is not None
            and trailing_hwm_pct is not None
            and trailing_activation_profit_pct > 0
            and trailing_hwm_pct > 0
            and hwm >= entry_price * (1 + trailing_activation_profit_pct / 100)
        ):
            candidate = hwm * (1 - trailing_hwm_pct / 100)
            protected = entry_price * (1 + trailing_protected_profit_pct / 100)
            if not trailing_never_sell_at_loss or candidate >= protected:
                trailing_stop = candidate

        sl_hit = low <= sl_price
        tp_hit = high >= tp_price
        trailing_hit = (
            trailing_stop is not None
            and trailing_stop > sl_price
            and low <= trailing_stop
        )
        entry_candle = candle_at == entry_bucket
        if entry_boundary_partial and entry_candle and (trailing_hit or sl_hit or tp_hit):
            result.update(
                {
                    "status": "UNRESOLVED",
                    "reason_code": "BARRIER_PATH_UNRESOLVED",
                    "barrier_touched": "BARRIER_PATH_UNRESOLVED",
                    "barrier_touched_at": candle_at,
                    "exit_price_semantics": "ENTRY_PARTIAL_CANDLE_UNRESOLVED",
                }
            )
            break
        if trailing_hit:
            result.update(
                {
                    "status": "OUTCOME",
                    "outcome": "TRAILING_STOP",
                    "reason_code": "TRAILING_FIRST_TOUCH",
                    "barrier_touched": "TRAILING",
                    "barrier_touched_at": candle_at,
                    "exit_price_nominal": trailing_stop,
                    "exit_price_observed": low,
                    "exit_price_semantics": "CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL",
                }
            )
            break
        if sl_hit:
            result.update(
                {
                    "status": "OUTCOME",
                    "outcome": "SL_HIT",
                    "reason_code": "BOTH_SAME_CANDLE" if tp_hit else "SL_FIRST_TOUCH",
                    "barrier_touched": "BOTH_SAME_CANDLE" if tp_hit else "SL",
                    "barrier_touched_at": candle_at,
                    "exit_price_nominal": sl_price,
                    "exit_price_observed": low,
                    "exit_price_semantics": "CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL",
                }
            )
            break
        if tp_hit:
            result.update(
                {
                    "status": "OUTCOME",
                    "outcome": "TP_HIT",
                    "reason_code": "TP_FIRST_TOUCH",
                    "barrier_touched": "TP",
                    "barrier_touched_at": candle_at,
                    "exit_price_nominal": tp_price,
                    "exit_price_observed": high,
                    "exit_price_semantics": "CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL",
                }
            )
            break

        hwm = max(hwm, high)
        if timeout_candles and candles_seen_before + index >= timeout_candles:
            close = candle.get("close")
            exit_price = float(close if close is not None else candle.get("open"))
            result.update(
                {
                    "status": "OUTCOME",
                    "outcome": "TIMEOUT",
                    "reason_code": "TIMEOUT_CANDLE_LIMIT",
                    "barrier_touched": "NONE",
                    "barrier_touched_at": None,
                    "exit_price_nominal": None,
                    "exit_price_observed": exit_price,
                    "exit_price_semantics": "TIMEOUT_CANDLE_CLOSE",
                }
            )
            break

    if ordered and result["status"] == "PENDING":
        result["reason_code"] = "NO_BARRIER_TOUCH"
    result.update(
        {
            "last_candle_at": last_candle_at,
            "min_price": min_price,
            "min_price_at": min_price_at,
            "max_price": max_price,
            "max_price_at": max_price_at,
            "high_water_mark": hwm,
            "candles_evaluated": len(ordered),
        }
    )
    result["input_hash"] = _input_hash(
        ordered,
        {
            "entry_price": entry_price,
            "entry_timestamp": entry_timestamp,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "timeout_candles": timeout_candles,
            "candles_seen_before": candles_seen_before,
            "prior_high_water_mark": prior_high_water_mark,
            "trailing_activation_profit_pct": trailing_activation_profit_pct,
            "trailing_hwm_pct": trailing_hwm_pct,
            "trailing_never_sell_at_loss": trailing_never_sell_at_loss,
            "trailing_protected_profit_pct": trailing_protected_profit_pct,
        },
    )
    return result
