"""Fase 1 — Bloco D: job de certificação de integridade do dataset ML.

Roda a cada 2h (beat: ``crontab(minute=0, hour="*/2")``) a certificação do
Bloco C sobre a janela sobreposta de 26h, persiste uma linha em
``ml_data_certification_runs`` e alerta pelo canal D2 (LOG_ONLY).

Regras vinculantes (PROMPT_FASE1, Bloco D):
- Isolado dos workers de captura: roda na fila structural_compute (o worker
  dedicado de análise pesada), nunca em microstructure/structural/execution.
  Falha aqui não afeta a captura — a task só lê shadow_trades/decisions_log
  e escreve exclusivamente em ml_data_certification_runs.
- Janela [now-26h, now] sobreposta de propósito: nenhuma linha escapa entre
  execuções de 2h.
- I09 (piso de geração) é FAIL na execução do job.
- Idempotência de alerta: assinatura repetida na mesma janela não duplica
  o alerta (implementada em run_certification).
"""

from __future__ import annotations

import asyncio
import logging

from .celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async coroutine em task Celery síncrono.

    Mesma lógica de teardown de 5 passos do shadow_trade_monitor
    (Task #274 canonical pattern).
    """
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
            logger.debug("[ml-certification] pending-task drain: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[ml-certification] engine dispose: %s", exc)

        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                asyncpg_conn = (
                    getattr(raw, "_connection", None)
                    or getattr(raw, "connection", None)
                    or raw
                )
                terminate = getattr(asyncpg_conn, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except BaseException:
                        pass
        except BaseException as exc:
            logger.debug("[ml-certification] hard-terminate: %s", exc)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[ml-certification] shutdown_asyncgens: %s", exc)

        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[ml-certification] loop.close: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _run() -> dict:
    from ..database import get_celery_session
    from ..services.ml_data_certification_service import run_certification

    async with get_celery_session() as db:
        return await run_certification(db, persist=True, i09_informative=False)


@celery_app.task(name="app.tasks.ml_data_certification.run")
def run() -> None:
    try:
        result = _run_async(_run())
        logger.info(
            "[ml-certification] run status=%s failed=%s run_id=%s",
            result.get("status"), result.get("failed"), result.get("run_id"),
        )
    except Exception:
        # Falha do job NUNCA pode afetar a captura: não re-raise para o beat
        # não acumular retries; o erro fica no log do worker.
        logger.exception("[ml-certification] execução falhou")
