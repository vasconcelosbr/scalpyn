"""Authenticated Social Intelligence ingestion and read APIs."""

import hmac
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.social_intelligence import SocialAssetObservation
from ..schemas.social_intelligence import SocialIngestionResponse, SocialRunInput, SocialScoreConfig
from ..services.config_service import config_service
from ..services.social_intelligence_service import (
    ingest_social_run,
    latest_observations,
    normalize_social_symbol,
    serialize_observation,
)
from .config import get_current_user_id

router = APIRouter(prefix="/api/social-intelligence", tags=["Social Intelligence"])


def _enforce_ingest_token(authorization: Optional[str]) -> None:
    expected = settings.SOCIAL_INTELLIGENCE_INGEST_TOKEN.strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    prefix = "Bearer "
    presented = (
        authorization[len(prefix):].strip()
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/runs", response_model=SocialIngestionResponse)
async def create_social_run(
    payload: SocialRunInput,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    _enforce_ingest_token(authorization)
    try:
        run, accepted, rejected, duplicate = await ingest_social_run(db, payload)
    except ValueError as exc:
        message = str(exc)
        code = status.HTTP_409_CONFLICT if "already exists" in message else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=message) from exc
    return {
        "run_id": run.id,
        "status": "DUPLICATE" if duplicate else run.status,
        "accepted_symbols": accepted,
        "rejected_items": rejected,
        "payload_hash": run.payload_hash,
    }


@router.get("/latest")
async def get_latest_social_observations(
    symbols: str = Query(min_length=1, description="Comma-separated symbols"),
    as_of: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    user_id=Depends(get_current_user_id),
):
    cutoff = as_of or datetime.now(timezone.utc)
    requested = [normalize_social_symbol(item) for item in symbols.split(",") if item.strip()]
    config = SocialScoreConfig.model_validate(
        await config_service.get_config(db, "social_score", user_id) or {}
    )
    rows = await latest_observations(db, requested, as_of=cutoff)
    return {
        "as_of": cutoff,
        "config": config.model_dump(),
        "items": [
            serialize_observation(row, as_of=cutoff, max_age_seconds=config.max_age_seconds)
            for symbol in requested
            if (row := rows.get(symbol)) is not None
        ],
        "missing_symbols": [symbol for symbol in requested if symbol not in rows],
    }


@router.get("/{symbol}/history")
async def get_social_observation_history(
    symbol: str,
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id=Depends(get_current_user_id),
):
    canonical = normalize_social_symbol(symbol)
    config = SocialScoreConfig.model_validate(
        await config_service.get_config(db, "social_score", user_id) or {}
    )
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(SocialAssetObservation)
            .where(SocialAssetObservation.symbol == canonical)
            .order_by(
                SocialAssetObservation.window_end.desc(),
                SocialAssetObservation.collected_at.desc(),
                SocialAssetObservation.id.desc(),
            )
            .limit(limit)
        )
    ).scalars()
    return {
        "symbol": canonical,
        "items": [
            serialize_observation(row, as_of=now, max_age_seconds=config.max_age_seconds)
            for row in rows
        ],
    }
