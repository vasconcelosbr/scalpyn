"""Shadow-mode runner — wraps legacy pipeline outputs in robust envelopes,
runs the validation + score engines, persists snapshots and emits divergence
metrics. Never raises into the caller — the legacy pipeline must stay healthy
even when this code path explodes.

Slack delivery for divergence events is intentionally restricted to a
single ops-only webhook (``ROBUST_ALERTS_OPS_WEBHOOK_URL``) so that one
tenant's symbol/score data can never be broadcast to another tenant's
Slack workspace. When the ops webhook is unset the alert is logged and
dropped — no per-user broadcast.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from .compute import envelope_indicators
from .metrics import (
    divergence_bucket,
    increment_divergence,
    increment_rejection,
    set_indicator_confidence,
    set_indicator_staleness,
)
from .score import calculate_score_with_confidence
from .snapshot import persist_snapshot
from .validation import validate_indicator_integrity

logger = logging.getLogger(__name__)


_DIVERGENCE_RATE_LIMIT_SECONDS = 15 * 60
_LOCAL_DIVERGENCE_RL: dict[str, float] = {}


def is_shadow_enabled() -> bool:
    return bool(getattr(settings, "USE_ROBUST_INDICATORS", False))


def _ops_webhook_url() -> Optional[str]:
    """Resolve the ops-only Slack webhook used for divergence alerts."""
    url = getattr(settings, "ROBUST_ALERTS_OPS_WEBHOOK_URL", None) or os.environ.get(
        "ROBUST_ALERTS_OPS_WEBHOOK_URL"
    )
    return url.strip() if isinstance(url, str) and url.strip() else None


async def _divergence_rate_limit_open(condition: str) -> bool:
    """Return True when a divergence alert is allowed to fire.

    Uses Redis ``SET NX EX`` for cross-process coordination; falls back to
    an in-process dict when Redis is unavailable.
    """
    try:
        from ..config_service import _make_redis_client
        redis_client = _make_redis_client()
    except Exception:
        redis_client = None

    if redis_client is not None:
        try:
            key = f"robust_alerts:rl:{condition}"
            ok = await redis_client.set(
                key, "1", ex=_DIVERGENCE_RATE_LIMIT_SECONDS, nx=True,
            )
            return bool(ok)
        except Exception as exc:
            logger.debug("[shadow] redis rate-limit failed: %s", exc)

    now = time.time()
    last = _LOCAL_DIVERGENCE_RL.get(condition, 0.0)
    if now - last < _DIVERGENCE_RATE_LIMIT_SECONDS:
        return False
    _LOCAL_DIVERGENCE_RL[condition] = now
    return True


async def _send_ops_alert(message: str) -> None:
    """Send a divergence alert to the single ops Slack webhook.

    Cross-tenant safety: this NEVER iterates ``NotificationSetting`` rows
    or per-user webhooks — the divergence payload contains operator-only
    information (per-tenant symbol + score values) and must not leak into
    other tenants' Slack workspaces. When no ops webhook is configured the
    alert is logged at INFO and dropped.
    """
    url = _ops_webhook_url()
    if not url:
        logger.info("[shadow] ops alert (no webhook configured): %s", message)
        return
    try:
        from ..notification_service import notification_service
        await notification_service._send_slack(url, message)
    except Exception as exc:
        logger.debug("[shadow] ops slack send failed: %s", exc)


def _legacy_score(asset: Mapping[str, Any]) -> Optional[float]:
    """Return the legacy comparable score for divergence calculation.

    Spot watchlists set ``alpha_score`` / ``score``; the futures pipeline
    sets ``confidence_score`` (= ``max(score_long, score_short)``) and
    exposes per-direction scores too. We try them in priority order so
    futures-mode watchlists also produce a divergence bucket instead of
    silently emitting ``unknown``.
    """
    for key in ("alpha_score", "score", "confidence_score"):
        v = asset.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    # Final fall-back: max of futures dual scores when present.
    long_s = asset.get("score_long")
    short_s = asset.get("score_short")
    candidates = [s for s in (long_s, short_s) if s is not None]
    if candidates:
        try:
            return float(max(candidates))
        except (TypeError, ValueError):
            return None
    return None


def _candle_fallback_keys(indicators: Mapping[str, Any], indicators_config: Mapping[str, Any]) -> set[str]:
    """Identify keys whose value came from the candle approximation rather
    than a real flow source.
    """
    keys: set[str] = set()
    if "taker_source" in indicators or "buy_pressure" in indicators:
        return keys
    if indicators.get("taker_ratio") is not None and indicators_config.get("taker_ratio", {}).get(
        "allow_candle_fallback", False
    ):
        keys.add("taker_ratio")
    if indicators.get("volume_delta") is not None and indicators_config.get("volume_delta", {}).get(
        "allow_candle_fallback", False
    ):
        keys.add("volume_delta")
    return keys


def _coerce_timestamp(raw: Any, default: datetime) -> datetime:
    if raw is None:
        return default
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return default
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return default
    return default


def _build_per_indicator_timestamps(
    asset: Mapping[str, Any],
    indicators: Mapping[str, Any],
    *,
    default: datetime,
) -> Mapping[str, datetime]:
    """Best-effort per-indicator timestamps so envelope staleness reflects
    real source freshness, not the wall-clock at the moment of the shadow
    pass.

    Sources searched (in priority order):
      * ``indicators['_timestamps']`` — explicit per-key dict if upstream
        attached it (Phase 2 path).
      * ``indicators['<name>_timestamp']`` — per-key sibling field.
      * ``indicators['taker_window_end']`` — flow window end for
        flow-family indicators.
      * ``asset['indicator_refreshed_at']`` / ``asset['refreshed_at']`` —
        per-asset refresh timestamps populated by the legacy pipeline.

    Falls back to ``default`` when no signal is available.
    """
    explicit = indicators.get("_timestamps") if isinstance(indicators, Mapping) else None
    explicit_map: dict[str, datetime] = {}
    if isinstance(explicit, Mapping):
        for k, v in explicit.items():
            explicit_map[str(k)] = _coerce_timestamp(v, default)

    flow_ts = _coerce_timestamp(indicators.get("taker_window_end"), default)
    asset_ts = _coerce_timestamp(
        asset.get("indicator_refreshed_at") or asset.get("refreshed_at"),
        default,
    )

    flow_indicators = {
        "taker_ratio", "buy_pressure", "volume_delta",
        "taker_buy_volume", "taker_sell_volume",
    }

    out: dict[str, datetime] = {}
    for name in indicators.keys():
        if not isinstance(name, str) or name.startswith("_"):
            continue
        if name in explicit_map:
            out[name] = explicit_map[name]
            continue
        sibling = indicators.get(f"{name}_timestamp")
        if sibling is not None:
            out[name] = _coerce_timestamp(sibling, default)
            continue
        if name in flow_indicators and flow_ts is not default:
            out[name] = flow_ts
            continue
        out[name] = asset_ts
    return out


async def run_shadow_scan(
    db: AsyncSession,
    *,
    assets: Iterable[Mapping[str, Any]],
    score_config: Optional[Mapping[str, Any]] = None,
    indicators_config: Optional[Mapping[str, Any]] = None,
    user_id: Optional[uuid.UUID] = None,
    watchlist_id: Optional[uuid.UUID] = None,
    watchlist_level: Optional[str] = None,
    market_mode: Optional[str] = None,
) -> int:
    """Run the robust pipeline in shadow mode.

    Returns the number of snapshots successfully persisted. Never raises.
    """
    if not is_shadow_enabled():
        return 0

    score_config = dict(score_config or {})
    indicators_config = dict(indicators_config or {})
    rules = score_config.get("scoring_rules") or score_config.get("rules") or []
    threshold = float(
        (score_config.get("thresholds") or {}).get("buy", 65.0)
    )

    written = 0
    now = datetime.now(timezone.utc)

    for asset in assets:
        try:
            symbol = asset.get("symbol")
            if not symbol:
                continue
            inds = asset.get("indicators") or {}
            if not inds:
                continue

            fallback_keys = _candle_fallback_keys(inds, indicators_config)
            per_ts = _build_per_indicator_timestamps(asset, inds, default=now)
            envelopes = envelope_indicators(
                symbol,
                inds,
                timestamp=now,
                source_timestamps=per_ts,
                flow_source_hint=inds.get("taker_source"),
                candle_fallback_keys=fallback_keys,
            )
            if not envelopes:
                continue

            validation = validate_indicator_integrity(envelopes)
            score = calculate_score_with_confidence(
                envelopes,
                rules,
                can_trade_threshold=threshold,
            )

            legacy = _legacy_score(asset)
            bucket = divergence_bucket(legacy, score.score) if legacy is not None else "unknown"
            if legacy is not None:
                increment_divergence(bucket)
                if bucket == ">10%":
                    logger.info(
                        "[shadow] divergence %s legacy=%.3f robust=%.3f bucket=%s mode=%s",
                        symbol, legacy, score.score, bucket, market_mode or "spot",
                    )
                    # Per-event divergence Slack alert routed to a single
                    # ops-only webhook — never per-user broadcast.
                    try:
                        if await _divergence_rate_limit_open("divergence_event"):
                            base = max(abs(float(legacy)), 1e-9)
                            diff_pct = abs(float(legacy) - float(score.score)) / base * 100.0
                            msg = (
                                ":warning: *Robust indicators* — divergence event "
                                f"`{symbol}` legacy={float(legacy):.2f} "
                                f"robust={score.score:.2f} "
                                f"(Δ={diff_pct:.1f}%, bucket={bucket}, "
                                f"mode={market_mode or 'spot'})."
                            )
                            await _send_ops_alert(msg)
                    except Exception as _alert_exc:
                        logger.debug(
                            "[shadow] divergence alert dispatch failed: %s",
                            _alert_exc,
                        )

            if score.rejected and score.rejection_reason:
                reason_label = score.rejection_reason.split(":", 1)[0]
                increment_rejection(reason_label)

            set_indicator_confidence(symbol, score.global_confidence)
            for name, env in envelopes.items():
                set_indicator_staleness(symbol, name, env.staleness_seconds)

            inserted = await persist_snapshot(
                db,
                symbol=symbol,
                envelopes=envelopes,
                validation=validation,
                score=score,
                legacy_score=legacy,
                divergence_bucket=bucket,
                user_id=user_id,
                watchlist_id=watchlist_id,
                timestamp=now,
            )
            if inserted is not None:
                written += 1
        except Exception as exc:
            logger.debug(
                "[shadow] error processing %s: %s",
                asset.get("symbol", "?"), exc,
            )
            continue

    if written:
        logger.info(
            "[shadow] persisted %d snapshots (level=%s, mode=%s, watchlist=%s)",
            written, watchlist_level, market_mode or "spot", watchlist_id,
        )
    return written


__all__ = ["is_shadow_enabled", "run_shadow_scan"]
