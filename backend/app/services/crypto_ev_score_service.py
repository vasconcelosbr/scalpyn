from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config_service import config_service
from .crypto_ev_config import default_crypto_ev_config

logger = logging.getLogger(__name__)

CRYPTO_EV_SOURCE = "L1_SPECTRUM"
CRYPTO_EV_OUTCOMES = ("TP_HIT", "SL_HIT", "TIMEOUT")


@dataclass(frozen=True)
class CryptoEVTrade:
    shadow_trade_id: str
    symbol: str
    created_at: Any
    net_return_decimal: Optional[float]
    atr_pct: Optional[float]
    would_pass_l3: Optional[bool]
    replay_status: str
    l3_config_version: str


def stable_config_version(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def clamp_score(raw: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, raw))


def normalize_ev_to_score(ev_decimal: float, config: Mapping[str, Any]) -> float:
    norm = config.get("score_normalization") or {}
    if norm.get("method") != "linear_clamp":
        raise ValueError("crypto_ev score_normalization.method must be linear_clamp")
    ev0 = float(norm["ev_at_score_0"])
    ev100 = float(norm["ev_at_score_100"])
    if ev100 <= ev0:
        raise ValueError("crypto_ev score_normalization requires ev_at_score_100 > ev_at_score_0")
    return clamp_score((ev_decimal - ev0) / (ev100 - ev0) * 100.0)


def resolve_atr_bucket(atr_pct: Optional[float], buckets: Iterable[Mapping[str, Any]]) -> str:
    for bucket in buckets:
        max_value = bucket.get("atr_pct_max")
        if max_value is None:
            return str(bucket["name"])
        if atr_pct is not None and atr_pct <= float(max_value):
            return str(bucket["name"])
    return "UNKNOWN"


def shrink_ev(ev_symbol: Optional[float], ev_prior: float, n: int, k: int) -> tuple[float, float]:
    if n <= 0 or ev_symbol is None:
        return 0.0, ev_prior
    w = n / (n + k)
    return w, (w * ev_symbol) + ((1.0 - w) * ev_prior)


def normalize_crypto_ev_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    merged = default_crypto_ev_config()
    if config:
        raw_config = dict(config)
        legacy_without_window_policy = "window_policy" not in raw_config
        merged.update(raw_config)
        if legacy_without_window_policy:
            views = dict(merged.get("views") or {})
            if views.get("operational_view") == "executable":
                views["operational_view"] = "spectrum"
                merged["views"] = views
    return merged


def resolve_crypto_ev_window(
    config: Mapping[str, Any],
    now: Optional[datetime] = None,
) -> tuple[str, datetime, datetime, int]:
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    policy = str(config.get("window_policy") or "current_month_to_date")
    if policy == "current_month_to_date":
        window_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif policy == "rolling_hours":
        window_start = now_utc - timedelta(hours=int(config["window_hours"]))
    else:
        raise ValueError(f"unsupported crypto_ev window_policy: {policy}")

    window_hours = max(1, math.ceil((now_utc - window_start).total_seconds() / 3600.0))
    return policy, window_start, now_utc, window_hours


def resolve_state(score: float, n: int, previous_state: Optional[str], config: Mapping[str, Any]) -> str:
    min_n = int(config["min_trades_for_state"])
    if n < min_n:
        return "INSUFFICIENT_DATA"
    states = config["states"]
    prev = previous_state or "NEUTRAL"
    if prev == "FAVORABLE":
        return "FAVORABLE" if score >= float(states["favorable_exit"]) else "NEUTRAL"
    if prev == "RISKY":
        return "RISKY" if score <= float(states["risky_exit"]) else "NEUTRAL"
    if prev == "AVOID":
        return "AVOID" if score <= float(states["avoid_exit"]) else "NEUTRAL"
    if score >= float(states["favorable_enter"]):
        return "FAVORABLE"
    if score <= float(states["avoid_enter"]):
        return "AVOID"
    if score <= float(states["risky_enter"]):
        return "RISKY"
    return "NEUTRAL"


