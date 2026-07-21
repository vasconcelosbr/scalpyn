"""Fail-closed AI review for validated Profile Intelligence indicator adjustments."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile import Profile
from ..models.profile_intelligence import ProfileIndicatorStats
from .ai_keys_service import get_decrypted_api_key


def _strip_json(raw: str) -> str:
    value = raw.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1])
    return value.strip()


async def review_indicator_adjustment(
    db: AsyncSession,
    *,
    user_id: UUID,
    indicator_stat: ProfileIndicatorStats,
    profiles: list[Profile],
) -> dict[str, Any]:
    """Ask AI to approve/reject a bounded shadow-only adjustment; never mutate a profile."""
    if indicator_stat.validation_status != "validated":
        raise ValueError(f"indicator_not_temporally_validated:{indicator_stat.validation_status}")
    if indicator_stat.role_detected not in {"winning_indicator", "losing_indicator"}:
        raise ValueError("indicator_role_not_adjustable")
    if not profiles:
        raise ValueError("indicator_review_requires_profile")

    action = (
        "REPLACE_SIGNAL_CONDITION"
        if indicator_stat.role_detected == "winning_indicator"
        else "ADD_SCORE_PENALTY"
    )
    evidence = dict(indicator_stat.evidence_json or {})
    context = {
        "indicator_stat_id": str(indicator_stat.id),
        "run_id": str(indicator_stat.run_id),
        "dataset_version": evidence.get("dataset_version"),
        "label_version": evidence.get("label_version"),
        "indicator": indicator_stat.indicator,
        "bucket": indicator_stat.bucket_label,
        "role": indicator_stat.role_detected,
        "bounded_action": action,
        "discovery": {
            "cases": indicator_stat.total_cases,
            "win_rate": float(indicator_stat.win_rate or 0),
            "avg_pnl_pct": float(indicator_stat.avg_pnl_pct or 0),
            "lift": float(indicator_stat.lift_vs_base or 0),
        },
        "validation": evidence.get("validation"),
        "profiles": [
            {
                "id": str(profile.id),
                "name": profile.name,
                "signals": (profile.config or {}).get("signals"),
                "scoring": (profile.config or {}).get("scoring"),
            }
            for profile in profiles
        ],
        "safety": {
            "shadow_only": True,
            "incumbent_mutated": False,
            "training_dataset_mutated": False,
            "training_or_promotion_allowed": False,
        },
    }
    context_hash = hashlib.sha256(
        json.dumps(context, sort_keys=True, default=str).encode()
    ).hexdigest()

    api_key = await get_decrypted_api_key(db, user_id, "anthropic")
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("anthropic_key_not_configured")

    import anthropic  # type: ignore

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=os.environ.get("PI_AI_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=700,
            messages=[{
                "role": "user",
                "content": (
                    "Review this temporally validated indicator adjustment. "
                    "Approve only if the validation evidence supports the bounded action and it does not "
                    "weaken the current profile contract. You cannot change parameters, train/promote a model, "
                    "or mutate an incumbent. Return JSON only with verdict APPROVE_SHADOW or REJECT, "
                    "bounded_action exactly as supplied, rationale, risks, and safeguards.\n\n"
                    + json.dumps(context, default=str)
                ),
            }],
        )
        raw = response.content[0].text if response.content else ""
        parsed = json.loads(_strip_json(raw))
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise ValueError("invalid_ai_indicator_review_response") from exc
    finally:
        await client.close()

    verdict = str(parsed.get("verdict", "")).upper()
    returned_action = str(parsed.get("bounded_action", ""))
    if verdict not in {"APPROVE_SHADOW", "REJECT"} or returned_action != action:
        raise ValueError("invalid_ai_indicator_review_contract")
    if not str(parsed.get("rationale", "")).strip():
        raise ValueError("invalid_ai_indicator_review_rationale")

    review = {
        "verdict": verdict,
        "bounded_action": action,
        "rationale": str(parsed["rationale"]),
        "risks": parsed.get("risks") or [],
        "safeguards": parsed.get("safeguards") or [],
        "context_hash": context_hash,
        "model": getattr(response, "model", None),
        "profile_ids": [str(profile.id) for profile in profiles],
        "profile_config_hashes": {
            str(profile.id): hashlib.sha256(
                json.dumps(profile.config or {}, sort_keys=True, default=str).encode()
            ).hexdigest()
            for profile in profiles
        },
        "incumbent_mutated": False,
        "training_dataset_mutated": False,
    }
    evidence["ai_review"] = review
    indicator_stat.evidence_json = evidence
    indicator_stat.actionability_status = (
        "validated" if verdict == "APPROVE_SHADOW" else "ai_rejected"
    )
    return review
