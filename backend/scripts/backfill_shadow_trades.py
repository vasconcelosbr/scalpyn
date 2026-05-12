"""One-shot backfill for Shadow Portfolio (Fase 4).

Popula ``shadow_trades`` retroativamente a partir de promoções L3
(``decisions_log.decision='ALLOW' AND direction='up'``) que NUNCA viraram
trade real (``trade_executed IS NULL OR trade_executed=FALSE``) num
range de datas.

Resgata oportunidades históricas que sumiram sem rastro antes da Fase 2
estar em produção. Após rodar, o monitor da Fase 3 pega automaticamente
os shadows criados em ``status='PENDING'`` nos próximos ticks.

Uso
---
    cd backend && python -m scripts.backfill_shadow_trades \\
        --user-id <UUID> --min-date 2026-04-01 --max-date 2026-05-01

    # Pré-visualização sem gravar:
    python -m scripts.backfill_shadow_trades \\
        --user-id <UUID> --min-date 2026-04-01 --max-date 2026-05-01 --dry-run

Como funciona
-------------
1. Carrega o ``ConfigProfile`` ativo (``config_type='spot_engine'``) do
   usuário pra extrair ``tp_pct`` (= ``selling.take_profit_pct``) e
   ``sl_pct`` (proxy = ``sell_flow.kill_switch.max_drawdown_from_hwm_pct``,
   alinhado com ``execute_buy.py``).
2. Enumera promoções elegíveis no range, com LEFT JOIN em
   ``shadow_trades`` pra pular o que já existe (idempotência forte:
   UNIQUE INDEX em ``shadow_trades.decision_id`` da migration 047
   também cobre dupla execução).
3. Para cada promoção:
   * Pré-checa cobertura OHLCV 1m após ``decision.created_at``.
   * Se OK → ``ShadowTradeService._create_from_decision`` (status=PENDING).
   * Se faltar OHLCV → INSERT direto com ``status='ERROR'`` +
     ``config_snapshot.error='no_ohlcv_coverage_at_backfill'``.
4. Commit por batch (``--batch-size``, default 50) — não falha o batch
   inteiro se um shadow individual quebrar.
5. Sumário no fim + exit code 0 (qualquer progresso) ou 1 (erro fatal).

Exit codes
----------
* 0 — query OK; pode ter criado 0 ou N shadows.
* 1 — falha de DB ou config (ex: usuário sem ``ConfigProfile`` ativo).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

# Make ``app`` importable when invoked from the repo root or from inside
# ``backend/`` — same trick as ``backfill_structural_orphans.py``.
_HERE = Path(__file__).resolve().parent
_BACKEND_ROOT = _HERE.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_shadow_trades")


def _parse_date(s: str) -> datetime:
    """Aceita 'YYYY-MM-DD' (00:00:00 UTC) ou ISO completo."""
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # fromisoformat lida com timezone se presente
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Data inválida {s!r}: use YYYY-MM-DD ou ISO 8601 ({exc})"
        )


async def _load_user_config(db, user_id: UUID) -> dict[str, Any]:
    """Extrai tp_pct/sl_pct do ConfigProfile ativo do usuário.

    Mesmo mapeamento de ``execute_buy.py`` (linhas que constroem
    ``_shadow_user_config``) — single source of truth pra que backfill
    e fluxo on-line gerem TPs/SLs idênticos.
    """
    from sqlalchemy import select

    from app.models.config_profile import ConfigProfile
    from app.schemas.spot_engine_config import SpotEngineConfig

    rows = await db.execute(
        select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.config_type == "spot_engine",
            ConfigProfile.is_active == True,  # noqa: E712
        )
    )
    cfg_row = rows.scalar_one_or_none()
    if cfg_row is None:
        raise RuntimeError(
            f"Usuário {user_id} não tem ConfigProfile ativo "
            f"(config_type='spot_engine'). Backfill abortado."
        )
    se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
    return {
        "tp_pct": float(se_cfg.selling.take_profit_pct),
        "sl_pct": float(se_cfg.sell_flow.kill_switch.max_drawdown_from_hwm_pct),
    }


async def _has_ohlcv_after(db, symbol: str, after_ts: datetime) -> bool:
    """True se existir ao menos 1 candle 1m em ``ohlcv`` após ``after_ts``."""
    from sqlalchemy import text

    res = await db.execute(
        text(
            """
            SELECT 1
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time > :t
             LIMIT 1
            """
        ),
        {"s": symbol, "t": after_ts},
    )
    return res.fetchone() is not None


_INSERT_ERROR_SHADOW_SQL = None  # lazy build (text() needs sqlalchemy import)


def _build_insert_error_sql():
    global _INSERT_ERROR_SHADOW_SQL
    if _INSERT_ERROR_SHADOW_SQL is not None:
        return _INSERT_ERROR_SHADOW_SQL
    from sqlalchemy import text

    _INSERT_ERROR_SHADOW_SQL = text("""
        INSERT INTO shadow_trades (
            decision_id, user_id, symbol, strategy, direction,
            amount_usdt, status, skip_reason, config_snapshot
        ) VALUES (
            :decision_id, :user_id, :symbol, :strategy, 'long',
            :amount_usdt, 'ERROR', :skip_reason,
            CAST(:config_snapshot AS JSONB)
        )
        ON CONFLICT (decision_id) DO NOTHING
        RETURNING id
    """)
    return _INSERT_ERROR_SHADOW_SQL


async def _insert_error_shadow(
    db,
    decision,
    skip_reason: str,
    user_config: dict[str, Any],
    error_msg: str,
):
    """Cria shadow em status=ERROR quando OHLCV não cobre o símbolo."""
    from app.services.shadow_trade_service import SHADOW_TRADE_AMOUNT_USDT

    sql = _build_insert_error_sql()
    config_snap = {
        **user_config,
        "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
        "error": error_msg,
    }
    res = await db.execute(
        sql,
        {
            "decision_id": decision.id,
            "user_id": decision.user_id,
            "symbol": decision.symbol,
            "strategy": decision.strategy,
            "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
            "skip_reason": skip_reason,
            "config_snapshot": json.dumps(config_snap, default=str),
        },
    )
    row = res.fetchone()
    return row[0] if row is not None else None


async def _enumerate_eligible(
    db,
    user_id: UUID,
    min_date: datetime,
    max_date: datetime,
):
    """IDs de promoções elegíveis (anti-join com shadow_trades existentes)."""
    from sqlalchemy import text

    rows = await db.execute(
        text(
            """
            SELECT d.id
              FROM decisions_log d
              LEFT JOIN shadow_trades s ON s.decision_id = d.id
             WHERE d.user_id = :uid
               AND d.decision = 'ALLOW'
               AND d.direction = 'up'
               AND d.created_at >= :min_dt
               AND d.created_at <  :max_dt
               AND (d.trade_executed IS NULL OR d.trade_executed = FALSE)
               AND s.id IS NULL
             ORDER BY d.created_at ASC
            """
        ),
        {"uid": user_id, "min_dt": min_date, "max_dt": max_date},
    )
    # Determinismo: ordena IDs antes de iterar para evitar deadlock 40P01
    # entre o backfill e workers concorrentes (gotcha #251/#273).
    return sorted(r.id for r in rows.fetchall())


async def _process_decision(
    db,
    decision,
    user_config: dict[str, Any],
    skip_reason: str,
    dry_run: bool,
) -> str:
    """Processa uma promoção. Retorna 'created' | 'errored' | 'dry'."""
    from app.services.shadow_trade_service import _create_from_decision

    has_ohlcv = await _has_ohlcv_after(db, decision.symbol, decision.created_at)

    if dry_run:
        return "dry"

    if not has_ohlcv:
        new_id = await _insert_error_shadow(
            db,
            decision,
            skip_reason=skip_reason,
            user_config=user_config,
            error_msg="no_ohlcv_coverage_at_backfill",
        )
        # ON CONFLICT DO NOTHING devolve None se outro processo (ex.:
        # worker concorrente) já gravou row pra este decision_id —
        # contabiliza como duplicate em vez de errored.
        return "errored" if new_id is not None else "duplicate"

    new_id = await _create_from_decision(db, decision, skip_reason, user_config)
    return "created" if new_id is not None else "duplicate"


async def _run(
    user_id: UUID,
    min_date: datetime,
    max_date: datetime,
    dry_run: bool,
    batch_size: int,
    skip_reason: str,
) -> dict[str, int]:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.backoffice import DecisionLog

    summary = {
        "eligible": 0,
        "created": 0,
        "errored_no_ohlcv": 0,
        "duplicates": 0,
        "failures": 0,
        "dry": 0,
    }

    async with AsyncSessionLocal() as db:
        async with db.begin():
            user_config = await _load_user_config(db, user_id)
            ids = await _enumerate_eligible(db, user_id, min_date, max_date)

        summary["eligible"] = len(ids)
        logger.info(
            "Backfill: user=%s range=[%s, %s) elegíveis=%d batch_size=%d dry_run=%s "
            "tp_pct=%.4f sl_pct=%.4f",
            user_id, min_date.isoformat(), max_date.isoformat(),
            len(ids), batch_size, dry_run,
            user_config["tp_pct"], user_config["sl_pct"],
        )

        if not ids:
            return summary

        # Commit per-batch — uma falha individual não derruba o lote.
        for batch_start in range(0, len(ids), batch_size):
            chunk = ids[batch_start : batch_start + batch_size]
            try:
                async with db.begin():
                    for did in chunk:
                        # Savepoint por decisão: um IntegrityError /
                        # DBAPIError individual não poisona a outer
                        # transaction do batch. ``begin_nested`` faz
                        # ROLLBACK TO SAVEPOINT no exit excepcional, e
                        # o batch segue processando as próximas IDs.
                        try:
                            async with db.begin_nested():
                                res = await db.execute(
                                    select(DecisionLog)
                                    .where(DecisionLog.id == did)
                                    .limit(1)
                                )
                                decision = res.scalar_one_or_none()
                                if decision is None:
                                    summary["failures"] += 1
                                    continue
                                outcome = await _process_decision(
                                    db, decision, user_config, skip_reason, dry_run
                                )
                            if outcome == "created":
                                summary["created"] += 1
                            elif outcome == "errored":
                                summary["errored_no_ohlcv"] += 1
                            elif outcome == "duplicate":
                                summary["duplicates"] += 1
                            elif outcome == "dry":
                                summary["dry"] += 1
                        except Exception:
                            summary["failures"] += 1
                            logger.exception(
                                "[backfill] decision_id=%s falhou", did
                            )
            except Exception:
                # Se o COMMIT do batch falhar, conta o batch todo como
                # falha (rollback automático já aconteceu pelo ctx mgr).
                summary["failures"] += len(chunk)
                logger.exception(
                    "[backfill] batch start=%d (size=%d) falhou no commit",
                    batch_start, len(chunk),
                )

            processed = min(batch_start + batch_size, len(ids))
            if processed % 50 == 0 or processed == len(ids):
                logger.info(
                    "[backfill] progresso %d/%d — created=%d errored=%d "
                    "duplicates=%d failures=%d",
                    processed, len(ids),
                    summary["created"], summary["errored_no_ohlcv"],
                    summary["duplicates"], summary["failures"],
                )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Shadow Portfolio a partir de promoções L3 históricas.",
    )
    parser.add_argument(
        "--user-id", required=True, type=lambda s: UUID(s),
        help="UUID do usuário dono das promoções a serem backfilladas.",
    )
    parser.add_argument(
        "--min-date", required=True, type=_parse_date,
        help="Início do range (inclusive). Aceita YYYY-MM-DD ou ISO 8601.",
    )
    parser.add_argument(
        "--max-date", required=True, type=_parse_date,
        help="Fim do range (exclusive). Aceita YYYY-MM-DD ou ISO 8601.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Não grava nada — só lista o que seria criado.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Promoções por commit (default 50).",
    )
    parser.add_argument(
        "--skip-reason", default="BACKFILL",
        help="Valor a gravar em shadow_trades.skip_reason (default 'BACKFILL').",
    )
    args = parser.parse_args()

    if args.min_date >= args.max_date:
        logger.error("--min-date deve ser < --max-date")
        return 1
    if args.batch_size <= 0:
        logger.error("--batch-size deve ser > 0")
        return 1

    try:
        summary = asyncio.run(
            _run(
                user_id=args.user_id,
                min_date=args.min_date,
                max_date=args.max_date,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
                skip_reason=args.skip_reason,
            )
        )
    except RuntimeError as exc:
        logger.error("Backfill abortado: %s", exc)
        return 1
    except Exception:
        logger.exception("Backfill falhou com erro fatal")
        return 1

    print(
        "Backfill summary: "
        f"eligible={summary['eligible']} "
        f"created={summary['created']} "
        f"errored_no_ohlcv={summary['errored_no_ohlcv']} "
        f"duplicates={summary['duplicates']} "
        f"failures={summary['failures']} "
        f"dry={summary['dry']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