def replay_l3_filters_from_snapshot(features_snapshot: Mapping[str, Any], l3_config: Mapping[str, Any]) -> tuple[Optional[bool], str]:
    """Pure fail-closed L3 replay.

    Current L1 snapshots do not persist profile/L3 filter context. Returning
    false with a stable reason is safer than inventing executable eligibility.
    """
    del l3_config
    required_context = ("profile_id", "profile_passed", "l3_passed")
    if not any(key in features_snapshot for key in required_context):
        return None, "missing_l3_snapshot_context"
    return None, "unsupported_l3_replay_context"


class CryptoEVScoreService:
    async def compute_for_all_configured_users(self, db: AsyncSession) -> Dict[str, Any]:
        rows = (await db.execute(text("""
            SELECT DISTINCT user_id
              FROM config_profiles
             WHERE config_type = 'crypto_ev'
               AND is_active = true
        """))).mappings().all()
        summaries = []
        for row in rows:
            summaries.append(await self.compute_for_user(db, row["user_id"]))
        return {"users": len(summaries), "summaries": summaries}

    async def compute_for_user(self, db: AsyncSession, user_id: UUID) -> Dict[str, Any]:
        config = await config_service.get_config(db, "crypto_ev", user_id)
        config = normalize_crypto_ev_config(config)
        if not bool(config.get("enabled")):
            return {"status": "disabled", "user_id": str(user_id)}

        self._validate_config(config)
        window_policy, window_start, window_end, effective_window_hours = resolve_crypto_ev_window(config)
        config_version = stable_config_version(config)
        ml_config = await self._load_ml_config(db)
        fee_pct = self._load_fee_pct(config, ml_config)
        l3_config_version = await self._l3_config_version(db, user_id)

        await self._materialize_fail_closed_l3_flags(db, window_start, window_end, l3_config_version)
        trades = await self._load_trades(db, window_start, window_end, fee_pct)
        previous = await self._load_previous_states(db)
        ml_health = await self.ml_component_health(db, config)
        snapshots = self._build_snapshots(
            trades=trades,
            config=config,
            config_version=config_version,
            previous_states=previous,
            ml_health=ml_health,
            window_policy=window_policy,
            window_start=window_start,
            window_end=window_end,
            effective_window_hours=effective_window_hours,
        )
        await self._insert_snapshots(db, snapshots)
        await db.commit()
        return {
            "status": "ok",
            "user_id": str(user_id),
            "source": CRYPTO_EV_SOURCE,
            "trades_loaded": len(trades),
            "snapshots_inserted": len(snapshots),
            "config_version": config_version,
            "ml_component_health": ml_health,
            "l3_config_version": l3_config_version,
            "window_policy": window_policy,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    async def ml_component_health(self, db: AsyncSession, config: Mapping[str, Any]) -> Dict[str, Any]:
        ml_component = config.get("ml_component") or {}
        health_gate = ml_component.get("health_gate") or {}
        if not bool(ml_component.get("user_enabled")) or float(ml_component.get("weight_pct") or 0) <= 0:
            return {"healthy": False, "reason": "ml_component_disabled_by_config"}

        require_status = str(health_gate.get("require_status") or "promoted")
        min_auc = float(health_gate.get("min_oos_auc") or 0)
        row = (await db.execute(text("""
            SELECT version::text AS version,
                   status,
                   COALESCE(test_roc_auc, roc_auc) AS oos_auc,
                   metrics_json
              FROM ml_models
             WHERE status = :status
             ORDER BY activated_at DESC NULLS LAST, created_at DESC NULLS LAST
             LIMIT 1
        """), {"status": require_status})).mappings().first()
        if not row:
            return {"healthy": False, "reason": f"no_model_with_status:{require_status}"}
        auc = row["oos_auc"]
        if auc is None or float(auc) < min_auc:
            return {
                "healthy": False,
                "reason": f"oos_auc_below_gate:{auc}<{min_auc}",
                "model_version": row["version"],
            }
        if bool(health_gate.get("require_canary_passed")):
            metrics = row["metrics_json"] or {}
            if not bool(metrics.get("canary_passed")):
                return {
                    "healthy": False,
                    "reason": "canary_not_passed",
                    "model_version": row["version"],
                }
        return {"healthy": False, "reason": "ml_symbol_component_unavailable", "model_version": row["version"]}

    def _validate_config(self, config: Mapping[str, Any]) -> None:
        required = [
            "window_policy",
            "window_hours",
            "shrinkage_k",
            "min_trades_for_state",
            "max_unreplayable_ratio",
            "fee_roundtrip_pct_source",
            "atr_buckets",
            "score_normalization",
            "states",
            "views",
            "ml_component",
        ]
        missing = [key for key in required if key not in config]
        if missing:
            raise ValueError(f"crypto_ev config missing required keys: {missing}")
        if str(config["window_policy"]) not in {"current_month_to_date", "rolling_hours"}:
            raise ValueError(f"unsupported crypto_ev window_policy: {config['window_policy']}")

    def _load_fee_pct(self, config: Mapping[str, Any], ml_config: Mapping[str, Any]) -> float:
        fee_key = str(config["fee_roundtrip_pct_source"])
        if fee_key not in ml_config:
            raise ValueError(f"crypto_ev fee source missing from ml config: {fee_key}")
        return float(ml_config[fee_key])

    async def _load_ml_config(self, db: AsyncSession) -> Dict[str, Any]:
        row = (await db.execute(text("""
            SELECT config_json
              FROM config_profiles
             WHERE config_type = 'ml'
               AND is_active = true
             ORDER BY updated_at DESC NULLS LAST
             LIMIT 1
        """))).mappings().first()
        if not row:
            raise ValueError("config_profiles.ml missing")
        cfg = dict(row["config_json"] or {})
        if isinstance(cfg.get("ml_dataset_valid_from"), str):
            cfg["ml_dataset_valid_from"] = datetime.fromisoformat(
                cfg["ml_dataset_valid_from"].replace("Z", "+00:00")
            )
        return cfg

    async def _l3_config_version(self, db: AsyncSession, user_id: UUID) -> str:
        rows = (await db.execute(text("""
            SELECT id::text, name, level, updated_at
              FROM pipeline_watchlists
             WHERE user_id = CAST(:uid AS uuid)
               AND UPPER(COALESCE(level, '')) = 'L3'
             ORDER BY id::text
        """), {"uid": str(user_id)})).mappings().all()
        payload = [dict(row) for row in rows]
        return stable_config_version({"l3_watchlists": payload})

    async def _materialize_fail_closed_l3_flags(
        self,
        db: AsyncSession,
        window_start: datetime,
        window_end: datetime,
        l3_config_version: str,
    ) -> None:
        await db.execute(text("""
            INSERT INTO crypto_ev_l3_replay_flags (
                shadow_trade_id, would_pass_l3, replay_status, l3_config_version, replay_reason, replay_details
            )
            SELECT st.id,
                   NULL,
                   'UNREPLAYABLE',
                   :l3_config_version,
                   'missing_l3_snapshot_context',
                   jsonb_build_object(
                       'source', st.source,
                       'reason', 'L1 features_snapshot has no persisted profile/L3 filter context'
                   )
             FROM shadow_trades st
             WHERE st.source = :source
               AND st.created_at >= CAST(:window_start AS timestamptz)
               AND st.created_at <= CAST(:window_end AS timestamptz)
               AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
               AND NOT EXISTS (
                   SELECT 1 FROM crypto_ev_l3_replay_flags f
                    WHERE f.shadow_trade_id = st.id
               )
        """), {
            "l3_config_version": l3_config_version,
            "source": CRYPTO_EV_SOURCE,
            "window_start": window_start,
            "window_end": window_end,
        })

    async def _load_trades(
        self,
        db: AsyncSession,
        window_start: datetime,
        window_end: datetime,
        fee_pct: float,
    ) -> List[CryptoEVTrade]:
        rows = (await db.execute(text("""
            SELECT st.id::text AS shadow_trade_id,
                   st.symbol,
                   st.created_at,
                   st.net_return_pct,
                   st.pnl_pct,
                   st.atr_pct_at_entry,
                   st.features_snapshot,
                   f.would_pass_l3,
                   COALESCE(f.replay_status, 'UNREPLAYABLE') AS replay_status,
                   f.l3_config_version
              FROM shadow_trades st
              LEFT JOIN crypto_ev_l3_replay_flags f ON f.shadow_trade_id = st.id
             WHERE st.source = :source
               AND st.created_at >= CAST(:window_start AS timestamptz)
               AND st.created_at <= CAST(:window_end AS timestamptz)
               AND st.outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
             ORDER BY st.symbol, st.created_at
        """), {
            "source": CRYPTO_EV_SOURCE,
            "window_start": window_start,
            "window_end": window_end,
        })).mappings().all()

        trades: List[CryptoEVTrade] = []
        for row in rows:
            features = row["features_snapshot"] or {}
            net_return = row["net_return_pct"]
            if net_return is None and row["pnl_pct"] is not None:
                net_return = float(row["pnl_pct"]) - fee_pct
            atr_pct = self._extract_atr_pct(features, row["atr_pct_at_entry"])
            trades.append(
                CryptoEVTrade(
                    shadow_trade_id=row["shadow_trade_id"],
                    symbol=row["symbol"],
                    created_at=row["created_at"],
                    net_return_decimal=(float(net_return) / 100.0) if net_return is not None else None,
                    atr_pct=atr_pct,
                    would_pass_l3=row["would_pass_l3"] if row["replay_status"] != "UNREPLAYABLE" else None,
                    replay_status=row["replay_status"],
                    l3_config_version=row["l3_config_version"] or "",
                )
            )
        return trades

    def _extract_atr_pct(self, features: Mapping[str, Any], fallback: Any) -> Optional[float]:
        for key in ("atr_pct", "atr_percent"):
            value = features.get(key) if isinstance(features, Mapping) else None
            if isinstance(value, Mapping):
                value = value.get("value")
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        if fallback is not None:
            try:
                return float(fallback)
            except (TypeError, ValueError):
                return None
        return None

    async def _load_previous_states(self, db: AsyncSession) -> Dict[tuple[str, str], str]:
        rows = (await db.execute(text("""
            SELECT symbol, view, state
              FROM crypto_ev_current
        """))).mappings().all()
        return {(row["symbol"], row["view"]): row["state"] for row in rows}

    def _build_snapshots(
        self,
        *,
        trades: List[CryptoEVTrade],
        config: Mapping[str, Any],
        config_version: str,
        previous_states: Mapping[tuple[str, str], str],
        ml_health: Mapping[str, Any],
        window_policy: str,
        window_start: datetime,
        window_end: datetime,
        effective_window_hours: int,
    ) -> List[Dict[str, Any]]:
        buckets = config["atr_buckets"]
        k = int(config["shrinkage_k"])
        window_hours = effective_window_hours
        max_unreplayable_ratio = float(config["max_unreplayable_ratio"])
        all_symbols = sorted({trade.symbol for trade in trades})
        snapshots: List[Dict[str, Any]] = []

        spectrum_trades = [trade for trade in trades if trade.net_return_decimal is not None]
        prior_by_bucket = self._prior_by_bucket(spectrum_trades, buckets)

        for view in ("spectrum", "executable"):
            view_trades = spectrum_trades if view == "spectrum" else [
                trade for trade in spectrum_trades if trade.replay_status == "PASSED" and trade.would_pass_l3 is True
            ]
            by_symbol: Dict[str, List[CryptoEVTrade]] = defaultdict(list)
            for trade in view_trades:
                by_symbol[trade.symbol].append(trade)
            unreplayable_by_symbol: Dict[str, int] = defaultdict(int)
            if view == "executable":
                for trade in spectrum_trades:
                    if trade.replay_status == "UNREPLAYABLE":
                        unreplayable_by_symbol[trade.symbol] += 1

            for symbol in all_symbols:
                symbol_trades = by_symbol.get(symbol, [])
                n_unreplayable = unreplayable_by_symbol.get(symbol, 0)
                latest_trade = symbol_trades[-1] if symbol_trades else next(
                    (trade for trade in reversed(spectrum_trades) if trade.symbol == symbol),
                    None,
                )
                bucket = resolve_atr_bucket(latest_trade.atr_pct if latest_trade else None, buckets)
                prior = prior_by_bucket.get(bucket, prior_by_bucket.get("__global__", 0.0))
                n = len(symbol_trades)
                ev_symbol = (
                    sum(trade.net_return_decimal for trade in symbol_trades if trade.net_return_decimal is not None) / n
                    if n
                    else None
                )
                w, ev_shrunk = shrink_ev(ev_symbol, prior, n, k)
                score = normalize_ev_to_score(ev_shrunk, config)
                state = resolve_state(score, n, previous_states.get((symbol, view)), config)
                unreplayable_denominator = n + n_unreplayable
                unreplayable_ratio = (
                    n_unreplayable / unreplayable_denominator
                    if view == "executable" and unreplayable_denominator > 0
                    else 0.0
                )
                if view == "executable" and unreplayable_ratio > max_unreplayable_ratio:
                    state = "INSUFFICIENT_DATA"
                snapshots.append({
                    "symbol": symbol,
                    "view": view,
                    "window_hours": window_hours,
                    "n_trades": n,
                    "n_excluded_no_pnl": 0,
                    "n_excluded_unreplayable": n_unreplayable if view == "executable" else 0,
                    "ev_symbol": ev_symbol,
                    "ev_prior": prior,
                    "atr_bucket": bucket,
                    "shrinkage_k": k,
                    "w": w,
                    "ev_shrunk": ev_shrunk,
                    "score": score,
                    "state": state,
                    "ml_component_applied": False,
                    "ml_component_value": None,
                    "ml_model_version": ml_health.get("model_version"),
                    "config_version": config_version,
                    "l3_config_version": latest_trade.l3_config_version if latest_trade else None,
                    "audit_json": {
                        "source": CRYPTO_EV_SOURCE,
                        "window_policy": window_policy,
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                        "ml_component_health": dict(ml_health),
                        "operational_view": (config.get("views") or {}).get("operational_view"),
                        "unreplayable_ratio": unreplayable_ratio,
                    },
                })
        return snapshots

    def _prior_by_bucket(
        self,
        trades: List[CryptoEVTrade],
        buckets: Iterable[Mapping[str, Any]],
    ) -> Dict[str, float]:
        grouped: Dict[str, List[float]] = defaultdict(list)
        for trade in trades:
            if trade.net_return_decimal is None:
                continue
            grouped[resolve_atr_bucket(trade.atr_pct, buckets)].append(trade.net_return_decimal)
            grouped["__global__"].append(trade.net_return_decimal)
        if "__global__" not in grouped:
            grouped["__global__"].append(0.0)
        global_prior = sum(grouped["__global__"]) / len(grouped["__global__"])
        priors = {"__global__": global_prior}
        for bucket in buckets:
            name = str(bucket["name"])
            values = grouped.get(name)
            priors[name] = (sum(values) / len(values)) if values else global_prior
        return priors

    async def _insert_snapshots(self, db: AsyncSession, snapshots: List[Dict[str, Any]]) -> None:
        if not snapshots:
            return
        await db.execute(text("""
            INSERT INTO crypto_ev_snapshots (
                symbol, view, window_hours, n_trades, n_excluded_no_pnl,
                n_excluded_unreplayable, ev_symbol, ev_prior, atr_bucket, shrinkage_k, w, ev_shrunk,
                score, state, ml_component_applied, ml_component_value,
                ml_model_version, config_version, l3_config_version, audit_json
            )
            VALUES (
                :symbol, :view, :window_hours, :n_trades, :n_excluded_no_pnl,
                :n_excluded_unreplayable, :ev_symbol, :ev_prior, :atr_bucket, :shrinkage_k, :w, :ev_shrunk,
                :score, :state, :ml_component_applied, :ml_component_value,
                :ml_model_version, :config_version, :l3_config_version,
                CAST(:audit_json AS jsonb)
            )
        """), [
            {**snapshot, "audit_json": json.dumps(snapshot["audit_json"], default=str)}
            for snapshot in snapshots
        ])


crypto_ev_score_service = CryptoEVScoreService()
