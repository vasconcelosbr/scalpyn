"""Debug / diagnostic endpoints for indicator validation.

These endpoints are intended for development-time inspection and
AI-assisted troubleshooting.  They are protected by the standard
user auth dependency and do NOT perform any DB writes.
"""

from __future__ import annotations

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ..api.config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debug", tags=["debug"])


class MACDValidationRequest(BaseModel):
    close_prices: List[float] = Field(
        ...,
        description="Ordered list of closing prices (oldest first). Minimum: slow + signal candles.",
    )
    fast: int = Field(default=12, ge=2, le=200, description="EMA fast period")
    slow: int = Field(default=26, ge=2, le=500, description="EMA slow period")
    signal: int = Field(default=9, ge=1, le=100, description="Signal-line EMA period")

    @field_validator("close_prices")
    @classmethod
    def prices_not_empty(cls, v: List[float]) -> List[float]:
        if not v:
            raise ValueError("close_prices must not be empty")
        return v

    @field_validator("slow")
    @classmethod
    def slow_gt_fast(cls, v: int, info) -> int:
        fast = info.data.get("fast", 12)
        if v <= fast:
            raise ValueError(f"slow ({v}) must be greater than fast ({fast})")
        return v


@router.post("/macd-validation")
async def macd_histogram_validation(
    body: MACDValidationRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Validate the MACD Histogram for a given close-price series.

    Returns a structured diagnostic report with histogram value,
    momentum direction/strength, consistency status, signal quality,
    and debug details (mean_10, std_10, z_score).
    """
    from ..services.indicator_validity import validate_macd_histogram

    try:
        result = validate_macd_histogram(
            close_prices=body.close_prices,
            fast=body.fast,
            slow=body.slow,
            signal=body.signal,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error in macd_histogram_validation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error during MACD validation",
        ) from exc

    return result
