"""Ingestion, point-in-time lookup, and deterministic Social Score modifier."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.pool import PoolCoin
from ..models.social_intelligence import SocialAssetObservation, SocialIntelligenceRun
from ..schemas.social_intelligence import SocialAssetInput, SocialRunInput, SocialScoreConfig
from .config_service import config_service
from .pool_service import normalize_pool_symbol


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_social_symbol(symbol: str) -> str:
    """Normalize an agent symbol to the canonical Gate.io ``BASE_USDT`` pair."""

    cleaned = symbol.upper().strip().replace("/", "_").replace("-", "_")
    if not cleaned:
        return ""
    normalized = normalize_pool_symbol(cleaned)
    if "_" not in normalized:
        normalized = f"{normalized}_USDT"
    return normalized


def payload_hash(payload: SocialRunInput) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validation_reasons(exc: ValidationError) -> list[str]:
    reasons: list[str] = []
    for error in exc.errors(include_url=False):
        path = ".".join(str(item) for item in error.get("loc") or ())
        message = str(error.get("msg") or "invalid value")
        reasons.append(f"{path}: {message}" if path else message)
    return reasons


async def ingest_social_run(
    db: AsyncSession,
    payload: SocialRunInput,
) -> tuple[SocialIntelligenceRun, list[str], list[dict[str, Any]], bool]:
    """Persist one immutable run, accepting valid assets independently.

    Returns ``(run, accepted_symbols, rejected_items, duplicate)``. A retry
    with the same payload is idempotent; reusing the external id with a
    different payload raises ``ValueError``.
    """

    now = datetime.now(timezone.utc)
    if _utc(payload.collected_at) > now:
        raise ValueError("collected_at must not be in the future")

    digest = payload_hash(payload)
    existing = (
        await db.execute(
            select(SocialIntelligenceRun).where(
                SocialIntelligenceRun.source == payload.source,
                SocialIntelligenceRun.external_run_id == payload.external_run_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.payload_hash != digest:
            raise ValueError("external_run_id already exists with a different payload")
        accepted = list(
            (
                await db.execute(
                    select(SocialAssetObservation.symbol)
                    .where(SocialAssetObservation.run_id == existing.id)
                    .order_by(SocialAssetObservation.symbol)
                )
            ).scalars()
        )
        return existing, accepted, list(existing.validation_errors or []), True

    raw_symbols = {
        normalize_social_symbol(str(item.get("symbol") or ""))
        for item in payload.assets
        if isinstance(item, Mapping) and item.get("symbol")
    }
    known_symbols = set()
    if raw_symbols:
        known_symbols = set(
            (
                await db.execute(
                    select(PoolCoin.symbol).where(PoolCoin.symbol.in_(raw_symbols))
                )
            ).scalars()
        )
        known_symbols = {normalize_social_symbol(value) for value in known_symbols}

    accepted_inputs: list[tuple[str, SocialAssetInput]] = []
    rejected: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for index, raw_asset in enumerate(payload.assets):
        raw_symbol = raw_asset.get("symbol") if isinstance(raw_asset, Mapping) else None
        try:
            parsed = SocialAssetInput.model_validate(raw_asset)
        except ValidationError as exc:
            rejected.append({
                "index": index,
                "symbol": str(raw_symbol) if raw_symbol else None,
                "reasons": _validation_reasons(exc),
            })
            continue

        symbol = normalize_social_symbol(parsed.symbol)
        reasons: list[str] = []
        if symbol not in known_symbols:
            reasons.append("symbol is not registered in pool_coins")
        if symbol in seen_symbols:
            reasons.append("duplicate symbol in the same run")
        if reasons:
            rejected.append({"index": index, "symbol": symbol, "reasons": reasons})
            continue
        seen_symbols.add(symbol)
        accepted_inputs.append((symbol, parsed))

    status = "ACCEPTED" if not rejected else "PARTIAL" if accepted_inputs else "REJECTED"
    run = SocialIntelligenceRun(
        contract_version=payload.contract_version,
        external_run_id=payload.external_run_id,
        source=payload.source,
        model=payload.model,
        prompt_version=payload.prompt_version,
        window_start=_utc(payload.window_start),
        window_end=_utc(payload.window_end),
        collected_at=_utc(payload.collected_at),
        payload_hash=digest,
        status=status,
        accepted_count=len(accepted_inputs),
        rejected_count=len(rejected),
        validation_errors=rejected,
    )
    db.add(run)
    await db.flush()

    for symbol, parsed in accepted_inputs:
        db.add(SocialAssetObservation(
            run_id=run.id,
            symbol=symbol,
            attention_score=parsed.attention_score,
            sentiment_score=parsed.sentiment_score,
            confidence=parsed.confidence,
            sentiment_label=parsed.sentiment_label,
            recommendation=parsed.recommendation,
            summary=parsed.summary,
            narratives=parsed.narratives,
            anomalies=parsed.anomalies,
            metrics=parsed.metrics,
            sources=[source.model_dump(mode="json") for source in parsed.sources],
            contract_version=payload.contract_version,
            window_start=_utc(payload.window_start),
            window_end=_utc(payload.window_end),
            collected_at=_utc(payload.collected_at),
        ))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced = (
            await db.execute(
                select(SocialIntelligenceRun).where(
                    SocialIntelligenceRun.source == payload.source,
                    SocialIntelligenceRun.external_run_id == payload.external_run_id,
                )
            )
        ).scalar_one_or_none()
        if raced is None or raced.payload_hash != digest:
            raise
        accepted = list(
            (
                await db.execute(
                    select(SocialAssetObservation.symbol)
                    .where(SocialAssetObservation.run_id == raced.id)
                    .order_by(SocialAssetObservation.symbol)
                )
            ).scalars()
        )
        return raced, accepted, list(raced.validation_errors or []), True

    return run, sorted(symbol for symbol, _ in accepted_inputs), rejected, False


async def latest_observations(
    db: AsyncSession,
    symbols: Iterable[str],
    *,
    as_of: Optional[datetime] = None,
) -> dict[str, SocialAssetObservation]:
    """Return the deterministic latest observation at or before ``as_of``."""

    canonical = sorted({normalize_social_symbol(symbol) for symbol in symbols if symbol})
    if not canonical:
        return {}
    cutoff = _utc(as_of or datetime.now(timezone.utc))
    rows = (
        await db.execute(
            select(SocialAssetObservation)
            .where(
                SocialAssetObservation.symbol.in_(canonical),
                SocialAssetObservation.window_end <= cutoff,
                SocialAssetObservation.collected_at <= cutoff,
            )
            .order_by(
                SocialAssetObservation.symbol,
                SocialAssetObservation.window_end.desc(),
                SocialAssetObservation.collected_at.desc(),
                SocialAssetObservation.id.desc(),
            )
            .distinct(SocialAssetObservation.symbol)
        )
    ).scalars()
    return {row.symbol: row for row in rows}


def serialize_observation(
    observation: SocialAssetObservation,
    *,
    as_of: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    age_seconds = max(0.0, (_utc(as_of) - _utc(observation.window_end)).total_seconds())
    return {
        "id": str(observation.id),
        "run_id": str(observation.run_id),
        "symbol": observation.symbol,
        "attention_score": float(observation.attention_score),
        "sentiment_score": float(observation.sentiment_score),
        "confidence": float(observation.confidence),
        "sentiment_label": observation.sentiment_label,
        "recommendation": observation.recommendation,
        "summary": observation.summary,
        "narratives": list(observation.narratives or []),
        "anomalies": list(observation.anomalies or []),
        "metrics": dict(observation.metrics or {}),
        "sources": list(observation.sources or []),
        "contract_version": observation.contract_version,
        "window_start": _utc(observation.window_start).isoformat(),
        "window_end": _utc(observation.window_end).isoformat(),
        "collected_at": _utc(observation.collected_at).isoformat(),
        "age_seconds": age_seconds,
        "eligible": age_seconds <= max_age_seconds,
    }


async def attach_social_context(
    db: AsyncSession,
    assets: list[dict[str, Any]],
    *,
    user_id,
    is_futures: bool,
    as_of: Optional[datetime] = None,
) -> SocialScoreConfig:
    """Attach social lineage to asset snapshots without changing their scores."""

    now = _utc(as_of or datetime.now(timezone.utc))
    raw_config = await config_service.get_config(db, "social_score", user_id)
    config = SocialScoreConfig.model_validate(raw_config or {})
    observations = await latest_observations(
        db,
        [str(asset.get("symbol") or "") for asset in assets],
        as_of=now,
    )
    weight = config.futures_weight if is_futures else config.spot_weight

    for asset in assets:
        symbol = normalize_social_symbol(str(asset.get("symbol") or ""))
        observation = observations.get(symbol)
        snapshot = dict(asset.get("analysis_snapshot") or {})
        if observation is None:
            context: dict[str, Any] = {
                "enabled": config.enabled,
                "eligible": False,
                "applied": False,
                "fallback_reason": "no_observation",
                "weight": weight,
                "max_age_seconds": config.max_age_seconds,
                "formula_version": config.formula_version,
            }
        else:
            context = serialize_observation(
                observation,
                as_of=now,
                max_age_seconds=config.max_age_seconds,
            )
            context.update({
                "enabled": config.enabled,
                "applied": False,
                "fallback_reason": None if context["eligible"] else "stale",
                "weight": weight,
                "max_age_seconds": config.max_age_seconds,
                "formula_version": config.formula_version,
            })
        asset["_social_score"] = context
        snapshot["social_score"] = context
        asset["analysis_snapshot"] = snapshot
    return config


def confidence_adjusted_sentiment(sentiment_score: float, confidence: float) -> float:
    return max(0.0, min(100.0, 50.0 + confidence * (sentiment_score - 50.0)))


def blend_score(technical_score: float, social_score: float, weight: float) -> float:
    return max(0.0, min(100.0, (1.0 - weight) * technical_score + weight * social_score))


def apply_social_score_to_asset(
    asset: dict[str, Any],
    *,
    is_futures: bool,
    technical_threshold: float,
) -> dict[str, Any]:
    """Apply the modifier only after technical/profile eligibility passed."""

    context = dict(asset.get("_social_score") or {})
    has_context = bool(context)
    technical_score = float(asset.get("_technical_score", asset.get("_score") or 0.0))
    context["applied"] = False
    context["technical_score"] = technical_score
    context["final_score"] = technical_score

    if not has_context:
        context["fallback_reason"] = "context_unavailable"
    elif not context.get("enabled"):
        context["fallback_reason"] = "config_disabled"
    elif not context.get("eligible"):
        context["fallback_reason"] = context.get("fallback_reason") or "no_observation"
    elif technical_score < technical_threshold:
        context["fallback_reason"] = "technical_gate_failed"
    else:
        adjusted = confidence_adjusted_sentiment(
            float(context["sentiment_score"]),
            float(context["confidence"]),
        )
        weight = float(context["weight"])
        context["sentiment_adjusted"] = adjusted
        context["applied"] = True
        context["fallback_reason"] = None

        if is_futures:
            technical_long = float(asset.get("_technical_score_long", asset.get("score_long") or 0.0))
            technical_short = float(asset.get("_technical_score_short", asset.get("score_short") or 0.0))
            final_long = (
                blend_score(technical_long, adjusted, weight)
                if technical_long >= technical_threshold else technical_long
            )
            final_short = (
                blend_score(technical_short, 100.0 - adjusted, weight)
                if technical_short >= technical_threshold else technical_short
            )
            asset["score_long"] = round(final_long, 2)
            asset["score_short"] = round(final_short, 2)
            direction = str(asset.get("futures_direction") or "NEUTRAL").upper()
            final_score = final_long if direction == "LONG" else final_short if direction == "SHORT" else technical_score
            asset["confidence_score"] = round(final_score, 2)
            context.update({
                "technical_score_long": technical_long,
                "technical_score_short": technical_short,
                "final_score_long": final_long,
                "final_score_short": final_short,
                "direction": direction,
            })
        else:
            final_score = blend_score(technical_score, adjusted, weight)

        asset["_score"] = round(final_score, 4)
        asset["score"] = round(final_score, 4)
        asset["alpha_score"] = round(final_score, 4)
        context["final_score"] = final_score

    asset["_social_score"] = context
    snapshot = dict(asset.get("analysis_snapshot") or {})
    snapshot["social_score"] = context
    snapshot["technical_score"] = technical_score
    snapshot["final_score"] = context["final_score"]
    asset["analysis_snapshot"] = snapshot
    return context
