"""
Profile Create Service — transforms a ProfileSuggestion into a live Strategy Profile.

Security guarantees (non-negotiable):
  - live_trading_enabled is ALWAYS False — never set to True by this service.
  - is_shadow_only is ALWAYS True.
  - profile_type is ALWAYS 'GENERATED'.
  - Only profiles with status in ('pending_user_approval', 'draft') can be promoted.
  - LOW confidence and overfit_risk require explicit confirmation flags in payload.
  - Post-entry metrics (outcome, pnl_pct, mae_pct, mfe_pct, holding_seconds,
    exit_price, future_*) are forbidden as signal/scoring/block rule fields.
  - Full audit trail written before commit.
  - Single atomic transaction: all-or-nothing.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from ..models.profile_intelligence import (
    AlgorithmForwardValidation,
    ProfileIntelligenceAuditLog,
    ProfileRuleCombination,
    ProfileSuggestion,
)

_ConfigProfileModel = ConfigProfile

logger = logging.getLogger("scalpyn.services.profile_create")

# ── Forbidden post-entry fields ────────────────────────────────────────────────
_FORBIDDEN_FIELDS = frozenset({
    "outcome", "pnl_pct", "exit_price", "future_outcome", "future_pnl_pct",
    "holding_seconds", "mae_pct", "mfe_pct", "realized_pnl", "unrealized_pnl",
    "closed_at", "tp_hit", "sl_hit", "timeout_hit",
})

# ── Valid creation modes (no live trading) ─────────────────────────────────────
_VALID_MODES = frozenset({"SHADOW_ONLY", "DRAFT"})

# ── Allowed suggestion statuses for promotion ──────────────────────────────────
_PROMOTABLE_STATUSES = frozenset({
    "pending_user_approval",
    "draft",
    "validated",
    "approved",
})

# ── Operator normalization map ─────────────────────────────────────────────────
_OP_NORMALIZE = {
    "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
    "eq": "=", "ne": "!=", "==": "=",
    ">": ">", "<": "<", ">=": ">=", "<=": "<=",
    "=": "=", "!=": "!=", "between": "between",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_value(v: Any) -> Any:
    """Convert comma-decimal strings to float with dot; keep other types."""
    if isinstance(v, str):
        try:
            return float(v.replace(",", "."))
        except ValueError:
            return v
    return v


def _normalize_rule(rule: dict) -> dict:
    """Normalize a single condition/rule dict in-place copy."""
    r = dict(rule)
    field = r.get("field") or r.get("indicator") or ""
    r["indicator"] = field
    r.pop("field", None)
    r["operator"] = _OP_NORMALIZE.get(str(r.get("operator", "")).strip(), r.get("operator", ""))
    r["value"] = _normalize_value(r.get("value"))
    if "min" in r:
        r["min"] = _normalize_value(r["min"])
    if "max" in r:
        r["max"] = _normalize_value(r["max"])
    return r


def _normalize_signal_condition(rule: dict) -> dict:
    """Normalize a profile signal using the canonical ``field`` key."""
    r = _normalize_rule(rule)
    r["field"] = r.pop("indicator", "")
    return r


def _check_forbidden(conditions: List[dict]) -> List[str]:
    """Return list of forbidden field names found in conditions."""
    bad = []
    for c in conditions:
        f = (c.get("field") or c.get("indicator") or "").lower()
        if f in _FORBIDDEN_FIELDS:
            bad.append(f)
    return bad


def _extract_conditions(section: Any) -> List[dict]:
    """Extract conditions list from a signals/entry_triggers/block_rules section."""
    if not section:
        return []
    if isinstance(section, list):
        return section
    if isinstance(section, dict):
        return section.get("conditions", []) or section.get("blocks", []) or []
    return []


def _stable_rule_id(indicator: str, operator: str, value: Any, category: str) -> str:
    """Generate a stable, deterministic ID for a master scoring rule."""
    raw = f"{indicator}|{operator}|{value}|{category}"
    h = abs(hash(raw)) % (10 ** 8)
    safe = indicator.replace("_", "").replace(" ", "")[:12]
    return f"sr_{safe}_{h}"


# ── Master Scoring Rules ───────────────────────────────────────────────────────

async def _get_or_create_master_score_config(
    db: AsyncSession, user_id: UUID
) -> Tuple[Any, dict]:
    """Return (config_profile_orm, config_json_dict). Creates if missing."""
    result = await db.execute(
        select(_ConfigProfileModel).where(
            _ConfigProfileModel.user_id == user_id,
            _ConfigProfileModel.config_type == "score",
            _ConfigProfileModel.is_active == True,
        ).order_by(_ConfigProfileModel.updated_at.desc()).limit(1)
    )
    cp = result.scalars().first()
    if cp is None:
        cfg = {
            "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
            "auto_select_top_n": 5,
            "auto_select_min_score": 80.0,
            "rules": [],
        }
        cp = ConfigProfile(
            user_id=user_id,
            config_type="score",
            config_json=cfg,
            is_active=True,
        )
        db.add(cp)
        await db.flush()
    # asyncpg may return JSONB as string
    raw = cp.config_json
    cfg_dict: dict = json.loads(raw) if isinstance(raw, str) else (raw or {})
    if "rules" not in cfg_dict:
        cfg_dict["rules"] = []
    return cp, cfg_dict


async def ensure_master_scoring_rules(
    db: AsyncSession,
    user_id: UUID,
    required_rules: List[dict],
    create_missing: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Ensure required scoring rules exist in the master score config.

    Returns:
        {
            "created": [...],       # newly created rule dicts
            "reused": [...],        # existing rule dicts
            "missing": [...],       # rules needed but not created (create_missing=False)
            "selected_ids": [...],  # all rule IDs to link in profile.config.scoring
        }
    """
    if not required_rules:
        return {"created": [], "reused": [], "missing": [], "selected_ids": []}

    cp, cfg = await _get_or_create_master_score_config(db, user_id)
    existing_rules: List[dict] = cfg.get("rules", [])

    created, reused, missing = [], [], []
    selected_ids = []

    for req in required_rules:
        indicator = str(req.get("indicator") or req.get("field") or "").strip()
        if not indicator:
            continue
        operator = _OP_NORMALIZE.get(str(req.get("operator", "")).strip(), req.get("operator", ">"))
        value = _normalize_value(req.get("value"))
        minimum = _normalize_value(req.get("min")) if "min" in req else None
        maximum = _normalize_value(req.get("max")) if "max" in req else None
        category = str(req.get("category") or "momentum").strip()
        points = float(req.get("points", 10))

        # Check equivalence
        match = None
        for ex in existing_rules:
            same_ind = str(ex.get("indicator", "")).strip() == indicator
            same_op = _OP_NORMALIZE.get(str(ex.get("operator", "")), ex.get("operator")) == operator
            same_cat = str(ex.get("category", "")).strip() == category
            ex_val = _normalize_value(ex.get("value"))
            try:
                same_points = abs(float(ex.get("points", 0)) - points) < 1e-6
            except (TypeError, ValueError):
                same_points = False
            if operator == "between":
                try:
                    same_val = (
                        abs(float(ex.get("min")) - float(minimum)) < 1e-6
                        and abs(float(ex.get("max")) - float(maximum)) < 1e-6
                    )
                except (TypeError, ValueError):
                    same_val = ex.get("min") == minimum and ex.get("max") == maximum
            else:
                # Numeric tolerance for value comparison
                try:
                    same_val = abs(float(ex_val) - float(value)) < 1e-6
                except (TypeError, ValueError):
                    same_val = str(ex_val) == str(value)
            if same_ind and same_op and same_cat and same_val and same_points:
                match = ex
                break

        if match is not None:
            reused.append(match)
            selected_ids.append(match["id"])
        elif create_missing:
            stable_value = f"{minimum}:{maximum}" if operator == "between" else value
            rule_id = _stable_rule_id(indicator, operator, stable_value, category)
            # Avoid ID collision
            existing_ids = {r.get("id") for r in existing_rules}
            suffix = 0
            base_id = rule_id
            while rule_id in existing_ids:
                suffix += 1
                rule_id = f"{base_id}_{suffix}"
            new_rule = {
                "id": rule_id,
                "name": req.get("name") or f"{indicator} {operator} {value}",
                "enabled": True,
                "indicator": indicator,
                "operator": operator,
                "value": value,
                "points": points,
                "category": category,
                "description": req.get("description") or f"Generated by Profile Intelligence",
                "source": "profile_intelligence",
            }
            if operator == "between":
                new_rule.pop("value", None)
                new_rule["min"] = minimum
                new_rule["max"] = maximum
            if not dry_run:
                existing_rules.append(new_rule)
            created.append(new_rule)
            selected_ids.append(rule_id)
        else:
            missing.append(req)

    if not dry_run and (created or not existing_rules):
        cfg["rules"] = existing_rules
        cp.config_json = cfg
        cp.updated_at = _now()
        await db.flush()

        # Config audit log
        from ..models.config_profile import ConfigAuditLog
        audit = ConfigAuditLog(
            config_id=cp.id,
            changed_by=user_id,
            previous_json={"rules_count_before": len(existing_rules) - len(created)},
            new_json={"rules_count_after": len(existing_rules), "added": [r["id"] for r in created]},
            change_description=f"Profile Intelligence added {len(created)} scoring rule(s)",
        )
        db.add(audit)
        await db.flush()

    return {
        "created": created,
        "reused": reused,
        "missing": missing,
        "selected_ids": selected_ids,
    }


