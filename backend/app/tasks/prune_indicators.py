"""Retenção de ``indicators`` — nunca existiu antes (auditoria 2026-09-18).

``indicators`` (JSONB por symbol/timeframe/scheduler_group/time) é 13GB e 41%
do banco inteiro, crescendo sem limite a cada ciclo dos schedulers de
microstructure/structural, sem NENHUMA rotina de limpeza -- o mesmo padrão de
falha que já causou o crash de disco do Postgres em 2026-07-26 via
``indicator_snapshots`` (ver ``prune_indicator_snapshots.py``).

Antes de definir a janela, mapeado todo consumidor de leitura direta
(``indicator_merge.py``/``indicators_provider.py`` e os diagnósticos em
``admin_diagnostics.py``, ``symbol_health_service.py``, ``shadow_trade_service.py``,
``shadow_trailing_view.py``, ``strategy_settings.py``, ``health_checks.py``):
todos só leem a linha MAIS RECENTE por symbol (``ORDER BY time DESC LIMIT 1``).
O único consumidor de histórico profundo é ``mtf_calibration_service.py``, que
reconstrói o snapshot L1/L2 como estava no momento de cada Shadow Trade
``COMPLETED`` histórico -- feature de proposta (``PROPOSAL_INPUTS_ONLY``,
exige aprovação humana), não trading ao vivo. Confirmado por dado real: a
linha mais antiga de ``indicators`` já é de 85 dias atrás (2026-06-25) --
90 dias de retenção não apaga NADA hoje, só passa a valer daqui a ~5 dias,
e dá folga generosa acima do teto que já existe na prática.

Apaga em lotes por ``ctid`` (a tabela não tem coluna ``id`` nem PK single-
column, só o índice único composto ``time, symbol, timeframe``) -- evita uma
transação gigante / rajada de WAL num banco que já crashou uma vez por
espaço. Isolado na fila structural_compute -- falha aqui nunca afeta
captura/scan.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from .celery_app import celery_app

logger = logging.getLogger(__name__)

# Zero Hardcode (infra, não política de trading): mesmo padrão de
# INDICATOR_SNAPSHOTS_RETENTION_HOURS. 90 dias cobre folgadamente o teto real
# de hoje (~85 dias) e qualquer janela razoável de calibração MTF.
RETENTION_DAYS = int(os.environ.get("INDICATORS_RETENTION_DAYS", "90"))
BATCH_SIZE = int(os.environ.get("INDICATORS_PRUNE_BATCH_SIZE", "20000"))
# Teto de segurança por execução — nunca prende o worker indefinidamente
# mesmo se o beat ficar muito tempo sem rodar (backlog grande).
MAX_BATCHES_PER_RUN = int(os.environ.get("INDICATORS_PRUNE_MAX_BATCHES", "100"))


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
            logger.debug("[prune-indicators] pending-task drain: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[prune-indicators] engine dispose: %s", exc)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[prune-indicators] shutdown_asyncgens: %s", exc)

        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[prune-indicators] loop.close: %s", exc)
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
                    DELETE FROM indicators
                    WHERE ctid IN (
                        SELECT ctid FROM indicators
                        WHERE time < now() - interval '{RETENTION_DAYS} days'
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
        "retention_days": RETENTION_DAYS,
        "hit_batch_cap": batches >= MAX_BATCHES_PER_RUN,
    }


@celery_app.task(name="app.tasks.prune_indicators.run")
def run() -> None:
    try:
        result = _run_async(_prune())
        logger.info(
            "[prune-indicators] deleted=%s batches=%s retention_days=%s hit_cap=%s",
            result["total_deleted"], result["batches"],
            result["retention_days"], result["hit_batch_cap"],
        )
        if result["hit_batch_cap"]:
            logger.warning(
                "[prune-indicators] atingiu MAX_BATCHES_PER_RUN=%s — "
                "backlog maior que o esperado, vai continuar na próxima execução",
                MAX_BATCHES_PER_RUN,
            )
    except Exception:
        # Falha aqui NUNCA pode afetar captura/scan — mesma regra das outras
        # tasks de manutenção (prune_indicator_snapshots, ml_data_certification).
        logger.exception("[prune-indicators] execução falhou")
