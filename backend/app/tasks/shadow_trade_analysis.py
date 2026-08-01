"""Celery task for durable Shadow Trade AI analyses."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from .celery_app import celery_app
from ..database import run_db_task
from ..models.shadow_trade import ShadowTrade
from ..models.shadow_trade_analysis import (
    ShadowTradeAnalysisJob,
    ShadowTradeReportItem,
    ShadowTradeReportRun,
)
from ..models.ai_provider_key import AIProviderKey
from ..services.ai_keys_service import get_decrypted_api_key
from ..services.shadow_trade_analysis_service import analyze_trade_documents


async def _load_job_input(db, job_id: UUID):
    job = (
        await db.execute(
            select(ShadowTradeAnalysisJob).where(ShadowTradeAnalysisJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise LookupError("Shadow analysis job not found")
    if job.status == "COMPLETED":
        return None
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)

    if job.scope == "TRADE":
        trade = (
            await db.execute(
                select(ShadowTrade).where(
                    ShadowTrade.id == job.shadow_trade_id,
                    ShadowTrade.user_id == job.user_id,
                )
            )
        ).scalar_one_or_none()
        if trade is None:
            raise LookupError("Shadow trade not found")
        documents = [_compact_trade(trade)]
        selection = {"scope": "TRADE", "trade_id": str(trade.id)}
    else:
        run = (
            await db.execute(
                select(ShadowTradeReportRun).where(
                    ShadowTradeReportRun.id == job.report_run_id,
                    ShadowTradeReportRun.user_id == job.user_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            raise LookupError("Detailed report run not found")
        trades = (
            await db.execute(
                select(ShadowTrade)
                .join(ShadowTradeReportItem, ShadowTradeReportItem.shadow_trade_id == ShadowTrade.id)
                .where(ShadowTradeReportItem.report_run_id == run.id)
                .order_by(ShadowTradeReportItem.position)
            )
        ).scalars().all()
        documents = [_compact_trade(trade) for trade in trades]
        selection = {
            "scope": "REPORT",
            "report_run_id": str(run.id),
            "filters": run.filters,
            "trade_ids_hash": run.trade_ids_hash,
        }
    api_key = await get_decrypted_api_key(db, job.user_id, job.provider)
    if not api_key:
        raise ValueError("Configured AI key could not be loaded")
    return {
        "user_id": job.user_id,
        "provider": job.provider,
        "model": job.model,
        "api_key": api_key,
        "documents": documents,
        "selection": selection,
    }


def _compact_trade(trade: ShadowTrade) -> dict:
    return {
        "trade_id": str(trade.id),
        "symbol": trade.symbol,
        "source": trade.source,
        "watchlist_id": str(trade.watchlist_id) if trade.watchlist_id else None,
        "profile_id": str(trade.profile_id) if trade.profile_id else None,
        "profile_name": trade.profile_name,
        "profile_version": trade.profile_version,
        "profile_config_hash": trade.profile_config_hash,
        "outcome": trade.outcome,
        "entry_timestamp": trade.entry_timestamp,
        "exit_timestamp": trade.exit_timestamp,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "tp_price": trade.tp_price,
        "sl_price": trade.sl_price,
        "pnl_pct": trade.pnl_pct,
        "pnl_usdt": trade.pnl_usdt,
        "mae_pct": trade.mae_pct,
        "mfe_pct": trade.mfe_pct,
        "holding_seconds": trade.holding_seconds,
        "config_snapshot": trade.config_snapshot,
        "rules_snapshot": trade.rules_snapshot,
        "indicators_at_entry": trade.features_snapshot,
        "indicators_at_exit": trade.features_snapshot_exit,
        "exit_metrics": trade.exit_metrics_json,
        "lineage_status": trade.lineage_status,
    }


def _count_usage_tokens(usage: dict) -> int:
    total = 0
    for provider_call in usage.get("provider_calls") or []:
        explicit_total = provider_call.get("total_tokens") or provider_call.get("totalTokenCount")
        if explicit_total is not None:
            total += int(explicit_total)
            continue
        input_tokens = provider_call.get("input_tokens") or provider_call.get("prompt_tokens") or provider_call.get("promptTokenCount") or 0
        output_tokens = provider_call.get("output_tokens") or provider_call.get("completion_tokens") or provider_call.get("candidatesTokenCount") or 0
        total += int(input_tokens) + int(output_tokens)
    return total


async def _store_success(db, job_id: UUID, result, raw, usage):
    job = (
        await db.execute(
            select(ShadowTradeAnalysisJob).where(ShadowTradeAnalysisJob.id == job_id).with_for_update()
        )
    ).scalar_one()
    job.status = "COMPLETED"
    job.result_json = result
    job.raw_response = raw
    job.usage = usage
    job.error = None
    job.completed_at = datetime.now(timezone.utc)
    key = (
        await db.execute(
            select(AIProviderKey).where(
                AIProviderKey.user_id == job.user_id,
                AIProviderKey.provider == job.provider,
                AIProviderKey.is_active.is_(True),
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if key is not None:
        key.tokens_used_month = int(key.tokens_used_month or 0) + _count_usage_tokens(usage)
        key.last_used_at = datetime.now(timezone.utc)


async def _store_failure(db, job_id: UUID, error: str):
    job = (
        await db.execute(
            select(ShadowTradeAnalysisJob).where(ShadowTradeAnalysisJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if job:
        job.status = "FAILED"
        job.error = error[:8000]
        job.completed_at = datetime.now(timezone.utc)


@celery_app.task(name="app.tasks.shadow_trade_analysis.run")
def run(job_id: str) -> dict:
    async def _run():
        try:
            payload = await run_db_task(lambda db: _load_job_input(db, UUID(job_id)), celery=True)
            if payload is None:
                return {"status": "COMPLETED", "job_id": job_id}
            result, raw, usage = await analyze_trade_documents(
                provider=payload["provider"],
                api_key=payload["api_key"],
                model=payload["model"],
                documents=payload["documents"],
                selection=payload["selection"],
            )
            await run_db_task(lambda db: _store_success(db, UUID(job_id), result, raw, usage), celery=True)
            return {"status": "COMPLETED", "job_id": job_id}
        except Exception as exc:
            await run_db_task(lambda db: _store_failure(db, UUID(job_id), f"{type(exc).__name__}: {exc}"), celery=True)
            raise

    return asyncio.run(_run())