# ── Config Builder ─────────────────────────────────────────────────────────────

def _build_profile_config(
    suggestion,
    selected_rule_ids: List[str],
    created_rules: List[dict],
    mode: str,
) -> dict:
    """Build the full profiles.config JSONB from suggestion data."""
    raw_signals = suggestion.suggested_signals_json or {}
    raw_scoring = suggestion.suggested_scoring_json or {}
    raw_blocks = suggestion.suggested_block_rules_json or []
    raw_config = suggestion.suggested_config_json or {}

    # Resolve signals section
    signals_conditions = (
        _extract_conditions(raw_signals)
        or _extract_conditions(raw_config.get("signals"))
        or _extract_conditions(raw_config.get("entry_triggers"))
    )
    signals_logic = (
        (raw_signals.get("logic") if isinstance(raw_signals, dict) else None)
        or raw_config.get("signals", {}).get("logic", "AND")
        or "AND"
    )
    signals = {
        "logic": signals_logic,
        "conditions": [_normalize_signal_condition(c) for c in signals_conditions],
    }

    # Block rules
    blocks_raw = (
        _extract_conditions(raw_blocks)
        or _extract_conditions(raw_config.get("block_rules"))
        or []
    )
    block_rules = [_normalize_rule(b) for b in blocks_raw]

    # Scoring weights (use suggestion's or balanced default)
    scoring_weights = (
        (raw_scoring.get("weights") if isinstance(raw_scoring, dict) else None)
        or raw_config.get("scoring", {}).get("weights")
        or {"liquidity": 25, "momentum": 25, "market_structure": 25, "signal": 25}
    )

    # All scoring rules inline (for UI display)
    generated_rules = (
        (raw_scoring.get("rules") if isinstance(raw_scoring, dict) else None)
        or raw_config.get("scoring", {}).get("generated_rules")
        or created_rules
    )

    config = {
        "signals": signals,
        "entry_triggers": signals,  # alias for compatibility
        "scoring": {
            "selected_rule_ids": selected_rule_ids,
            "weights": scoring_weights,
            "generated_rules": generated_rules,
            "source": "profile_intelligence",
            "suggestion_id": str(suggestion.id),
        },
        "block_rules": {"blocks": block_rules} if block_rules else {"blocks": []},
        "metadata": {
            "generated_by": "profile_intelligence",
            "suggestion_id": str(suggestion.id),
            "source_combination_id": str(suggestion.source_combination_id) if suggestion.source_combination_id else None,
            "confidence_level": suggestion.confidence_level,
            "confidence_score": float(suggestion.confidence_score or 0),
            "created_as": mode,
            "live_trading_enabled": False,
            "is_shadow_only": True,
            "profile_family": suggestion.suggested_profile_family,
        },
    }

    # Propagate any extra config keys from suggestion
    for k in ("filters", "default_timeframe"):
        v = raw_config.get(k)
        if v:
            config[k] = v

    return config


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_rules(config: dict) -> List[str]:
    """Return list of warning strings for forbidden fields in rules."""
    warnings = []
    for section_name in ("signals", "entry_triggers", "block_rules"):
        section = config.get(section_name, {})
        conds = _extract_conditions(section)
        bad = _check_forbidden(conds)
        if bad:
            warnings.append(f"Section '{section_name}' contains forbidden post-entry fields: {bad}")
    return warnings


