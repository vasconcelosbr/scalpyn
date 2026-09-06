from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from uuid import UUID

import jwt as pyjwt

from ..config import settings
from ..database import get_db
from ..models.social_intelligence import SocialAssetObservation
from ..services.config_service import config_service
from ..services.crypto_ev_config import default_crypto_ev_config
from ..schemas.social_intelligence import SocialScoreConfig
from ..schemas.ai_provider_runtime_config import AIProviderRuntimeConfig
from ..schemas.analysis_chat import AnalysisChatRuntimeConfig
from ..schemas.entry_risk_observation import EntryRiskObservationConfig

security = HTTPBearer()

async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UUID:
    token = credentials.credentials
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        return UUID(payload["sub"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

router = APIRouter(prefix="/api/config", tags=["Configuration"])


@router.get("/flags", include_in_schema=False)
async def get_feature_flags() -> Dict[str, Any]:
    """Public feature-flag bundle for the frontend.

    Task #316 — UI consulta este endpoint para decidir se renderiza o
    painel Entry | Exit lado-a-lado. Sem auth: nenhum dado sensível
    (apenas booleanos de feature). Mantido como ``include_in_schema=False``
    para não poluir o OpenAPI público.
    """
    return {
        "exit_metrics_ui": bool(settings.ENABLE_EXIT_METRICS_UI),
        "exit_metrics_capture": bool(settings.ENABLE_EXIT_METRICS_CAPTURE),
        "decision_snapshots": bool(settings.ENABLE_DECISION_SNAPSHOTS),
        "signal_timeline": bool(settings.ENABLE_SIGNAL_TIMELINE),
    }

_GONE_DETAIL = (
    "The 'signal' config_type has been retired. "
    "Entry conditions are now managed under 'block' (entry_triggers). "
    "Use GET/PUT /api/config/block instead."
)


@router.get("/signal")
@router.put("/signal")
async def signal_config_gone():
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=_GONE_DETAIL,
    )


@router.post("/crypto_ev/reset")
async def reset_crypto_ev_config(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    data = default_crypto_ev_config()
    updated = await config_service.update_config(
        db=db,
        config_type="crypto_ev",
        user_id=user_id,
        new_json=data,
        changed_by=user_id,
        change_description="Reset crypto_ev config to default schema",
    )
    return {"status": "success", "config_type": "crypto_ev", "data": updated}


@router.get("/{config_type}")
async def get_config(
    config_type: str,
    pool_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    config = await config_service.get_config(db, config_type, user_id, pool_id)
    if config_type == "shadow_l3_exit_policy":
        from ..schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy
        config = ShadowL3ExitPolicy.model_validate(config).model_dump()
    return {"config_type": config_type, "pool_id": pool_id, "data": config}


@router.get("/shadow_l3_exit_policy/metadata")
async def shadow_l3_policy_metadata(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    from ..schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy
    from sqlalchemy import text
    raw = await config_service.get_config(db, "shadow_l3_exit_policy", user_id)
    policy = ShadowL3ExitPolicy.model_validate(raw)
    spot = await config_service.get_config(db, "spot_engine", user_id)
    approved = (await db.execute(text("""
        SELECT approved_at FROM shadow_l3_policy_validations
        WHERE user_id=:uid AND policy_hash=:hash AND approved_by=:uid
          AND approved_at IS NOT NULL AND report->>'decision'='PASS'
    """), {"uid":user_id,"hash":policy.digest()})).scalar_one_or_none()
    return {"schema":ShadowL3ExitPolicy.model_json_schema(), "hash":policy.digest(),
            "missing_parameters":policy.missing_parameters(), "approved":bool(approved),
            "validation_status":"VALIDATED" if approved else "NOT_CALIBRATED",
            "pre_tp":{"trailing":(spot.get("sell_flow") or {}).get("trailing"),"selling":spot.get("selling")}}

@router.put("/{config_type}")
async def update_config(
    config_type: str,
    payload: Dict[str, Any],
    pool_id: Optional[UUID] = None,
    change_description: str = "Updated via API",
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    if config_type in {"score", "signal", "block"}:
        from ..services.entry_risk_features import (
            assert_no_observational_execution_fields,
        )

        try:
            assert_no_observational_execution_fields(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    if config_type == "social_score":
        validated = SocialScoreConfig.model_validate(payload)
        if validated.enabled:
            now = datetime.now(timezone.utc)
            oldest_allowed = now - timedelta(seconds=validated.max_age_seconds)
            fresh_observation_id = (
                await db.execute(
                    select(SocialAssetObservation.id)
                    .where(
                        SocialAssetObservation.window_end >= oldest_allowed,
                        SocialAssetObservation.window_end <= now,
                        SocialAssetObservation.collected_at <= now,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if fresh_observation_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Social Score cannot be enabled without a fresh reconciled observation",
                )
        payload = validated.model_dump()
    elif config_type == "shadow_l3_exit_policy":
        from ..schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy
        from pydantic import ValidationError
        try:
            payload = ShadowL3ExitPolicy.model_validate(payload).model_dump()
            await config_service.validate_shadow_l3_policy(db, payload, user_id, pool_id)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif config_type == "ai_provider_runtime":
        payload = AIProviderRuntimeConfig.model_validate(payload).model_dump(mode="json")
    elif config_type == "ai_analysis_chat_runtime":
        # JSONB cannot serialize Decimal directly.  Pydantic's JSON mode keeps
        # the persisted config canonical and makes runtime flag updates
        # reversible through this endpoint.
        payload = AnalysisChatRuntimeConfig.model_validate(payload).model_dump(mode="json")
    elif config_type == "entry_risk_observation":
        # v1 is observation-only by construction.  Literal[False] rejects an
        # attempted operational activation instead of silently accepting it.
        payload = EntryRiskObservationConfig.model_validate(payload).model_dump(mode="json")
    updated = await config_service.update_config(
        db=db,
        config_type=config_type,
        user_id=user_id,
        new_json=payload,
        changed_by=user_id,
        pool_id=pool_id,
        change_description=change_description
    )
    return {"status": "success", "config_type": config_type, "data": updated}
