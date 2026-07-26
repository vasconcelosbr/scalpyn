"""Retenção de ``indicator_snapshots`` — nunca existiu antes (auditoria 2026-07-26).

Causa-raiz do crash de disco do Postgres em 2026-07-26 (volume 100% cheio):
``indicator_snapshots`` é escrita a cada ativo, a cada ciclo de scan
(``persist_snapshot`` em pipeline_scan.py), sem NENHUMA rotina de limpeza.
O único consumidor, ``robust_alerts.run`` (Celery beat), só olha os últimos
~90 segundos (janelas sustentadas de no máximo 5min). Linhas mais antigas que
isso não servem a nenhum propósito operacional.

Apaga em lotes (evita uma transação gigante / rajada de WAL num banco que já
crashou uma vez por espaço). Isolado na fila structural_compute — falha aqui
nunca afeta captura/scan.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from .celery_app import celery_app

logger = logging.getLogger(__name__)

# Zero Hardcode (infra, não política de trading): env var com default,
# mesmo padrão de SHADOW_TIMEOUT_CANDLES/LOOKBACK_DAYS já usados no projeto.
# 48h dá folga generosa acima dos ~90s/5min que os alertas realmente usam.
RETENTION_HOURS = int(os.environ.get("INDICATOR_SNAPSHOTS_RETENTION_HOURS", "48"))
BATCH_SIZE = int(os.environ.get("INDICATOR_SNAPSHOTS_PRUNE_BATCH_SIZE", "20000"))
# Teto de segurança por execução — nunca prende o worker indefinidamente
# mesmo se o beat ficar muito tempo sem rodar (backlog grande).
MAX_BATCHES_PER_RUN = int(os.environ.get("INDICATOR_SNAPSHOTS_PRUNE_MAX_BATCHES", "100"))


def _run_async(coro):
    """Mesmo padrão canônico de teardown das outras tasks (Task #274)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:
            logger.debug("[prune-indicator-snapshots] pending-task drain: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[prune-indicator-snapshots] engine dispose: %s", exc)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[prune-indicator-snapshots] shutdown_asyncgens: %s", exc)

        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[prune-indicator-snapshots] loop.close: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _prune() -> dict:
    from ..database import get_celery_session

    total_deleted = 0
    batches = 0
    async with get_celery_session() as db:
        while batches < MAX_BATCHES_PER_RUN:
            result = await db.execute(
                text(
                    f"""
                    DELETE FROM indicator_snapshots
                    WHERE id IN (
                        SELECT id FROM indicator_snapshots
                        WHERE timestamp < now() - interval '{RETENTION_HOURS} hours'
                        LIMIT :batch_size
                    )
                    """
                ),
                {"batch_size": BATCH_SIZE},
            )
            await db.commit()
            batches += 1
            n = result.rowcount or 0
            total_deleted += n
            if n < BATCH_SIZE:
                break
    return {
        "total_deleted": total_deleted,
        "batches": batches,
        "retention_hours": RETENTION_HOURS,
        "hit_batch_cap": batches >= MAX_BATCHES_PER_RUN,
    }


@celery_app.task(name="app.tasks.prune_indicator_snapshots.run")
def run() -> None:
    try:
        result = _run_async(_prune())
        logger.info(
            "[prune-indicator-snapshots] deleted=%s batches=%s retention_hours=%s hit_cap=%s",
            result["total_deleted"], result["batches"],
            result["retention_hours"], result["hit_batch_cap"],
        )
        if result["hit_batch_cap"]:
            logger.warning(
                "[prune-indicator-snapshots] atingiu MAX_BATCHES_PER_RUN=%s — "
                "backlog maior que o esperado, vai continuar na próxima execução",
                MAX_BATCHES_PER_RUN,
            )
    except Exception:
        # Falha aqui NUNCA pode afetar captura/scan — mesma regra das outras
        # tasks de manutenção (ml_data_certification etc.).
        logger.exception("[prune-indicator-snapshots] execução falhou")