# ── Main Service ───────────────────────────────────────────────────────────────

class ProfileCreateService:
    """
    Transforms a ProfileSuggestion into a new Strategy Profile.

    Always creates profiles with is_shadow_only=True, live_trading_enabled=False.
    Never activates live trading.
    """

    async def create_from_suggestion(
        self,
        db: AsyncSession,
        user_id: UUID,
        suggestion_id: UUID,
        profile_name: Optional[str] = None,
        profile_description: Optional[str] = None,
        mode: str = "SHADOW_ONLY",
        confirm_low_confidence: bool = False,
        confirm_overfit_risk: bool = False,
        create_missing_master_rules: bool = True,
        reuse_existing_master_rules: bool = True,
        assign_to_watchlist_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Main entry point.

        Returns a result dict with status, profile_id (if created), warnings, etc.
        On dry_run=True: returns preview without writing anything.
        """
        # ── 1. Validate mode ──────────────────────────────────────────────────
        if mode not in _VALID_MODES:
            raise ValueError(
                f"Mode '{mode}' não é suportado nesta fase. "
                f"Use: {sorted(_VALID_MODES)}. Live trading permanece desativado."
            )

        # ── 2. Fetch + ownership check ─────────────────────────────────────────
        result = await db.execute(
            select(ProfileSuggestion).where(
                ProfileSuggestion.id == suggestion_id,
                ProfileSuggestion.user_id == user_id,
            ).with_for_update() if not dry_run else
            select(ProfileSuggestion).where(
                ProfileSuggestion.id == suggestion_id,
                ProfileSuggestion.user_id == user_id,
            )
        )
        suggestion = result.scalars().first()
        if not suggestion:
            raise LookupError("Sugestão não encontrada ou não pertence ao usuário.")

        # ── 3. Idempotency: already created ───────────────────────────────────
        if suggestion.status in {"created", "applied"} and suggestion.created_profile_id:
            return {
                "status": "already_created",
                "profile_id": str(suggestion.created_profile_id),
                "profile_name": profile_name or suggestion.suggested_profile_name,
                "profile_url": f"/profiles/{suggestion.created_profile_id}",
                "message": "Profile já foi criado a partir desta sugestão (idempotente).",
                "audit_id": None,
            }

        # ── 4. Status guard ───────────────────────────────────────────────────
        if suggestion.status not in _PROMOTABLE_STATUSES:
            raise ValueError(
                f"Sugestão com status '{suggestion.status}' não pode ser promovida. "
                f"Apenas: {sorted(_PROMOTABLE_STATUSES)}."
            )

        # ── 5. Confidence guard ───────────────────────────────────────────────
        from .algorithm_governance_service import suggestion_registry_block_reasons
        registry_block_reasons = suggestion_registry_block_reasons(suggestion)
        if registry_block_reasons:
            return {
                "status": "blocked",
                "blocked_reasons": registry_block_reasons,
                "warnings": [],
                "profile_payload": None,
            }

        blocked_reasons = []
        warnings = []

        if suggestion.confidence_level == "LOW" and not confirm_low_confidence:
            blocked_reasons.append(
                "Sugestão com LOW confidence exige confirmação explícita "
                "(envie confirm_low_confidence=true)."
            )
        if suggestion.confidence_level == "LOW":
            warnings.append(
                f"LOW confidence ({float(suggestion.confidence_score or 0):.1f}): "
                f"menos de 30 trades de suporte. Valide em shadow antes de usar."
            )

        # ── 6. Overfit guard ──────────────────────────────────────────────────
        overfit_risk = False
        if suggestion.source_combination_id:
            combo_result = await db.execute(
                select(ProfileRuleCombination).where(
                    ProfileRuleCombination.id == suggestion.source_combination_id,
                    ProfileRuleCombination.user_id == user_id,
                )
            )
            combo = combo_result.scalars().first()
            if combo and combo.overfit_risk:
                overfit_risk = True
                if not confirm_overfit_risk:
                    blocked_reasons.append(
                        "Combinação de origem tem risco de overfitting. "
                        "Envie confirm_overfit_risk=true para prosseguir."
                    )
                warnings.append(
                    "⚠️ Risco de overfitting detectado. "
                    "Resultados de discovery podem não generalizar para dados novos."
                )

        if blocked_reasons:
            return {
                "status": "blocked",
                "blocked_reasons": blocked_reasons,
                "warnings": warnings,
                "profile_payload": None,
            }

        # ── 7. Validate + normalize rules ─────────────────────────────────────
        rule_warnings = _validate_rules(suggestion.suggested_config_json or {})
        # Also check top-level suggestion signals/block_rules
        for section, data in [
            ("suggested_signals_json", suggestion.suggested_signals_json),
            ("suggested_block_rules_json", suggestion.suggested_block_rules_json),
        ]:
            if data:
                bad = _check_forbidden(_extract_conditions(data))
                if bad:
                    rule_warnings.append(
                        f"{section} contém campos pós-entrada proibidos: {bad}. "
                        f"Esses campos serão ignorados."
                    )
        warnings.extend(rule_warnings)

        # ── 8. Master scoring rules ───────────────────────────────────────────
        required_rules: List[dict] = (
            suggestion.required_master_scoring_rules_json
            if isinstance(suggestion.required_master_scoring_rules_json, list)
            else (
                json.loads(suggestion.required_master_scoring_rules_json)
                if isinstance(suggestion.required_master_scoring_rules_json, str)
                else []
            )
        ) or []

        master_result = await ensure_master_scoring_rules(
            db=db,
            user_id=user_id,
            required_rules=required_rules,
            create_missing=create_missing_master_rules,
            dry_run=dry_run,
        )
        created_master_rules = master_result["created"]
        reused_master_rules = master_result["reused"]
        missing_master_rules = master_result["missing"]
        selected_rule_ids = master_result["selected_ids"]

        if missing_master_rules and not create_missing_master_rules:
            return {
                "status": "blocked",
                "blocked_reasons": [
                    f"{len(missing_master_rules)} scoring rule(s) master ausente(s) e "
                    f"create_missing_master_rules=false: {[r.get('indicator') for r in missing_master_rules]}"
                ],
                "warnings": warnings,
                "master_rules_missing": missing_master_rules,
            }

        # ── 9. Build profile config ───────────────────────────────────────────
        final_name = (profile_name or suggestion.suggested_profile_name or "").strip()
        if not final_name:
            final_name = f"PI Generated — {suggestion.id!s:.8}"
        final_desc = profile_description or suggestion.suggested_profile_description or ""

        profile_config = _build_profile_config(
            suggestion=suggestion,
            selected_rule_ids=selected_rule_ids,
            created_rules=created_master_rules,
            mode=mode,
        )

        # ── 9.5 Duplicate name warning (non-blocking) ─────────────────────────
        from ..models.profile import Profile as _Profile
        _dup_res = await db.execute(
            select(_Profile.id, _Profile.name).where(
                _Profile.user_id == user_id,
                _Profile.name.ilike(final_name),
            )
        )
        _dups = _dup_res.fetchall()
        if _dups:
            _ids = ", ".join(str(r.id)[:8] + "…" for r in _dups[:3])
            warnings.append(
                f"Já existe{'m' if len(_dups) > 1 else ''} {len(_dups)} profile(s) "
                f"com o nome '{final_name}': [{_ids}]. "
                f"Considere renomear para evitar ambiguidade."
            )

        # ── DRY RUN ───────────────────────────────────────────────────────────
        if dry_run:
            return {
                "status": "dry_run",
                "profile_payload": {
                    "name": final_name,
                    "description": final_desc,
                    "profile_type": "GENERATED",
                    "generated_by": "profile_intelligence",
                    "generated_from_suggestion_id": str(suggestion_id),
                    "is_shadow_only": True,
                    "live_trading_enabled": False,
                    "config": profile_config,
                },
                "master_rules_to_create": created_master_rules,
                "master_rules_to_reuse": reused_master_rules,
                "master_rules_missing": missing_master_rules,
                "selected_rule_ids": selected_rule_ids,
                "warnings": warnings,
                "blocked_reasons": [],
                "overfit_risk": overfit_risk,
                "confidence_level": suggestion.confidence_level,
                "confidence_score": float(suggestion.confidence_score or 0),
            }

        # ── 10. Create Profile ────────────────────────────────────────────────
        now = _now()
        profile = Profile(
            user_id=user_id,
            name=final_name,
            description=final_desc,
            is_active=True,
            profile_type="GENERATED",
            profile_version=now,
            generated_by="profile_intelligence",
            generated_from_suggestion_id=suggestion_id,
            is_shadow_only=True,
            live_trading_enabled=False,  # NEVER True
            config=profile_config,
            auto_pilot_enabled=False,
            auto_pilot_config={},
        )
        db.add(profile)
        await db.flush()  # get profile.id

        profile_id = profile.id

        # ── 11. Profile Audit Log ─────────────────────────────────────────────
        forward_validation = AlgorithmForwardValidation(
            suggestion_id=suggestion.id,
            profile_id=profile_id,
            stage="shadow_forward",
            validation_status=suggestion.validation_status,
            metrics_json={
                "source_run_id": str(suggestion.source_run_id),
                "source_type": suggestion.source_type,
                "evidence_count": suggestion.evidence_count,
                "expected_impact": suggestion.expected_impact or {},
            },
            rollback_payload=suggestion.rollback_payload,
        )
        db.add(forward_validation)
        await db.flush()

        pal = ProfileAuditLog(
            user_id=user_id,
            profile_id=profile_id,
            changed_by=user_id,
            change_source="profile_intelligence",
            change_description=(
                f"Profile criado a partir de sugestão do Profile Intelligence Engine "
                f"(suggestion_id={suggestion_id})"
            ),
            previous_config=None,
            new_config=profile_config,
            previous_profile_version=None,
            new_profile_version=now,
        )
        db.add(pal)
        await db.flush()

        # ── 12. PI Audit Log ──────────────────────────────────────────────────
        pi_audit = ProfileIntelligenceAuditLog(
            user_id=user_id,
            run_id=suggestion.run_id,
            suggestion_id=suggestion_id,
            combination_id=suggestion.source_combination_id,
            event_type="CREATE_PROFILE_FROM_SUGGESTION",
            event_description=(
                f"Profile '{final_name}' criado a partir de sugestão — "
                f"SHADOW_ONLY, live_trading_enabled=False"
            ),
            payload_json={
                "suggestion_id": str(suggestion_id),
                "source_combination_id": str(suggestion.source_combination_id) if suggestion.source_combination_id else None,
                "requested_profile_name": final_name,
                "mode": mode,
                "dry_run": False,
                "overfit_risk": overfit_risk,
                "confidence_level": suggestion.confidence_level,
                "confirm_low_confidence": confirm_low_confidence,
                "confirm_overfit_risk": confirm_overfit_risk,
            },
            result_json={
                "created_profile_id": str(profile_id),
                "created_master_rules": [r["id"] for r in created_master_rules],
                "reused_master_rules": [r["id"] for r in reused_master_rules],
                "selected_rule_ids": selected_rule_ids,
                "warnings": warnings,
                "blocked_reasons": [],
                "is_shadow_only": True,
                "live_trading_enabled": False,
            },
        )
        db.add(pi_audit)
        await db.flush()

        pi_audit_id = pi_audit.id

        # ── 13. Update suggestion status ──────────────────────────────────────
        suggestion.status = "applied"
        suggestion.created_profile_id = profile_id
        suggestion.applied_at = now
        suggestion.reason = "human_created_shadow_profile"
        suggestion.updated_at = now
        await db.flush()

        # ── 14. Watchlist assignment (optional) ───────────────────────────────
        if assign_to_watchlist_id:
            try:
                await _assign_to_watchlist(db, user_id, profile_id, assign_to_watchlist_id)
            except Exception as exc:
                warnings.append(f"Watchlist assignment failed (non-critical): {exc}")

        # ── 15. Commit ────────────────────────────────────────────────────────
        await db.commit()

        logger.info(
            "[ProfileCreate] profile=%s suggestion=%s user=%s is_shadow_only=True live=False",
            profile_id, suggestion_id, user_id,
        )

        return {
            "status": "created",
            "profile_id": str(profile_id),
            "profile_name": final_name,
            "profile_url": f"/profiles/{profile_id}",
            "created_master_rules": created_master_rules,
            "reused_master_rules": reused_master_rules,
            "selected_rule_ids": selected_rule_ids,
            "created_signals": profile_config["signals"]["conditions"],
            "created_scoring": profile_config["scoring"],
            "created_block_rules": profile_config["block_rules"]["blocks"],
            "audit_id": str(pi_audit_id),
            "is_shadow_only": True,
            "live_trading_enabled": False,
            "warnings": warnings,
        }


async def _assign_to_watchlist(
    db: AsyncSession, user_id: UUID, profile_id: UUID, watchlist_id: str
) -> None:
    """Assign the new profile as L3 to a watchlist (best-effort)."""
    from ..models.profile import WatchlistProfile
    existing = (await db.execute(
        select(WatchlistProfile).where(
            WatchlistProfile.user_id == user_id,
            WatchlistProfile.watchlist_id == watchlist_id,
            WatchlistProfile.profile_type == "L3",
        )
    )).scalars().first()
    if not existing:
        wp = WatchlistProfile(
            user_id=user_id,
            watchlist_id=watchlist_id,
            profile_type="L3",
            profile_id=profile_id,
            is_enabled=True,
        )
        db.add(wp)
        await db.flush()
