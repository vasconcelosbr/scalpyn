"""Global Profile Intelligence Auto-Pilot for Spot.

The service is intentionally transaction-oriented: profile, watchlist,
candidate metadata, association history, and audit rows are flushed together.
It never edits the config of an incumbent profile.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import re
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigProfile
from ..models.exchange_connection import ExchangeConnection
from ..models.pipeline_watchlist import PipelineWatchlist
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from ..models.profile_intelligence import (
    ProfileIndicatorStats,
    ProfileIntelligenceRun,
    ProfileRuleCombination,
    ProfileSuggestion,
)
from ..models.profile_intelligence_autopilot import (
    ProfileIntelligenceAutopilotAssociation,
    ProfileIntelligenceAutopilotAudit,
    ProfileIntelligenceAutopilotCandidate,
    ProfileIntelligenceAutopilotCompensation,
    ProfileIntelligenceAutopilotCycle,
    ProfileIntelligenceAutopilotReport,
    ProfileIntelligenceAutopilotSettings,
    ProfileIntelligenceLossFamily,
)
from .profile_create_service import (
    _build_profile_config,
    ensure_master_scoring_rules,
)


logger = logging.getLogger("scalpyn.services.profile_intelligence_autopilot")

SHADOW_STATES = {
    "SHADOW_COLLECTING",
    "SHADOW_READY",
    "PENDING_HUMAN_APPROVAL",
    "APPROVED_FOR_LIVE",
}
TERMINAL_STATES = {
    "REJECTED",
    "ROLLED_BACK",
    "EXPIRED",
    "BLOCKED",
    "DISABLED",
    "DUPLICATE_SKIPPED",
    "LOSS_FAMILY_COOLDOWN",
}

DEFAULT_AUTOPILOT_SETTINGS = {
    "cycle_hours": 24,
    "duplicate_relative_tolerance": 0.20,
    "loss_family_cooldown_hours": 60,
    "max_shadow_candidates": 30,
    "review_trade_target": 100,
    "review_min_trades": 50,
    "review_after_hours": 36,
    "promotion_min_win_rate": 0.55,
    "promotion_min_avg_pnl_pct": 0.005,
    "rollback_relative_floor": 0.80,
    "negative_rule_penalty": -10.0,
    "negative_score_max_impact": 30.0,
    "winner_rules_per_clone": 20,
    "loser_rules_per_clone": 20,
    "new_candidates_per_cycle": 30,
}

_ALIASES = {
    "rsi_14": "rsi",
    "relative_strength_index": "rsi",
    "adx_14": "adx",
    "average_directional_index": "adx",
    "atr_percent": "atr_pct",
    "macd_hist": "macd_histogram",
    "macd_histogram_pct": "macd_histogram",
    "volume_ratio": "volume_spike",
    "taker_buy_ratio": "taker_ratio",
}
_OPERATORS = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "eq": "=",
    "==": "=",
    "ne": "!=",
    ">": ">",
    ">=": ">=",
    "<": "<",
    "<=": "<=",
    "=": "=",
    "!=": "!=",
    "between": "between",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value


def _float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_indicator(value: Any) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return _ALIASES.get(name, name)


def normalize_operator(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _OPERATORS.get(raw, raw)


def _canonical_value(value: Any) -> Any:
    number = _float(value)
    if number is not None:
        return round(number, 10)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        return lowered
    return value


def _content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def canonicalize_rule(rule: dict, *, kind: str = "signal") -> dict:
    indicator = normalize_indicator(rule.get("field") or rule.get("indicator") or rule.get("item"))
    operator = normalize_operator(rule.get("operator"))
    direction = str(rule.get("direction") or rule.get("side") or "spot").strip().lower()
    canonical = {
        "kind": kind,
        "indicator": indicator,
        "operator": operator,
        "direction": direction,
    }
    if operator == "between":
        canonical["min"] = _canonical_value(rule.get("min"))
        canonical["max"] = _canonical_value(rule.get("max"))
    else:
        canonical["value"] = _canonical_value(rule.get("value"))
    if kind == "negative_score":
        canonical["points"] = _canonical_value(rule.get("points"))
    return canonical


def canonicalize_rules(rules: Iterable[dict]) -> list[dict]:
    normalized = [
        canonicalize_rule(rule, kind=rule.get("_kind") or rule.get("kind") or "signal")
        for rule in rules if isinstance(rule, dict)
    ]
    return sorted(
        normalized,
        key=lambda r: (
            r.get("kind", ""),
            r.get("indicator", ""),
            r.get("operator", ""),
            r.get("direction", ""),
            json.dumps(r, sort_keys=True, separators=(",", ":"), default=str),
        ),
    )


def extract_profile_rules(config: Optional[dict]) -> list[dict]:
    cfg = _json(config) or {}
    rules: list[dict] = []
    signals = cfg.get("signals") or cfg.get("entry_triggers") or {}
    signal_conditions = signals.get("conditions", []) if isinstance(signals, dict) else signals
    for rule in signal_conditions or []:
        if isinstance(rule, dict):
            rules.append({**rule, "_kind": "signal"})
    scoring = cfg.get("scoring") or {}
    for rule in scoring.get("generated_rules", []) or []:
        if isinstance(rule, dict) and (_float(rule.get("points")) or 0) < 0:
            rules.append({**rule, "_kind": "negative_score"})
    return canonicalize_rules(rules)


def canonical_signature(rules: Iterable[dict], *, context: str = "spot:l3") -> str:
    canonical = canonicalize_rules(rules)
    def family_value(value: Any) -> Any:
        number = _float(value)
        if number is None:
            return value
        if number == 0:
            return 0
        bucket = round(math.log(abs(number), 1.20))
        return {"sign": 1 if number > 0 else -1, "relative_bucket": bucket}

    family_rules = []
    for rule in canonical:
        item = {key: value for key, value in rule.items() if key != "points"}
        if item.get("operator") == "between":
            item["min"] = family_value(item.get("min"))
            item["max"] = family_value(item.get("max"))
        else:
            item["value"] = family_value(item.get("value"))
        family_rules.append(item)
    payload = {"context": context.lower(), "rules": family_rules}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _relative_equivalent(left: Any, right: Any, tolerance: float) -> bool:
    if left == right:
        return True
    a, b = _float(left), _float(right)
    if a is None or b is None:
        return str(left).lower() == str(right).lower()
    scale = max(abs(a), abs(b))
    if scale == 0:
        return abs(a - b) <= tolerance
    return abs(a - b) / scale <= tolerance


def semantic_rules_equivalent(left: Iterable[dict], right: Iterable[dict], tolerance: float = 0.20) -> bool:
    a = canonicalize_rules(left)
    b = canonicalize_rules(right)
    if len(a) != len(b):
        return False
    for one, two in zip(a, b):
        identity = ("kind", "indicator", "operator", "direction")
        if any(one.get(key) != two.get(key) for key in identity):
            return False
        if one.get("operator") == "between":
            if not _relative_equivalent(one.get("min"), two.get("min"), tolerance):
                return False
            if not _relative_equivalent(one.get("max"), two.get("max"), tolerance):
                return False
        elif not _relative_equivalent(one.get("value"), two.get("value"), tolerance):
            return False
    return True


def evaluation_ready(trades: int, elapsed_hours: float, settings: dict) -> bool:
    if trades >= int(settings["review_trade_target"]):
        return True
    return elapsed_hours >= float(settings["review_after_hours"]) and trades >= int(settings["review_min_trades"])


def promotion_decision(
    *,
    trades: int,
    elapsed_hours: float,
    win_rate: Optional[float],
    avg_pnl_pct: Optional[float],
    settings: dict,
    incumbent_exists: bool = False,
    incumbent_win_rate: Optional[float] = None,
    incumbent_avg_pnl_pct: Optional[float] = None,
) -> tuple[str, str]:
    if not evaluation_ready(trades, elapsed_hours, settings):
        return "COLLECT", "A amostra ainda não atingiu a janela mínima de avaliação."
    if trades < int(settings["review_min_trades"]):
        return "COLLECT", "Nenhuma decisão é permitida com menos do mínimo de trades."
    if win_rate is None or avg_pnl_pct is None:
        return "INSUFFICIENT_EVIDENCE", "Win Rate ou P&L médio ausente/inconsistente."
    if win_rate < float(settings["promotion_min_win_rate"]) or avg_pnl_pct < float(settings["promotion_min_avg_pnl_pct"]):
        return "REJECT", "O candidato não atingiu os mínimos obrigatórios de Win Rate e P&L."
    if incumbent_exists and (incumbent_win_rate is None or incumbent_avg_pnl_pct is None):
        return "INSUFFICIENT_EVIDENCE", "As métricas correspondentes do incumbent estão ausentes."
    if incumbent_exists:
        improves_one = win_rate > incumbent_win_rate or avg_pnl_pct > incumbent_avg_pnl_pct
        degrades_other = win_rate < incumbent_win_rate or avg_pnl_pct < incumbent_avg_pnl_pct
        if not improves_one or degrades_other:
            return "REJECT", "O candidato não supera uma métrica do incumbent sem degradar a outra."
    return "APPROVE", "A amostra e as métricas satisfazem todos os critérios de promoção."


def rollback_required(
    current_win_rate: Optional[float],
    current_avg_pnl_pct: Optional[float],
    promotion_win_rate: Optional[float],
    promotion_avg_pnl_pct: Optional[float],
    relative_floor: float,
) -> bool:
    values = (current_win_rate, current_avg_pnl_pct, promotion_win_rate, promotion_avg_pnl_pct)
    if any(value is None for value in values):
        return False
    win_floor = promotion_win_rate * relative_floor
    pnl_floor = promotion_avg_pnl_pct * relative_floor
    win_degraded = current_win_rate < win_floor and not math.isclose(current_win_rate, win_floor, rel_tol=1e-12, abs_tol=1e-12)
    pnl_degraded = current_avg_pnl_pct < pnl_floor and not math.isclose(current_avg_pnl_pct, pnl_floor, rel_tol=1e-12, abs_tol=1e-12)
    return bool(
        win_degraded or pnl_degraded
    )


def indicator_stat_to_condition(stat: ProfileIndicatorStats) -> Optional[dict]:
    indicator = normalize_indicator(stat.indicator)
    if not indicator:
        return None
    evidence = {
        "indicator_stat_id": str(stat.id),
        "total_cases": int(stat.total_cases or 0),
        "win_rate": _float(stat.win_rate),
        "avg_pnl_pct": _float(stat.avg_pnl_pct),
        "lift_vs_base": _float(stat.lift_vs_base),
        "confidence_level": stat.confidence_level,
        "bucket_label": stat.bucket_label,
    }
    minimum, maximum = _float(stat.range_min), _float(stat.range_max)
    if minimum is not None and maximum is not None:
        return {"field": indicator, "operator": "between", "min": minimum, "max": maximum, "required": True, "evidence": evidence}
    if minimum is not None:
        return {"field": indicator, "operator": ">=", "value": minimum, "required": True, "evidence": evidence}
    if maximum is not None:
        return {"field": indicator, "operator": "<", "value": maximum, "required": True, "evidence": evidence}
    value_text = str(stat.value_text or "").strip().lower()
    match = re.match(r"^(>=|<=|>|<|=|!=)\s*(-?\d+(?:\.\d+)?)$", value_text)
    if match:
        return {"field": indicator, "operator": match.group(1), "value": float(match.group(2)), "required": True, "evidence": evidence}
    if value_text in {"true", "false"}:
        return {"field": indicator, "operator": "=", "value": value_text == "true", "required": True, "evidence": evidence}
    return None


class SpotGateEvaluator:
    """Uses the existing Spot engine state/config/credentials; no parallel capital config."""

    async def evaluate(self, db: AsyncSession, user_id: UUID) -> tuple[bool, dict]:
        reasons: list[str] = []
        details: dict[str, Any] = {}

        running = False
        try:
            from ..engines.spot_scanner import get_engine
            scanner = get_engine(str(user_id))
            running = bool(scanner and scanner._running and not scanner._paused)
            if not running:
                from .redis_client import get_async_redis
                redis = await get_async_redis()
                running = bool(redis and await redis.exists(f"scalpyn:engine:spot:running:{user_id}"))
        except Exception as exc:
            details["spot_state_error"] = str(exc)
        if not running:
            reasons.append("SPOT_TRADING_DISABLED")

        config_row = (await db.execute(
            select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.config_type == "spot_engine",
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(1)
        )).scalars().first()
        config = _json(config_row.config_json) if config_row else {}
        details["spot_config_present"] = bool(config_row)
        if not config_row:
            reasons.append("SPOT_CONFIG_MISSING")
        config_text = json.dumps(config or {}).lower()
        if any(token in config_text for token in ('"risk_blocked": true', '"kill_switch_active": true', '"trading_blocked": true')):
            reasons.append("RISK_BLOCK_ACTIVE")

        connection = (await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.is_active.is_(True),
            ).order_by(ExchangeConnection.execution_priority, ExchangeConnection.created_at.desc()).limit(1)
        )).scalars().first()
        if not connection or not connection.api_key_encrypted or not connection.api_secret_encrypted:
            reasons.append("VALID_CREDENTIALS_MISSING")
        elif str(connection.connection_status or "").lower() not in {"connected", "active", "ok"}:
            reasons.append("CREDENTIALS_NOT_CONNECTED")

        available_usdt: Optional[float] = None
        if connection and not reasons:
            try:
                from ..exchange_adapters.gate_adapter import GateAdapter
                from ..utils.encryption import decrypt
                raw_key = bytes(connection.api_key_encrypted) if isinstance(connection.api_key_encrypted, memoryview) else connection.api_key_encrypted
                raw_secret = bytes(connection.api_secret_encrypted) if isinstance(connection.api_secret_encrypted, memoryview) else connection.api_secret_encrypted
                adapter = GateAdapter(decrypt(raw_key).strip(), decrypt(raw_secret).strip())
                balances = await adapter.get_spot_balance()
                available_usdt = next(
                    (float(item.get("available", 0)) for item in balances if item.get("currency") == "USDT"),
                    0.0,
                )
                minimum = _float((((config or {}).get("buying") or {}).get("capital_per_trade_min_usdt"))) or 0.0
                if available_usdt < minimum:
                    reasons.append("INSUFFICIENT_ALLOWED_BALANCE")
            except Exception as exc:
                reasons.append("BALANCE_OR_CREDENTIAL_CHECK_FAILED")
                details["balance_error"] = str(exc)
        details["available_usdt"] = available_usdt
        details["reasons"] = reasons
        return not reasons, details


class ProfileIntelligenceAutopilotService:
    def __init__(self, gate_evaluator: Optional[SpotGateEvaluator] = None):
        self.gate_evaluator = gate_evaluator or SpotGateEvaluator()

    async def get_settings(self, db: AsyncSession, user_id: UUID) -> tuple[ProfileIntelligenceAutopilotSettings, dict]:
        row = await db.get(ProfileIntelligenceAutopilotSettings, user_id)
        if row is None:
            row = ProfileIntelligenceAutopilotSettings(
                user_id=user_id,
                enabled=False,
                settings_json=DEFAULT_AUTOPILOT_SETTINGS.copy(),
            )
            db.add(row)
            await db.flush()
        settings = {**DEFAULT_AUTOPILOT_SETTINGS, **(_json(row.settings_json) or {})}
        return row, settings

    async def set_enabled(
        self,
        db: AsyncSession,
        user_id: UUID,
        enabled: bool,
        settings_update: Optional[dict] = None,
    ) -> dict:
        row, settings = await self.get_settings(db, user_id)
        if settings_update:
            settings.update({key: value for key, value in settings_update.items() if key in DEFAULT_AUTOPILOT_SETTINGS})
        now = utcnow()
        row.enabled = enabled
        row.settings_json = settings
        row.updated_at = now
        if enabled:
            row.enabled_at = now
        else:
            row.disabled_at = now
        await self._audit(
            db,
            user_id=user_id,
            actor_user_id=user_id,
            event_type="AUTOPILOT_ENABLED" if enabled else "AUTOPILOT_DISABLED",
            decision="ENABLED" if enabled else "DISABLED",
            reason="Alteração do controle global pelo proprietário da conta.",
            thresholds=settings,
        )
        await db.commit()
        return {"enabled": row.enabled, "settings": settings, "updated_at": row.updated_at.isoformat()}

    async def _candidate_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        candidate_id: UUID,
    ) -> Optional[ProfileIntelligenceAutopilotCandidate]:
        return await db.scalar(
            select(ProfileIntelligenceAutopilotCandidate).where(
                ProfileIntelligenceAutopilotCandidate.id == candidate_id,
                ProfileIntelligenceAutopilotCandidate.user_id == user_id,
            )
        )

    async def _latest_cycle_ref(self, db: AsyncSession, user_id: UUID):
        cycle = await db.scalar(
            select(ProfileIntelligenceAutopilotCycle).where(
                ProfileIntelligenceAutopilotCycle.user_id == user_id
            ).order_by(ProfileIntelligenceAutopilotCycle.started_at.desc()).limit(1)
        )
        return cycle or type("CycleRef", (), {"id": None})()

    async def _build_live_change_plan(
        self,
        db: AsyncSession,
        candidate: ProfileIntelligenceAutopilotCandidate,
    ) -> Optional[dict]:
        profile = await db.get(Profile, candidate.profile_id)
        target = (
            await db.get(PipelineWatchlist, candidate.target_watchlist_id)
            if candidate.target_watchlist_id
            else None
        )
        shadow = (
            await db.get(PipelineWatchlist, candidate.shadow_watchlist_id)
            if candidate.shadow_watchlist_id
            else None
        )
        live_watchlist = target or shadow
        if not profile or not live_watchlist:
            return None

        incumbent_id = live_watchlist.profile_id
        incumbent = await db.get(Profile, incumbent_id) if incumbent_id else None
        before = {
            "watchlist": {
                "id": str(live_watchlist.id),
                "profile_id": str(incumbent_id) if incumbent_id else None,
                "auto_refresh": bool(live_watchlist.auto_refresh),
            },
            "candidate_profile": {
                "id": str(profile.id),
                "is_active": bool(profile.is_active),
                "is_shadow_only": bool(profile.is_shadow_only),
                "live_trading_enabled": bool(profile.live_trading_enabled),
            },
            "incumbent_profile": {
                "id": str(incumbent.id) if incumbent else None,
                "is_active": bool(incumbent.is_active) if incumbent else None,
                "is_shadow_only": bool(incumbent.is_shadow_only) if incumbent else None,
                "live_trading_enabled": bool(incumbent.live_trading_enabled) if incumbent else None,
            },
            "shadow_watchlist": {
                "id": str(shadow.id) if shadow else None,
                "auto_refresh": bool(shadow.auto_refresh) if shadow else None,
            },
        }
        after = deepcopy(before)
        after["watchlist"]["profile_id"] = str(profile.id)
        after["watchlist"]["auto_refresh"] = True
        after["candidate_profile"].update({
            "is_active": True,
            "is_shadow_only": False,
            "live_trading_enabled": True,
        })
        if shadow and target and shadow.id != target.id:
            after["shadow_watchlist"]["auto_refresh"] = False
        diff = {
            key: {"before": before[key], "after": after[key]}
            for key in before
            if before[key] != after[key]
        }
        return {
            "profile": profile,
            "incumbent": incumbent,
            "live_watchlist": live_watchlist,
            "shadow_watchlist": shadow,
            "before_json": before,
            "after_json": after,
            "diff_json": diff,
            "rollback_payload": {
                "captured_at": utcnow().isoformat(),
                "watchlist_id": str(live_watchlist.id),
                "previous_profile_id": str(incumbent_id) if incumbent_id else None,
                "watchlist_auto_refresh": bool(live_watchlist.auto_refresh),
                "candidate_profile": before["candidate_profile"],
                "incumbent_profile": before["incumbent_profile"],
                "shadow_watchlist": before["shadow_watchlist"],
            },
        }

    def _promotion_audit_payload(
        self,
        candidate: ProfileIntelligenceAutopilotCandidate,
        *,
        before_json: Optional[dict] = None,
        after_json: Optional[dict] = None,
        diff_json: Optional[dict] = None,
        reason_code: str,
        mutation_applied: bool,
        rollback_payload: Optional[dict] = None,
    ) -> dict:
        evidence = _json(candidate.evidence_json) or {}
        recommendation = evidence.get("live_promotion_recommendation") or {}
        return {
            "candidate_id": str(candidate.id),
            "incumbent_profile_id": (
                str(candidate.previous_profile_id)
                if candidate.previous_profile_id
                else recommendation.get("incumbent_profile_id")
            ),
            "candidate_profile_id": str(candidate.profile_id),
            "before_json": before_json or {},
            "after_json": after_json or {},
            "diff_json": diff_json or {},
            "shadow_metrics": recommendation.get("shadow_metrics") or {
                "trades": candidate.observed_trades,
                "win_rate": _float(candidate.observed_win_rate),
                "avg_pnl_pct": _float(candidate.observed_avg_pnl_pct),
            },
            "comparison_metrics": recommendation.get("comparison_metrics") or {},
            "reason_code": reason_code,
            "approval_required": True,
            "approved_by": str(candidate.approved_by) if candidate.approved_by else None,
            "approved_at": (
                candidate.approved_at.isoformat()
                if candidate.approved_at
                else None
            ),
            "approval_reason": candidate.approval_reason,
            "rollback_payload": (
                rollback_payload
                if rollback_payload is not None
                else candidate.rollback_payload
            ),
            "mutation_applied": mutation_applied,
        }

    async def _block_candidate_action(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        candidate: ProfileIntelligenceAutopilotCandidate,
        actor_user_id: UUID,
        event_type: str,
        reason: str,
        claimed_actor_id: Optional[UUID] = None,
    ) -> dict:
        result = self._promotion_audit_payload(
            candidate,
            reason_code=reason,
            mutation_applied=False,
        )
        if claimed_actor_id is not None:
            result["claimed_actor_id"] = str(claimed_actor_id)
        await self._audit(
            db,
            user_id=user_id,
            actor_user_id=actor_user_id,
            candidate_id=candidate.id,
            profile_id=candidate.profile_id,
            watchlist_id=(
                candidate.target_watchlist_id
                or candidate.shadow_watchlist_id
            ),
            event_type=event_type,
            decision="BLOCKED",
            reason=reason,
            result=result,
        )
        await db.commit()
        return {"status": "blocked", "reason": reason}

    async def approve_candidate_for_live(
        self,
        db: AsyncSession,
        user_id: UUID,
        candidate_id: UUID,
        *,
        approved_by: UUID,
        approval_reason: str,
        approval_source: str,
        confirm_risk: bool,
    ) -> dict:
        candidate = await self._candidate_for_user(db, user_id, candidate_id)
        if not candidate:
            return {"status": "blocked", "reason": "candidate_not_found"}
        if approved_by != user_id:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                claimed_actor_id=approved_by,
                event_type="CANDIDATE_APPROVAL_BLOCKED",
                reason="approved_by_must_match_authenticated_user",
            )
        if not confirm_risk:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_APPROVAL_BLOCKED",
                reason="risk_confirmation_required",
            )
        if candidate.state != "PENDING_HUMAN_APPROVAL":
            reason_by_state = {
                "REJECTED": "candidate_rejected",
                "EXPIRED": "candidate_expired",
                "LIVE_ACTIVATED": "candidate_already_live",
                "ROLLED_BACK": "candidate_already_rolled_back",
            }
            reason = reason_by_state.get(
                candidate.state,
                "candidate_not_pending_human_approval",
            )
            result = await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_APPROVAL_BLOCKED",
                reason=reason,
            )
            result["state"] = candidate.state
            return result
        if not approval_reason.strip():
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_APPROVAL_BLOCKED",
                reason="approval_reason_required",
            )
        elapsed_hours = 0.0
        if isinstance(candidate.shadow_started_at, datetime):
            elapsed_hours = max(
                0.0,
                (utcnow() - candidate.shadow_started_at).total_seconds() / 3600.0,
            )
        if (
            candidate.observed_win_rate is None
            or candidate.observed_avg_pnl_pct is None
            or not evaluation_ready(
                int(candidate.observed_trades or 0),
                elapsed_hours,
                DEFAULT_AUTOPILOT_SETTINGS,
            )
        ):
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_APPROVAL_BLOCKED",
                reason="insufficient_shadow_metrics",
            )

        plan = await self._build_live_change_plan(db, candidate)
        if not plan or not plan.get("rollback_payload"):
            await self._audit(
                db,
                user_id=user_id,
                actor_user_id=approved_by,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                event_type="LIVE_ACTIVATION_BLOCKED_MISSING_ROLLBACK",
                decision="BLOCKED",
                reason="missing_rollback_payload",
                result=self._promotion_audit_payload(
                    candidate,
                    reason_code="missing_rollback_payload",
                    mutation_applied=False,
                    rollback_payload=None,
                ),
            )
            await db.commit()
            return {"status": "blocked", "reason": "missing_rollback_payload"}

        now = utcnow()
        snapshot = {
            "shadow_metrics": {
                "trades": candidate.observed_trades,
                "win_rate": _float(candidate.observed_win_rate),
                "avg_pnl_pct": _float(candidate.observed_avg_pnl_pct),
            },
            "evidence": _json(candidate.evidence_json) or {},
            "planned_change": {
                "before_json": plan["before_json"],
                "after_json": plan["after_json"],
                "diff_json": plan["diff_json"],
            },
        }
        candidate.state = "APPROVED_FOR_LIVE"
        candidate.approval_status = "approved"
        candidate.approval_required = True
        candidate.approved_by = approved_by
        candidate.approved_at = now
        candidate.approval_reason = approval_reason.strip()
        candidate.approval_source = approval_source
        candidate.approval_snapshot_json = snapshot
        candidate.rollback_payload = plan["rollback_payload"]
        candidate.promotion_blocked_reason = None
        candidate.updated_at = now
        await self._audit(
            db,
            user_id=user_id,
            actor_user_id=approved_by,
            candidate_id=candidate.id,
            profile_id=candidate.profile_id,
            watchlist_id=plan["live_watchlist"].id,
            event_type="CANDIDATE_APPROVED_FOR_LIVE",
            input_metrics=snapshot["shadow_metrics"],
            decision="APPROVED_FOR_LIVE",
            reason=candidate.approval_reason,
            result={
                **self._promotion_audit_payload(
                    candidate,
                    before_json=plan["before_json"],
                    after_json=plan["after_json"],
                    diff_json=plan["diff_json"],
                    reason_code="candidate_approved_for_live",
                    mutation_applied=False,
                ),
                "approval_required": True,
                "approved_by": str(approved_by),
                "approved_at": now.isoformat(),
                "approval_source": approval_source,
                "approval_snapshot": snapshot,
            },
        )
        await db.commit()
        return {
            "status": "approved",
            "state": candidate.state,
            "candidate_id": str(candidate.id),
            "live_activation_applied": False,
        }

    async def reject_candidate(
        self,
        db: AsyncSession,
        user_id: UUID,
        candidate_id: UUID,
        *,
        rejected_by: UUID,
        rejection_reason: str,
    ) -> dict:
        candidate = await self._candidate_for_user(db, user_id, candidate_id)
        if not candidate:
            return {"status": "blocked", "reason": "candidate_not_found"}
        if rejected_by != user_id:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                claimed_actor_id=rejected_by,
                event_type="CANDIDATE_REJECTION_BLOCKED",
                reason="rejected_by_must_match_authenticated_user",
            )
        if candidate.state in {"LIVE_ACTIVATED", "ROLLED_BACK"}:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_REJECTION_BLOCKED",
                reason="candidate_already_live_or_rolled_back",
            )
        if not rejection_reason.strip():
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_REJECTION_BLOCKED",
                reason="rejection_reason_required",
            )

        profile = await db.get(Profile, candidate.profile_id)
        shadow = (
            await db.get(PipelineWatchlist, candidate.shadow_watchlist_id)
            if candidate.shadow_watchlist_id
            else None
        )
        now = utcnow()
        candidate.state = "REJECTED"
        candidate.approval_status = "rejected"
        candidate.approval_required = True
        candidate.rejected_at = now
        candidate.updated_at = now
        candidate.decision_reason = rejection_reason.strip()
        candidate.promotion_blocked_reason = "candidate_rejected"
        if profile:
            profile.live_trading_enabled = False
            profile.is_shadow_only = True
        if shadow:
            shadow.auto_refresh = False
        await self._audit(
            db,
            user_id=user_id,
            actor_user_id=rejected_by,
            candidate_id=candidate.id,
            profile_id=candidate.profile_id,
            watchlist_id=candidate.shadow_watchlist_id,
            event_type="CANDIDATE_REJECTED",
            decision="REJECTED",
            reason=candidate.decision_reason,
            result={
                **self._promotion_audit_payload(
                    candidate,
                    reason_code="candidate_rejected",
                    mutation_applied=False,
                ),
            },
        )
        await db.commit()
        return {"status": "rejected", "state": candidate.state}

    async def activate_approved_candidate(
        self,
        db: AsyncSession,
        user_id: UUID,
        candidate_id: UUID,
        *,
        activated_by: UUID,
    ) -> dict:
        candidate = await self._candidate_for_user(db, user_id, candidate_id)
        if not candidate:
            return {"status": "blocked", "reason": "candidate_not_found"}
        candidate.live_activation_attempted_at = utcnow()
        if activated_by != user_id:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                claimed_actor_id=activated_by,
                event_type="LIVE_ACTIVATION_BLOCKED_ACTOR_MISMATCH",
                reason="activated_by_must_match_authenticated_user",
            )
        if candidate.state == "REJECTED":
            await self._audit(
                db,
                user_id=user_id,
                actor_user_id=activated_by,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                event_type="LIVE_ACTIVATION_BLOCKED_CANDIDATE_REJECTED",
                decision="BLOCKED",
                reason="candidate_rejected",
                result=self._promotion_audit_payload(
                    candidate,
                    reason_code="candidate_rejected",
                    mutation_applied=False,
                ),
            )
            await db.commit()
            return {"status": "blocked", "reason": "candidate_rejected"}
        if candidate.state in {"EXPIRED", "LIVE_ACTIVATED", "ROLLED_BACK"}:
            reason_by_state = {
                "EXPIRED": "candidate_expired",
                "LIVE_ACTIVATED": "candidate_already_live",
                "ROLLED_BACK": "candidate_already_rolled_back",
            }
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="LIVE_ACTIVATION_BLOCKED_INVALID_STATE",
                reason=reason_by_state[candidate.state],
            )
        if (
            candidate.state != "APPROVED_FOR_LIVE"
            or candidate.approval_status != "approved"
            or not candidate.approved_by
            or not candidate.approved_at
            or not candidate.approval_reason
        ):
            await self._audit(
                db,
                user_id=user_id,
                actor_user_id=activated_by,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                event_type="LIVE_ACTIVATION_BLOCKED_MISSING_APPROVAL",
                decision="BLOCKED",
                reason="missing_human_approval",
                result=self._promotion_audit_payload(
                    candidate,
                    reason_code="missing_human_approval",
                    mutation_applied=False,
                ),
            )
            await db.commit()
            return {"status": "blocked", "reason": "missing_human_approval"}
        if not candidate.rollback_payload:
            await self._audit(
                db,
                user_id=user_id,
                actor_user_id=activated_by,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                event_type="LIVE_ACTIVATION_BLOCKED_MISSING_ROLLBACK",
                decision="BLOCKED",
                reason="missing_rollback_payload",
                result=self._promotion_audit_payload(
                    candidate,
                    reason_code="missing_rollback_payload",
                    mutation_applied=False,
                    rollback_payload=None,
                ),
            )
            await db.commit()
            return {"status": "blocked", "reason": "missing_rollback_payload"}

        safe, gate_details = await self.gate_evaluator.evaluate(db, user_id)
        if not safe:
            await self._audit(
                db,
                user_id=user_id,
                actor_user_id=activated_by,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                event_type="LIVE_ACTIVATION_BLOCKED_OPERATIONAL_GATES",
                input_metrics={"gates": gate_details},
                decision="BLOCKED",
                reason="operational_gates_failed",
                result={
                    **self._promotion_audit_payload(
                        candidate,
                        reason_code="operational_gates_failed",
                        mutation_applied=False,
                    ),
                    "operational_gates": gate_details,
                },
            )
            await db.commit()
            return {
                "status": "blocked",
                "reason": "operational_gates_failed",
                "gates": gate_details,
            }

        plan = await self._build_live_change_plan(db, candidate)
        if not plan:
            await self._audit(
                db,
                user_id=user_id,
                actor_user_id=activated_by,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                event_type="LIVE_ACTIVATION_BLOCKED_TARGET_MISSING",
                decision="BLOCKED",
                reason="activation_target_missing",
                result=self._promotion_audit_payload(
                    candidate,
                    reason_code="activation_target_missing",
                    mutation_applied=False,
                ),
            )
            await db.commit()
            return {"status": "blocked", "reason": "activation_target_missing"}

        profile = plan["profile"]
        incumbent = plan["incumbent"]
        live_watchlist = plan["live_watchlist"]
        shadow = plan["shadow_watchlist"]
        previous_profile_id = live_watchlist.profile_id
        live_watchlist.profile_id = profile.id
        live_watchlist.auto_refresh = True
        if shadow and shadow.id != live_watchlist.id:
            shadow.auto_refresh = False
        profile.is_shadow_only = False
        profile.live_trading_enabled = True
        profile.is_active = True
        if incumbent and incumbent.id != profile.id:
            incumbent.live_trading_enabled = False

        now = utcnow()
        candidate.previous_profile_id = previous_profile_id
        candidate.target_watchlist_id = live_watchlist.id
        candidate.state = "LIVE_ACTIVATED"
        candidate.promotion_win_rate = candidate.observed_win_rate
        candidate.promotion_avg_pnl_pct = candidate.observed_avg_pnl_pct
        candidate.promoted_at = now
        candidate.live_activated_at = now
        candidate.updated_at = now
        candidate.decision_reason = "Ativação live executada após aprovação humana explícita."
        db.add(ProfileIntelligenceAutopilotAssociation(
            id=uuid4(),
            user_id=user_id,
            candidate_id=candidate.id,
            watchlist_id=live_watchlist.id,
            previous_profile_id=previous_profile_id,
            new_profile_id=profile.id,
            event_type="PROMOTION",
            is_active=True,
        ))
        await self._audit(
            db,
            user_id=user_id,
            actor_user_id=activated_by,
            candidate_id=candidate.id,
            profile_id=profile.id,
            profile_version=profile.profile_version,
            watchlist_id=live_watchlist.id,
            event_type="LIVE_ACTIVATED",
            input_metrics={
                "shadow_metrics": {
                    "trades": candidate.observed_trades,
                    "win_rate": _float(candidate.observed_win_rate),
                    "avg_pnl_pct": _float(candidate.observed_avg_pnl_pct),
                },
                "gates": gate_details,
            },
            decision="LIVE_ACTIVATED",
            reason=candidate.decision_reason,
            result={
                **self._promotion_audit_payload(
                    candidate,
                    before_json=plan["before_json"],
                    after_json=plan["after_json"],
                    diff_json=plan["diff_json"],
                    reason_code="human_approved_live_activation",
                    mutation_applied=True,
                ),
                "incumbent_profile_id": (
                    str(previous_profile_id) if previous_profile_id else None
                ),
                "candidate_profile_id": str(profile.id),
            },
        )
        await db.commit()
        return {
            "status": "live_activated",
            "state": candidate.state,
            "candidate_id": str(candidate.id),
        }

    async def rollback_candidate(
        self,
        db: AsyncSession,
        user_id: UUID,
        candidate_id: UUID,
        *,
        rolled_back_by: UUID,
    ) -> dict:
        candidate = await self._candidate_for_user(db, user_id, candidate_id)
        if not candidate:
            return {"status": "blocked", "reason": "candidate_not_found"}
        if rolled_back_by != user_id:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                claimed_actor_id=rolled_back_by,
                event_type="CANDIDATE_ROLLBACK_BLOCKED",
                reason="rolled_back_by_must_match_authenticated_user",
            )
        if candidate.state != "LIVE_ACTIVATED":
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_ROLLBACK_BLOCKED",
                reason="candidate_not_live",
            )
        if not candidate.rollback_payload:
            return await self._block_candidate_action(
                db,
                user_id=user_id,
                candidate=candidate,
                actor_user_id=user_id,
                event_type="CANDIDATE_ROLLBACK_BLOCKED",
                reason="missing_rollback_payload",
            )
        cycle = await self._latest_cycle_ref(db, user_id)
        _, settings = await self.get_settings(db, user_id)
        await self._rollback_candidate(
            db,
            user_id,
            cycle,
            candidate,
            settings,
            {"trigger": "human_requested", "actor": str(rolled_back_by)},
            actor_user_id=rolled_back_by,
        )
        await db.commit()
        return {"status": "rolled_back", "state": candidate.state}

    async def monitor_operational_state(self, db: AsyncSession, user_id: UUID) -> dict:
        """Frequent live degradation/gate monitor, independent from the 24h review."""
        if not await self._is_enabled(db, user_id):
            return {"status": "disabled"}
        lock_key = f"pi-autopilot:{user_id}"
        acquired = bool(await db.scalar(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": lock_key},
        ))
        if not acquired:
            return {"status": "duplicate"}
        try:
            _, settings = await self.get_settings(db, user_id)
            latest_cycle = await db.scalar(
                select(ProfileIntelligenceAutopilotCycle).where(
                    ProfileIntelligenceAutopilotCycle.user_id == user_id
                ).order_by(ProfileIntelligenceAutopilotCycle.started_at.desc()).limit(1)
            )
            cycle_ref = latest_cycle or type("CycleRef", (), {"id": None})()
            metrics = {"promoted": 0, "rolled_back": 0, "insufficient_evidence": 0}
            await self._monitor_live(db, user_id, cycle_ref, settings, metrics)
            await self._block_legacy_waiting_live(db, user_id, cycle_ref)
            await db.commit()
            return {"status": "completed", "metrics": metrics}
        finally:
            try:
                await db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": lock_key})
                await db.commit()
            except Exception:
                await db.rollback()

    async def _is_enabled(self, db: AsyncSession, user_id: UUID) -> bool:
        return bool(await db.scalar(
            select(ProfileIntelligenceAutopilotSettings.enabled).where(
                ProfileIntelligenceAutopilotSettings.user_id == user_id
            )
        ))

    async def run_cycle(
        self,
        db: AsyncSession,
        user_id: UUID,
        analysis_run_id: Optional[UUID] = None,
        force: bool = False,
    ) -> dict:
        lock_key = f"pi-autopilot:{user_id}"
        lock_acquired = bool(await db.scalar(text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": lock_key}))
        if not lock_acquired:
            await self._audit(
                db, user_id=user_id, event_type="CYCLE_DUPLICATE_SKIPPED",
                decision="DUPLICATE_SKIPPED", reason="Outro ciclo detém o lock por usuário.",
            )
            await db.commit()
            return {"status": "duplicate", "reason": "lock_not_acquired"}

        cycle: Optional[ProfileIntelligenceAutopilotCycle] = None
        try:
            settings_row, settings = await self.get_settings(db, user_id)
            if not settings_row.enabled and not force:
                return {"status": "disabled"}

            now = utcnow()
            cycle_hours = int(settings["cycle_hours"])
            window_epoch = int(now.timestamp()) // (cycle_hours * 3600) * (cycle_hours * 3600)
            window_start = datetime.fromtimestamp(window_epoch, tz=timezone.utc)
            idempotency_key = f"{user_id}:{window_start.isoformat()}:spot:l3"
            existing = await db.scalar(
                select(ProfileIntelligenceAutopilotCycle).where(
                    ProfileIntelligenceAutopilotCycle.idempotency_key == idempotency_key
                )
            )
            if existing and existing.status in {"COMPLETED", "STOPPED_DISABLED"} and not force:
                return {"status": "already_completed", "cycle_id": str(existing.id)}

            cycle = existing or ProfileIntelligenceAutopilotCycle(
                id=uuid4(),
                user_id=user_id,
                window_start=window_start,
                idempotency_key=idempotency_key,
                status="RUNNING",
                checkpoint="START",
                analysis_run_id=analysis_run_id,
                metrics_json={},
                errors_json=[],
            )
            if not existing:
                db.add(cycle)
            else:
                cycle.status = "RUNNING"
                cycle.started_at = now
                cycle.completed_at = None
                if analysis_run_id:
                    cycle.analysis_run_id = analysis_run_id
            await db.flush()
            await self._audit(db, user_id=user_id, cycle_id=cycle.id, event_type="CYCLE_STARTED", decision="RUNNING", thresholds=settings)
            await db.commit()

            if analysis_run_id is None:
                analysis_run_id = await db.scalar(
                    select(ProfileIntelligenceRun.id).where(
                        ProfileIntelligenceRun.user_id == user_id,
                        ProfileIntelligenceRun.status.in_(("completed", "completed_with_errors")),
                    ).order_by(ProfileIntelligenceRun.run_at.desc()).limit(1)
                )
                cycle.analysis_run_id = analysis_run_id

            metrics: dict[str, Any] = {
                "created": 0,
                "deduplicated": 0,
                "cooldown_blocked": 0,
                "rejected": 0,
                "promoted": 0,
                "waiting_live": 0,
                "rolled_back": 0,
                "disabled_for_capacity": 0,
                "insufficient_evidence": 0,
            }

            for checkpoint, operation in (
                ("MONITOR_LIVE", self._monitor_live),
                ("REVIEW_SHADOW", self._review_shadow),
                ("CALIBRATE_L3", self._calibrate_l3_profiles),
                ("CREATE_DISCOVERED", self._create_discovered_candidates),
                ("BLOCK_LEGACY_WAITING_LIVE", self._block_legacy_waiting_live),
            ):
                cycle.checkpoint = checkpoint
                await db.commit()
                if not force and not await self._is_enabled(db, user_id):
                    cycle.status = "STOPPED_DISABLED"
                    cycle.completed_at = utcnow()
                    await self._audit(
                        db, user_id=user_id, cycle_id=cycle.id, event_type="CYCLE_STOPPED_DISABLED",
                        decision="STOPPED", reason=f"Auto-Pilot desligado antes da etapa {checkpoint}.",
                        result={"checkpoint": checkpoint},
                    )
                    await db.commit()
                    return {"status": "stopped_disabled", "cycle_id": str(cycle.id), "metrics": metrics}
                try:
                    await operation(db, user_id, cycle, settings, metrics)
                    await db.commit()
                except Exception as exc:
                    logger.exception("[PIAutoPilot] phase %s failed for user=%s", checkpoint, user_id)
                    await db.rollback()
                    cycle = await db.get(ProfileIntelligenceAutopilotCycle, cycle.id)
                    errors = list(cycle.errors_json or [])
                    errors.append({"checkpoint": checkpoint, "error": str(exc), "at": utcnow().isoformat()})
                    cycle.errors_json = errors
                    await self._audit(
                        db, user_id=user_id, cycle_id=cycle.id, event_type="PARTIAL_FAILURE",
                        decision="COMPENSATION_REQUIRED", reason=str(exc), result={"checkpoint": checkpoint},
                    )
                    await db.commit()

            cycle.metrics_json = metrics
            cycle.status = "COMPLETED_WITH_ERRORS" if cycle.errors_json else "COMPLETED"
            cycle.checkpoint = "REPORT"
            cycle.completed_at = utcnow()
            settings_row = await db.get(ProfileIntelligenceAutopilotSettings, user_id)
            settings_row.last_cycle_at = cycle.completed_at
            settings_row.updated_at = cycle.completed_at
            report = await self._build_report(db, user_id, cycle, settings, metrics)
            db.add(ProfileIntelligenceAutopilotReport(
                id=uuid4(), user_id=user_id, cycle_id=cycle.id, report_json=report
            ))
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, event_type="CYCLE_COMPLETED",
                decision=cycle.status, thresholds=settings, result=metrics,
            )
            await db.commit()
            return {"status": cycle.status.lower(), "cycle_id": str(cycle.id), "metrics": metrics}
        finally:
            try:
                await db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": lock_key})
                await db.commit()
            except Exception:
                await db.rollback()

    async def _latest_indicator_stats(
        self, db: AsyncSession, user_id: UUID, run_id: Optional[UUID], role: str, limit: int
    ) -> list[ProfileIndicatorStats]:
        if run_id is None:
            return []
        return list((await db.execute(
            select(ProfileIndicatorStats).where(
                ProfileIndicatorStats.user_id == user_id,
                ProfileIndicatorStats.run_id == run_id,
                ProfileIndicatorStats.role_detected == role,
            ).order_by(ProfileIndicatorStats.lift_vs_base.desc().nullslast()).limit(limit)
        )).scalars().all())

    async def _calibrate_l3_profiles(self, db, user_id, cycle, settings, metrics):
        winners = await self._latest_indicator_stats(
            db, user_id, cycle.analysis_run_id, "winning_indicator", int(settings["winner_rules_per_clone"])
        )
        losers = await self._latest_indicator_stats(
            db, user_id, cycle.analysis_run_id, "losing_indicator", int(settings["loser_rules_per_clone"])
        )
        if not winners and not losers:
            return
        rows = (await db.execute(
            select(PipelineWatchlist, Profile).join(Profile, Profile.id == PipelineWatchlist.profile_id).where(
                PipelineWatchlist.user_id == user_id,
                func.upper(PipelineWatchlist.level) == "L3",
                func.lower(PipelineWatchlist.market_mode) == "spot",
                PipelineWatchlist.auto_refresh.is_(True),
                Profile.is_active.is_(True),
            )
        )).all()
        seen_profiles: set[UUID] = set()
        for watchlist, profile in rows:
            if profile.id in seen_profiles:
                continue
            seen_profiles.add(profile.id)
            if not await self._is_enabled(db, user_id):
                return
            await self._create_clone_candidate(db, user_id, cycle, settings, metrics, watchlist, profile, winners, losers)

    async def _create_clone_candidate(self, db, user_id, cycle, settings, metrics, target_watchlist, profile, winners, losers):
        config = deepcopy(_json(profile.config) or {})
        signals = config.setdefault("signals", {"logic": "AND", "conditions": []})
        conditions = signals.setdefault("conditions", [])
        winner_conditions = [condition for stat in winners if (condition := indicator_stat_to_condition(stat))]
        existing_keys = {
            json.dumps(canonicalize_rule(item), sort_keys=True, default=str)
            for item in conditions if isinstance(item, dict)
        }
        for condition in winner_conditions:
            key = json.dumps(canonicalize_rule(condition), sort_keys=True, default=str)
            if key not in existing_keys:
                conditions.append(condition)
                existing_keys.add(key)
        config["entry_triggers"] = deepcopy(signals)

        negative_requirements = []
        negative_rules = []
        for stat in losers:
            condition = indicator_stat_to_condition(stat)
            if not condition:
                continue
            rule = {
                "indicator": condition["field"],
                "operator": condition["operator"],
                "value": condition.get("value"),
                "min": condition.get("min"),
                "max": condition.get("max"),
                "points": float(settings["negative_rule_penalty"]),
                "category": "signal",
                "name": f"Auto-Pilot penalty: {stat.bucket_label}",
                "description": "Top Loser convertido em penalidade; não é Block Rule.",
                "evidence": condition["evidence"],
            }
            negative_requirements.append(rule)
        master = await ensure_master_scoring_rules(db, user_id, negative_requirements, create_missing=True)
        for rule in master["created"] + master["reused"]:
            if (_float(rule.get("points")) or 0) < 0:
                negative_rules.append(rule)
        scoring = config.setdefault("scoring", {})
        selected = list(dict.fromkeys([*(scoring.get("selected_rule_ids") or []), *master["selected_ids"]]))
        scoring["selected_rule_ids"] = selected
        generated = [item for item in (scoring.get("generated_rules") or []) if isinstance(item, dict)]
        by_id = {str(item.get("id")): item for item in generated}
        for rule in negative_rules:
            by_id[str(rule.get("id"))] = rule
        scoring["generated_rules"] = list(by_id.values())
        scoring["negative_score_max_impact"] = float(settings["negative_score_max_impact"])
        scoring["source"] = "profile_intelligence_autopilot"
        config.setdefault("metadata", {}).update({
            "generated_by": "profile_intelligence_autopilot",
            "origin_profile_id": str(profile.id),
            "previous_profile_version": profile.profile_version.isoformat() if profile.profile_version else None,
            "calibration_cycle_id": str(cycle.id),
            "spot_only": True,
        })
        version = int(await db.scalar(
            select(func.count(ProfileIntelligenceAutopilotCandidate.id)).where(
                ProfileIntelligenceAutopilotCandidate.origin_profile_id == profile.id
            )
        ) or 0) + 1
        evidence = {
            "top_winners": [condition["evidence"] for condition in winner_conditions],
            "top_losers": [rule["evidence"] for rule in negative_requirements],
            "origin_profile_version": profile.profile_version.isoformat() if profile.profile_version else None,
        }
        await self._create_candidate(
            db, user_id, cycle, settings, metrics,
            name=f"{profile.name} · Auto-Pilot v{version}",
            description=f"Clone versionado e calibrado automaticamente a partir de {profile.name}.",
            config=config,
            origin_profile_id=profile.id,
            previous_profile_id=profile.id,
            target_watchlist_id=target_watchlist.id,
            source_suggestion_id=None,
            source_combination_id=None,
            version_number=version,
            evidence=evidence,
        )

    async def _create_discovered_candidates(self, db, user_id, cycle, settings, metrics):
        if cycle.analysis_run_id is None:
            return
        limit = int(settings["new_candidates_per_cycle"])
        suggestions = list((await db.execute(
            select(ProfileSuggestion).where(
                ProfileSuggestion.user_id == user_id,
                ProfileSuggestion.run_id == cycle.analysis_run_id,
                ProfileSuggestion.status.in_(("pending_user_approval", "draft")),
            ).order_by(ProfileSuggestion.confidence_score.desc().nullslast()).limit(limit)
        )).scalars().all())
        created_sources = {
            row for row in (await db.execute(
                select(ProfileIntelligenceAutopilotCandidate.source_suggestion_id).where(
                    ProfileIntelligenceAutopilotCandidate.user_id == user_id,
                    ProfileIntelligenceAutopilotCandidate.source_suggestion_id.is_not(None),
                )
            )).scalars().all()
        }
        for suggestion in suggestions:
            if suggestion.id in created_sources or not await self._is_enabled(db, user_id):
                continue
            required = _json(suggestion.required_master_scoring_rules_json) or []
            master = await ensure_master_scoring_rules(db, user_id, required, create_missing=True)
            config = _build_profile_config(suggestion, master["selected_ids"], master["created"], "SHADOW_ONLY")
            config.setdefault("metadata", {}).update({
                "generated_by": "profile_intelligence_autopilot",
                "calibration_cycle_id": str(cycle.id),
                "spot_only": True,
            })
            await self._create_candidate(
                db, user_id, cycle, settings, metrics,
                name=suggestion.suggested_profile_name,
                description=suggestion.suggested_profile_description or "Candidato gerado pelo Auto-Pilot.",
                config=config,
                source_suggestion_id=suggestion.id,
                source_combination_id=suggestion.source_combination_id,
                evidence=_json(suggestion.evidence_summary_json) or {},
            )

        remaining = max(0, limit - len(suggestions))
        if remaining == 0:
            return
        combinations = list((await db.execute(
            select(ProfileRuleCombination).where(
                ProfileRuleCombination.user_id == user_id,
                ProfileRuleCombination.run_id == cycle.analysis_run_id,
            ).order_by(ProfileRuleCombination.champion_score.desc().nullslast()).limit(remaining)
        )).scalars().all())
        used_combinations = {
            row for row in (await db.execute(
                select(ProfileIntelligenceAutopilotCandidate.source_combination_id).where(
                    ProfileIntelligenceAutopilotCandidate.user_id == user_id,
                    ProfileIntelligenceAutopilotCandidate.source_combination_id.is_not(None),
                )
            )).scalars().all()
        }
        for combo in combinations:
            if combo.id in used_combinations or not await self._is_enabled(db, user_id):
                continue
            raw_rules = _json(combo.rules_json) or []
            signals = []
            for item in raw_rules:
                if not isinstance(item, dict):
                    continue
                field = item.get("field") or item.get("indicator") or item.get("item")
                if field:
                    signals.append({
                        "field": normalize_indicator(field),
                        "operator": normalize_operator(item.get("operator")),
                        "value": item.get("value"),
                        "min": item.get("min"),
                        "max": item.get("max"),
                        "required": True,
                    })
            config = {
                "signals": {"logic": "AND", "conditions": signals},
                "entry_triggers": {"logic": "AND", "conditions": deepcopy(signals)},
                "scoring": {
                    "selected_rule_ids": [],
                    "generated_rules": _json(combo.scoring_rules_json) or [],
                    "source": "profile_intelligence_autopilot",
                },
                "block_rules": {"blocks": []},
                "metadata": {
                    "generated_by": "profile_intelligence_autopilot",
                    "source_combination_id": str(combo.id),
                    "calibration_cycle_id": str(cycle.id),
                    "spot_only": True,
                },
            }
            await self._create_candidate(
                db, user_id, cycle, settings, metrics,
                name=combo.suggested_name or f"Auto-Pilot {combo.setup_family or 'Combination'}",
                description="Candidato Spot criado a partir de Combinação Descoberta.",
                config=config,
                source_combination_id=combo.id,
                evidence={
                    "discovery_metrics": _json(combo.discovery_metrics_json) or {},
                    "validation_metrics": _json(combo.validation_metrics_json) or {},
                    "total_cases": combo.total_cases,
                    "win_rate": _float(combo.win_rate),
                    "avg_pnl_pct": _float(combo.avg_pnl_pct),
                },
            )

    async def _create_candidate(
        self,
        db,
        user_id,
        cycle,
        settings,
        metrics,
        *,
        name,
        description,
        config,
        origin_profile_id=None,
        previous_profile_id=None,
        target_watchlist_id=None,
        source_suggestion_id=None,
        source_combination_id=None,
        version_number=1,
        evidence=None,
    ):
        canonical = extract_profile_rules(config)
        signature = canonical_signature(canonical)
        tolerance = float(settings["duplicate_relative_tolerance"])

        cooldown = await self._find_cooldown(db, user_id, canonical, tolerance)
        if cooldown:
            metrics["cooldown_blocked"] += 1
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, combination_id=source_combination_id,
                suggestion_id=source_suggestion_id, event_type="LOSS_FAMILY_COOLDOWN",
                decision="LOSS_FAMILY_COOLDOWN",
                reason="Família semântica reprovada ainda está no bloqueio.",
                result={"blocked_until": cooldown.blocked_until.isoformat(), "signature": signature},
            )
            return None

        duplicate = await self._find_duplicate(db, user_id, canonical, tolerance)
        if duplicate:
            metrics["deduplicated"] += 1
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, profile_id=duplicate.id,
                combination_id=source_combination_id, suggestion_id=source_suggestion_id,
                event_type="DUPLICATE_SKIPPED", decision="DUPLICATE_SKIPPED",
                reason="Profile semanticamente equivalente já existe no histórico.",
                result={"existing_profile_id": str(duplicate.id), "signature": signature},
            )
            return None

        await self._ensure_shadow_capacity(db, user_id, cycle, settings, metrics)
        l2 = await db.scalar(
            select(PipelineWatchlist).where(
                PipelineWatchlist.user_id == user_id,
                func.upper(PipelineWatchlist.level) == "L2",
                func.lower(PipelineWatchlist.market_mode) == "spot",
                PipelineWatchlist.auto_refresh.is_(True),
            ).order_by(PipelineWatchlist.updated_at.desc(), PipelineWatchlist.created_at.desc()).limit(1)
        )
        if not l2:
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, event_type="CANDIDATE_NOT_CREATED",
                decision="MISSING_L2_SOURCE", reason="Não existe Watchlist L2 Spot vigente para alimentar a watchlist candidata.",
            )
            return None

        now = utcnow()
        candidate_id = uuid4()
        profile = Profile(
            id=uuid4(),
            user_id=user_id,
            name=(name or "Auto-Pilot Candidate")[:255],
            description=description,
            is_active=True,
            config=config,
            profile_type="AUTOPILOT_CLONE" if origin_profile_id else "GENERATED",
            profile_version=now,
            generated_by="profile_intelligence_autopilot",
            generated_from_suggestion_id=source_suggestion_id,
            is_shadow_only=True,
            live_trading_enabled=False,
            auto_pilot_enabled=False,
            auto_pilot_config={},
        )
        db.add(profile)
        await db.flush()
        watchlist = PipelineWatchlist(
            id=uuid4(),
            user_id=user_id,
            name=f"AP · {profile.name}"[:100],
            level="L3",
            market_mode="spot",
            source_pool_id=None,
            source_watchlist_id=l2.id,
            profile_id=profile.id,
            auto_refresh=True,
            filters_json={
                "autopilot_candidate_id": str(candidate_id),
                "source_l2_watchlist_id": str(l2.id),
                "exclusive_profile_id": str(profile.id),
            },
        )
        db.add(watchlist)
        await db.flush()
        candidate_evidence = {
            **(evidence or {}),
            "profile_config_hash": _content_hash(config),
            "watchlist_snapshot": {
                "profile_id": str(profile.id),
                "source_watchlist_id": str(l2.id),
                "auto_refresh": True,
            },
            "reference_versions": [],
        }
        candidate = ProfileIntelligenceAutopilotCandidate(
            id=candidate_id,
            user_id=user_id,
            cycle_id=cycle.id,
            profile_id=profile.id,
            origin_profile_id=origin_profile_id,
            previous_profile_id=previous_profile_id,
            shadow_watchlist_id=watchlist.id,
            target_watchlist_id=target_watchlist_id,
            source_combination_id=source_combination_id,
            source_suggestion_id=source_suggestion_id,
            state="SHADOW_COLLECTING",
            canonical_signature=signature,
            canonical_rules_json=canonical,
            evidence_json=candidate_evidence,
            version_number=version_number,
            approval_status="pending",
            approval_required=True,
            shadow_started_at=now,
            review_after=now + timedelta(hours=float(settings["review_after_hours"])),
        )
        db.add(candidate)
        db.add(ProfileAuditLog(
            id=uuid4(),
            user_id=user_id,
            profile_id=profile.id,
            changed_by=user_id,
            change_source="profile_intelligence_autopilot",
            change_description="Clone/candidato versionado criado em Shadow; incumbent preservado.",
            previous_config=None,
            new_config=config,
            previous_profile_version=None,
            new_profile_version=now,
        ))
        await db.flush()
        await self._audit(
            db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
            profile_id=profile.id, profile_version=profile.profile_version,
            watchlist_id=watchlist.id, combination_id=source_combination_id,
            suggestion_id=source_suggestion_id, event_type="CANDIDATE_CREATED",
            input_metrics=evidence or {}, thresholds=settings, decision="SHADOW_COLLECTING",
            reason="Profile e watchlist L3 exclusiva criados atomicamente a partir da L2 vigente.",
            result={
                "candidate_id": str(candidate.id),
                "incumbent_profile_id": (
                    str(previous_profile_id) if previous_profile_id else None
                ),
                "candidate_profile_id": str(profile.id),
                "profile_id": str(profile.id),
                "watchlist_id": str(watchlist.id),
                "signature": signature,
                "before_json": {},
                "after_json": {"state": "SHADOW_COLLECTING"},
                "diff_json": {
                    "state": {
                        "before": None,
                        "after": "SHADOW_COLLECTING",
                    }
                },
                "shadow_metrics": {},
                "comparison_metrics": {},
                "reason_code": "candidate_created",
                "approval_required": True,
                "approved_by": None,
                "approved_at": None,
                "rollback_payload": None,
                "mutation_applied": False,
            },
        )
        await self._audit(
            db,
            user_id=user_id,
            cycle_id=cycle.id,
            candidate_id=candidate.id,
            profile_id=profile.id,
            profile_version=profile.profile_version,
            watchlist_id=watchlist.id,
            event_type="SHADOW_COLLECTING",
            decision="SHADOW_COLLECTING",
            reason="Coleta shadow iniciada para o candidato.",
            result={
                "candidate_id": str(candidate.id),
                "candidate_profile_id": str(profile.id),
                "approval_required": True,
                "mutation_applied": False,
            },
        )
        metrics["created"] += 1
        return candidate

    async def _find_duplicate(self, db, user_id, candidate_rules, tolerance):
        profiles = list((await db.execute(
            select(Profile).where(Profile.user_id == user_id)
        )).scalars().all())
        for profile in profiles:
            if semantic_rules_equivalent(candidate_rules, extract_profile_rules(profile.config), tolerance):
                return profile
        return None

    async def _find_cooldown(self, db, user_id, candidate_rules, tolerance):
        rows = list((await db.execute(
            select(ProfileIntelligenceLossFamily).where(
                ProfileIntelligenceLossFamily.user_id == user_id,
                ProfileIntelligenceLossFamily.blocked_until > utcnow(),
            )
        )).scalars().all())
        for row in rows:
            if semantic_rules_equivalent(candidate_rules, _json(row.canonical_rules_json) or [], tolerance):
                return row
        return None

    async def _ensure_shadow_capacity(self, db, user_id, cycle, settings, metrics):
        max_candidates = int(settings["max_shadow_candidates"])
        candidates = list((await db.execute(
            select(ProfileIntelligenceAutopilotCandidate).where(
                ProfileIntelligenceAutopilotCandidate.user_id == user_id,
                ProfileIntelligenceAutopilotCandidate.state.in_(SHADOW_STATES),
            ).order_by(
                ProfileIntelligenceAutopilotCandidate.observed_win_rate.asc().nullsfirst(),
                ProfileIntelligenceAutopilotCandidate.created_at.asc(),
            )
        )).scalars().all())
        if len(candidates) < max_candidates:
            return
        loser = candidates[0]
        profile = await db.get(Profile, loser.profile_id)
        watchlist = await db.get(PipelineWatchlist, loser.shadow_watchlist_id) if loser.shadow_watchlist_id else None
        loser.state = "DISABLED"
        loser.decision_reason = "Desativado para respeitar o limite global de candidatos Shadow."
        loser.updated_at = utcnow()
        if profile:
            profile.is_active = False
        if watchlist:
            watchlist.auto_refresh = False
        await self._audit(
            db, user_id=user_id, cycle_id=cycle.id, candidate_id=loser.id,
            profile_id=loser.profile_id, watchlist_id=loser.shadow_watchlist_id,
            event_type="SHADOW_CAPACITY_EVICTION", input_metrics={
                "observed_trades": loser.observed_trades,
                "observed_win_rate": _float(loser.observed_win_rate),
            }, thresholds={"max_shadow_candidates": max_candidates},
            decision="DISABLED", reason=loser.decision_reason,
        )
        metrics["disabled_for_capacity"] += 1

    async def _shadow_metrics(self, db, candidate) -> dict:
        row = (await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT'))::int AS trades,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT')::int AS wins,
                AVG(pnl_pct) FILTER (
                    WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT') AND pnl_pct IS NOT NULL
                ) / 100.0 AS avg_pnl_decimal
            FROM shadow_trades
            WHERE user_id = :uid
              AND profile_id = :pid
              AND created_at >= :started
        """), {
            "uid": str(candidate.user_id),
            "pid": str(candidate.profile_id),
            "started": candidate.shadow_started_at,
        })).one()
        trades = int(row.trades or 0)
        wins = int(row.wins or 0)
        return {
            "trades": trades,
            "wins": wins,
            "win_rate": wins / trades if trades else None,
            "avg_pnl_pct": _float(row.avg_pnl_decimal),
        }

    async def _incumbent_metrics(self, db, profile_id: Optional[UUID]) -> tuple[Optional[float], Optional[float]]:
        if not profile_id:
            return None, None
        row = (await db.execute(text("""
            SELECT win_rate, avg_pnl_pct / 100.0 AS avg_pnl_decimal
            FROM profile_metrics
            WHERE profile_id = :pid
            ORDER BY period_end DESC NULLS LAST, calculated_at DESC
            LIMIT 1
        """), {"pid": str(profile_id)})).first()
        if not row:
            return None, None
        return _float(row.win_rate), _float(row.avg_pnl_decimal)

    async def _review_shadow(self, db, user_id, cycle, settings, metrics):
        candidates = list((await db.execute(
            select(ProfileIntelligenceAutopilotCandidate).where(
                ProfileIntelligenceAutopilotCandidate.user_id == user_id,
                ProfileIntelligenceAutopilotCandidate.state.in_((
                    "SHADOW_COLLECTING",
                    "SHADOW_READY",
                    "SHADOW_READY_FOR_REVIEW",
                )),
            ).order_by(ProfileIntelligenceAutopilotCandidate.created_at)
        )).scalars().all())
        for candidate in candidates:
            if not await self._is_enabled(db, user_id):
                return
            if not await self._reconcile_manual_changes(db, user_id, cycle, candidate):
                continue
            observed = await self._shadow_metrics(db, candidate)
            elapsed_hours = max(0.0, (utcnow() - candidate.shadow_started_at).total_seconds() / 3600)
            candidate.observed_trades = observed["trades"]
            candidate.observed_win_rate = observed["win_rate"]
            candidate.observed_avg_pnl_pct = observed["avg_pnl_pct"]
            candidate.updated_at = utcnow()
            if evaluation_ready(observed["trades"], elapsed_hours, settings):
                became_ready = candidate.state != "SHADOW_READY"
                candidate.state = "SHADOW_READY"
                if became_ready:
                    await self._audit(
                        db,
                        user_id=user_id,
                        cycle_id=cycle.id,
                        candidate_id=candidate.id,
                        profile_id=candidate.profile_id,
                        watchlist_id=candidate.shadow_watchlist_id,
                        event_type="SHADOW_READY",
                        input_metrics=observed,
                        thresholds=settings,
                        decision="SHADOW_READY",
                        reason="Amostra shadow atingiu a janela mínima de revisão.",
                        result={
                            "approval_required": True,
                            "mutation_applied": False,
                        },
                    )
            incumbent_wr, incumbent_pnl = await self._incumbent_metrics(db, candidate.previous_profile_id)
            decision, reason = promotion_decision(
                trades=observed["trades"],
                elapsed_hours=elapsed_hours,
                win_rate=observed["win_rate"],
                avg_pnl_pct=observed["avg_pnl_pct"],
                settings=settings,
                incumbent_exists=candidate.previous_profile_id is not None,
                incumbent_win_rate=incumbent_wr,
                incumbent_avg_pnl_pct=incumbent_pnl,
            )
            input_metrics = {
                **observed,
                "elapsed_hours": elapsed_hours,
                "incumbent_win_rate": incumbent_wr,
                "incumbent_avg_pnl_pct": incumbent_pnl,
            }
            if decision == "COLLECT":
                candidate.state = "SHADOW_COLLECTING"
                continue
            if decision == "INSUFFICIENT_EVIDENCE":
                metrics["insufficient_evidence"] += 1
                await self._audit(
                    db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
                    profile_id=candidate.profile_id, watchlist_id=candidate.shadow_watchlist_id,
                    event_type="INSUFFICIENT_EVIDENCE", input_metrics=input_metrics,
                    thresholds=settings, decision=decision, reason=reason,
                )
                continue
            if decision == "REJECT":
                await self._reject_candidate(db, user_id, cycle, candidate, settings, input_metrics, reason)
                metrics["rejected"] += 1
                continue
            safe, gate_details = await self.gate_evaluator.evaluate(db, user_id)
            await self._mark_pending_human_approval(
                db,
                user_id,
                cycle,
                candidate,
                settings,
                input_metrics,
                gate_details,
                gates_passed=safe,
            )
            metrics["waiting_live"] += 1

    async def _reconcile_manual_changes(self, db, user_id, cycle, candidate) -> bool:
        profile = await db.get(Profile, candidate.profile_id)
        watchlist = await db.get(PipelineWatchlist, candidate.shadow_watchlist_id) if candidate.shadow_watchlist_id else None
        if not profile or (candidate.origin_profile_id and not await db.get(Profile, candidate.origin_profile_id)):
            candidate.state = "DISABLED"
            candidate.decision_reason = "Profile candidato/original ausente; reconciliação manual necessária."
            candidate.updated_at = utcnow()
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
                profile_id=candidate.profile_id, watchlist_id=candidate.shadow_watchlist_id,
                event_type="PROFILE_LINK_RECONCILIATION_REQUIRED",
                decision="DISABLED", reason=candidate.decision_reason,
            )
            return False

        evidence = dict(_json(candidate.evidence_json) or {})
        versions = list(evidence.get("reference_versions") or [])
        current_config = _json(profile.config) or {}
        current_hash = _content_hash(current_config)
        previous_hash = evidence.get("profile_config_hash")
        current_watchlist = {
            "profile_id": str(watchlist.profile_id) if watchlist and watchlist.profile_id else None,
            "source_watchlist_id": str(watchlist.source_watchlist_id) if watchlist and watchlist.source_watchlist_id else None,
            "auto_refresh": bool(watchlist.auto_refresh) if watchlist else False,
        }
        previous_watchlist = evidence.get("watchlist_snapshot")
        if current_hash != previous_hash or current_watchlist != previous_watchlist:
            versions.append({
                "captured_at": utcnow().isoformat(),
                "previous_profile_config_hash": previous_hash,
                "new_profile_config_hash": current_hash,
                "previous_watchlist": previous_watchlist,
                "new_watchlist": current_watchlist,
                "source": "manual_change_detected",
            })
            evidence["reference_versions"] = versions
            evidence["profile_config_hash"] = current_hash
            evidence["watchlist_snapshot"] = current_watchlist
            candidate.evidence_json = evidence
            candidate.canonical_rules_json = extract_profile_rules(current_config)
            candidate.canonical_signature = canonical_signature(candidate.canonical_rules_json)
            candidate.updated_at = utcnow()
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
                profile_id=profile.id, profile_version=profile.profile_version,
                watchlist_id=watchlist.id if watchlist else None,
                event_type="MANUAL_CHANGE_REFERENCE_VERSION_CREATED",
                decision="REFERENCE_UPDATED",
                reason="Alteração manual preservada como nova versão de referência; nenhum valor foi sobrescrito.",
                result=versions[-1],
            )
        if not watchlist or watchlist.profile_id != candidate.profile_id:
            candidate.state = "DISABLED"
            candidate.decision_reason = "Associação manual da watchlist mudou; reconciliação necessária."
            candidate.updated_at = utcnow()
            await self._audit(
                db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
                profile_id=candidate.profile_id, watchlist_id=candidate.shadow_watchlist_id,
                event_type="CANDIDATE_DISABLED_FOR_RECONCILIATION",
                decision="DISABLED", reason=candidate.decision_reason,
            )
            return False
        return True

    async def _reject_candidate(self, db, user_id, cycle, candidate, settings, input_metrics, reason):
        now = utcnow()
        candidate.state = "REJECTED"
        candidate.approval_status = "rejected"
        candidate.approval_required = True
        candidate.promotion_blocked_reason = "candidate_rejected"
        candidate.rejected_at = now
        candidate.updated_at = now
        candidate.decision_reason = reason
        profile = await db.get(Profile, candidate.profile_id)
        watchlist = await db.get(PipelineWatchlist, candidate.shadow_watchlist_id) if candidate.shadow_watchlist_id else None
        if profile:
            profile.is_active = False
            profile.live_trading_enabled = False
            profile.is_shadow_only = True
        if watchlist:
            watchlist.auto_refresh = False
        blocked_until = now + timedelta(hours=float(settings["loss_family_cooldown_hours"]))
        existing = await db.scalar(
            select(ProfileIntelligenceLossFamily).where(
                ProfileIntelligenceLossFamily.user_id == user_id,
                ProfileIntelligenceLossFamily.canonical_signature == candidate.canonical_signature,
            )
        )
        if existing:
            existing.canonical_rules_json = candidate.canonical_rules_json
            existing.metrics_json = input_metrics
            existing.rejection_reason = reason
            existing.blocked_at = now
            existing.blocked_until = blocked_until
            existing.candidate_id = candidate.id
        else:
            db.add(ProfileIntelligenceLossFamily(
                id=uuid4(), user_id=user_id,
                canonical_signature=candidate.canonical_signature,
                canonical_rules_json=candidate.canonical_rules_json,
                metrics_json=input_metrics,
                rejection_reason=reason,
                blocked_at=now,
                blocked_until=blocked_until,
                candidate_id=candidate.id,
            ))
        await self._audit(
            db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
            profile_id=candidate.profile_id, watchlist_id=candidate.shadow_watchlist_id,
            event_type="CANDIDATE_REJECTED", input_metrics=input_metrics,
            thresholds=settings, decision="REJECTED", reason=reason,
            result={
                **self._promotion_audit_payload(
                    candidate,
                    reason_code="candidate_rejected_by_shadow_metrics",
                    mutation_applied=False,
                ),
                "cooldown_until": blocked_until.isoformat(),
            },
        )

    async def _mark_pending_human_approval(
        self,
        db,
        user_id,
        cycle,
        candidate,
        settings,
        input_metrics,
        gate_details,
        *,
        gates_passed,
    ):
        plan = await self._build_live_change_plan(db, candidate)
        rollback_payload = plan["rollback_payload"] if plan else None
        evidence = dict(_json(candidate.evidence_json) or {})
        evidence["live_promotion_recommendation"] = {
            "candidate_id": str(candidate.id),
            "incumbent_profile_id": (
                rollback_payload.get("previous_profile_id")
                if rollback_payload
                else None
            ),
            "candidate_profile_id": str(candidate.profile_id),
            "shadow_metrics": input_metrics,
            "comparison_metrics": {
                "incumbent_win_rate": input_metrics.get("incumbent_win_rate"),
                "incumbent_avg_pnl_pct": input_metrics.get("incumbent_avg_pnl_pct"),
            },
            "risk_summary": {
                "operational_gates_passed": gates_passed,
                "operational_gates": gate_details,
                "human_approval_required": True,
            },
            "expected_impact": {
                "win_rate": _float(candidate.observed_win_rate),
                "avg_pnl_pct": _float(candidate.observed_avg_pnl_pct),
            },
            "rollback_payload": rollback_payload,
            "approval_required": True,
        }
        candidate.evidence_json = evidence
        candidate.rollback_payload = rollback_payload
        candidate.state = "PENDING_HUMAN_APPROVAL"
        candidate.approval_status = "pending"
        candidate.approval_required = True
        candidate.promotion_blocked_reason = "pending_human_approval"
        candidate.decision_reason = (
            "Métricas aprovadas; promoção live bloqueada até aprovação humana explícita."
        )
        candidate.updated_at = utcnow()
        await self._audit(
            db,
            user_id=user_id,
            cycle_id=cycle.id,
            candidate_id=candidate.id,
            profile_id=candidate.profile_id,
            watchlist_id=(
                plan["live_watchlist"].id
                if plan
                else candidate.shadow_watchlist_id
            ),
            event_type="LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL",
            input_metrics=input_metrics,
            thresholds=settings,
            decision="PENDING_HUMAN_APPROVAL",
            reason=candidate.decision_reason,
            result={
                **evidence["live_promotion_recommendation"],
                "before_json": plan["before_json"] if plan else {},
                "after_json": plan["after_json"] if plan else {},
                "diff_json": plan["diff_json"] if plan else {},
                "reason_code": "pending_human_approval",
                "approved_by": None,
                "approved_at": None,
                "mutation_applied": False,
            },
        )

    async def _block_legacy_waiting_live(
        self,
        db,
        user_id,
        cycle,
        _settings=None,
        _metrics=None,
    ):
        candidates = list((await db.execute(
            select(ProfileIntelligenceAutopilotCandidate).where(
                ProfileIntelligenceAutopilotCandidate.user_id == user_id,
                ProfileIntelligenceAutopilotCandidate.state == "APPROVED_WAITING_LIVE",
            )
        )).scalars().all())
        for candidate in candidates:
            candidate.state = "PENDING_HUMAN_APPROVAL"
            candidate.approval_status = "pending"
            candidate.approval_required = True
            candidate.promotion_blocked_reason = "legacy_waiting_live_requires_human_approval"
            candidate.updated_at = utcnow()
            await self._audit(
                db,
                user_id=user_id,
                cycle_id=cycle.id,
                candidate_id=candidate.id,
                profile_id=candidate.profile_id,
                watchlist_id=candidate.shadow_watchlist_id,
                event_type="LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL",
                decision="PENDING_HUMAN_APPROVAL",
                reason=candidate.promotion_blocked_reason,
                result=self._promotion_audit_payload(
                    candidate,
                    reason_code=candidate.promotion_blocked_reason,
                    mutation_applied=False,
                ),
            )

    async def _live_metrics(self, db, candidate) -> dict:
        row = (await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE outcome IN ('tp','sl','timeout','TP_HIT','SL_HIT','TIMEOUT'))::int AS trades,
                COUNT(*) FILTER (WHERE outcome IN ('tp','TP_HIT'))::int AS wins,
                AVG(pnl_pct) FILTER (
                    WHERE outcome IN ('tp','sl','timeout','TP_HIT','SL_HIT','TIMEOUT')
                      AND pnl_pct IS NOT NULL
                ) / 100.0 AS avg_pnl_decimal
            FROM decisions_log
            WHERE user_id = :uid
              AND profile_id = :pid
              AND trade_executed IS TRUE
              AND created_at >= :promoted_at
        """), {
            "uid": str(candidate.user_id),
            "pid": str(candidate.profile_id),
            "promoted_at": candidate.promoted_at,
        })).one()
        trades, wins = int(row.trades or 0), int(row.wins or 0)
        return {
            "trades": trades,
            "wins": wins,
            "win_rate": wins / trades if trades else None,
            "avg_pnl_pct": _float(row.avg_pnl_decimal),
        }

    async def _monitor_live(self, db, user_id, cycle, settings, metrics):
        candidates = list((await db.execute(
            select(ProfileIntelligenceAutopilotCandidate).where(
                ProfileIntelligenceAutopilotCandidate.user_id == user_id,
                ProfileIntelligenceAutopilotCandidate.state.in_((
                    "LIVE",
                    "LIVE_ACTIVATED",
                )),
            )
        )).scalars().all())
        for candidate in candidates:
            if not await self._is_enabled(db, user_id):
                return
            current = await self._live_metrics(db, candidate)
            if current["trades"] == 0 or current["win_rate"] is None or current["avg_pnl_pct"] is None:
                metrics["insufficient_evidence"] += 1
                await self._audit(
                    db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
                    profile_id=candidate.profile_id, watchlist_id=candidate.target_watchlist_id,
                    event_type="INSUFFICIENT_EVIDENCE", input_metrics=current,
                    thresholds=settings, decision="NO_ROLLBACK_DECISION",
                    reason="Métrica live ausente ou inconsistente.",
                )
                continue
            if rollback_required(
                current["win_rate"], current["avg_pnl_pct"],
                _float(candidate.promotion_win_rate), _float(candidate.promotion_avg_pnl_pct),
                float(settings["rollback_relative_floor"]),
            ):
                await self._rollback_candidate(db, user_id, cycle, candidate, settings, current)
                metrics["rolled_back"] += 1

    async def _rollback_candidate(
        self,
        db,
        user_id,
        cycle,
        candidate,
        settings,
        current,
        actor_user_id=None,
    ):
        rollback_payload = _json(candidate.rollback_payload) or {}
        target_id = rollback_payload.get("watchlist_id") or candidate.target_watchlist_id
        previous_id = rollback_payload.get("previous_profile_id") or candidate.previous_profile_id
        target = await db.get(PipelineWatchlist, UUID(str(target_id))) if target_id else None
        profile = await db.get(Profile, candidate.profile_id)
        previous = await db.get(Profile, UUID(str(previous_id))) if previous_id else None
        shadow = await db.get(PipelineWatchlist, candidate.shadow_watchlist_id) if candidate.shadow_watchlist_id else None
        if not target:
            await self._queue_compensation(
                db, user_id, cycle.id, candidate.id, "ROLLBACK_WATCHLIST_MISSING",
                {
                    "profile_id": str(candidate.profile_id),
                    "previous_profile_id": str(previous_id) if previous_id else None,
                },
            )
            return
        before_json = {
            "watchlist": {
                "id": str(target.id),
                "profile_id": str(target.profile_id) if target.profile_id else None,
                "auto_refresh": bool(target.auto_refresh),
            },
            "candidate_profile": {
                "id": str(profile.id) if profile else None,
                "is_active": bool(profile.is_active) if profile else None,
                "is_shadow_only": bool(profile.is_shadow_only) if profile else None,
                "live_trading_enabled": (
                    bool(profile.live_trading_enabled) if profile else None
                ),
            },
        }
        if previous:
            target.profile_id = previous.id
            target.auto_refresh = bool(
                rollback_payload.get("watchlist_auto_refresh", True)
            )
            incumbent_snapshot = rollback_payload.get("incumbent_profile") or {}
            previous.is_active = bool(incumbent_snapshot.get("is_active", True))
            previous.live_trading_enabled = bool(
                incumbent_snapshot.get("live_trading_enabled", True)
            )
            previous.is_shadow_only = bool(
                incumbent_snapshot.get("is_shadow_only", False)
            )
        else:
            target.auto_refresh = False
            if shadow:
                shadow.auto_refresh = True
        if profile:
            candidate_snapshot = rollback_payload.get("candidate_profile") or {}
            profile.is_active = bool(candidate_snapshot.get("is_active", True))
            profile.live_trading_enabled = bool(
                candidate_snapshot.get("live_trading_enabled", False)
            )
            profile.is_shadow_only = bool(
                candidate_snapshot.get("is_shadow_only", True)
            )
        candidate.state = "ROLLED_BACK"
        candidate.rollback_at = utcnow()
        candidate.updated_at = candidate.rollback_at
        candidate.decision_reason = "Rollback por degradação relativa de Win Rate ou P&L."
        db.add(ProfileIntelligenceAutopilotAssociation(
            id=uuid4(), user_id=user_id, candidate_id=candidate.id,
            watchlist_id=target.id, previous_profile_id=candidate.profile_id,
            new_profile_id=previous.id if previous else None,
            event_type="ROLLBACK", is_active=True,
        ))
        after_json = {
            "watchlist": {
                "id": str(target.id),
                "profile_id": str(target.profile_id) if target.profile_id else None,
                "auto_refresh": bool(target.auto_refresh),
            },
            "candidate_profile": {
                "id": str(profile.id) if profile else None,
                "is_active": bool(profile.is_active) if profile else None,
                "is_shadow_only": bool(profile.is_shadow_only) if profile else None,
                "live_trading_enabled": (
                    bool(profile.live_trading_enabled) if profile else None
                ),
            },
        }
        diff_json = {
            key: {"before": before_json[key], "after": after_json[key]}
            for key in before_json
            if before_json[key] != after_json[key]
        }
        await self._audit(
            db, user_id=user_id, cycle_id=cycle.id, candidate_id=candidate.id,
            profile_id=candidate.profile_id, watchlist_id=target.id,
            actor_user_id=actor_user_id,
            event_type="CANDIDATE_ROLLED_BACK", input_metrics={
                **current,
                "promotion_win_rate": _float(candidate.promotion_win_rate),
                "promotion_avg_pnl_pct": _float(candidate.promotion_avg_pnl_pct),
            }, thresholds={"rollback_relative_floor": settings["rollback_relative_floor"]},
            decision="ROLLED_BACK", reason=candidate.decision_reason,
            result={
                "restored_profile_id": str(previous.id) if previous else None,
                "returned_to_shadow": previous is None,
                "before_json": before_json,
                "after_json": after_json,
                "diff_json": diff_json,
                "rollback_payload": rollback_payload,
                "mutation_applied": True,
            },
        )

    async def _queue_compensation(self, db, user_id, cycle_id, candidate_id, operation, payload):
        db.add(ProfileIntelligenceAutopilotCompensation(
            id=uuid4(), user_id=user_id, cycle_id=cycle_id, candidate_id=candidate_id,
            operation=operation, payload_json=payload, status="PENDING",
        ))
        await self._audit(
            db, user_id=user_id, cycle_id=cycle_id, candidate_id=candidate_id,
            event_type="COMPENSATION_QUEUED", decision="PENDING",
            reason=operation, result=payload,
        )

    async def _build_report(self, db, user_id, cycle, settings, metrics) -> dict:
        candidates = list((await db.execute(
            select(ProfileIntelligenceAutopilotCandidate).where(
                ProfileIntelligenceAutopilotCandidate.user_id == user_id
            ).order_by(ProfileIntelligenceAutopilotCandidate.updated_at.desc()).limit(100)
        )).scalars().all())
        cooldowns = list((await db.execute(
            select(ProfileIntelligenceLossFamily).where(
                ProfileIntelligenceLossFamily.user_id == user_id,
                ProfileIntelligenceLossFamily.blocked_until > utcnow(),
            )
        )).scalars().all())
        return {
            "autopilot_enabled": await self._is_enabled(db, user_id),
            "cycle": {
                "id": str(cycle.id),
                "window_start": cycle.window_start.isoformat(),
                "analysis_run_id": str(cycle.analysis_run_id) if cycle.analysis_run_id else None,
                "status": cycle.status,
                "checkpoint": cycle.checkpoint,
            },
            "summary": metrics,
            "thresholds": settings,
            "candidates": [{
                "id": str(item.id),
                "profile_id": str(item.profile_id),
                "origin_profile_id": str(item.origin_profile_id) if item.origin_profile_id else None,
                "watchlist_id": str(item.shadow_watchlist_id) if item.shadow_watchlist_id else None,
                "target_watchlist_id": str(item.target_watchlist_id) if item.target_watchlist_id else None,
                "state": item.state,
                "trades": item.observed_trades,
                "win_rate": _float(item.observed_win_rate),
                "avg_pnl_pct": _float(item.observed_avg_pnl_pct),
                "reason": item.decision_reason,
            } for item in candidates],
            "loss_families": [{
                "signature": item.canonical_signature,
                "blocked_until": item.blocked_until.isoformat(),
                "reason": item.rejection_reason,
            } for item in cooldowns],
            "errors": cycle.errors_json or [],
            "generated_at": utcnow().isoformat(),
        }

    async def _audit(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        event_type: str,
        actor_user_id: Optional[UUID] = None,
        cycle_id: Optional[UUID] = None,
        candidate_id: Optional[UUID] = None,
        profile_id: Optional[UUID] = None,
        profile_version: Optional[datetime] = None,
        watchlist_id: Optional[UUID] = None,
        combination_id: Optional[UUID] = None,
        suggestion_id: Optional[UUID] = None,
        input_metrics: Optional[dict] = None,
        thresholds: Optional[dict] = None,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
        result: Optional[dict] = None,
    ):
        db.add(ProfileIntelligenceAutopilotAudit(
            id=uuid4(),
            user_id=user_id,
            actor_user_id=actor_user_id,
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            profile_id=profile_id,
            profile_version=profile_version,
            watchlist_id=watchlist_id,
            combination_id=combination_id,
            suggestion_id=suggestion_id,
            event_type=event_type,
            input_metrics_json=input_metrics or {},
            thresholds_json=thresholds or {},
            decision=decision,
            reason=reason,
            result_json=result or {},
        ))
        await db.flush()
