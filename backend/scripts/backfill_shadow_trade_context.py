"""Backfill ``shadow_trades`` market-context columns (migration 052).

Popula retroativamente os 4 campos:

* ``btc_price_at_entry``
* ``btc_change_1h_pct``
* ``funding_rate_at_entry``
* ``n_concurrent_signals``

para shadows que já existem com ``entry_timestamp`` preenchido mas
algum dos 4 campos NULL.

Uso
---
    cd backend && python -m scripts.backfill_shadow_trade_context

Opções:
    --dry-run        — não grava, só loga o que faria
    --batch-size N   — quantos shadows por commit (default 50)
    --limit N        — processa no máximo N shadows nesta execução

Idempotente
-----------
Roda quantas vezes quiser. O filtro ``entry_timestamp IS NOT NULL AND
(any of 4 cols IS NULL)`` garante que shadows já enriquecidos não são
re-processados (a não ser que algum campo tenha sido NULL por falha
de fonte de dados — nesse caso re-tenta, o que é o comportamento
esperado).

Nunca falha o batch inteiro: cada shadow tem seu próprio try/except,
erros isolados são logados e o backfill continua.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import List

# Bootstrap sys.path para que o script funcione tanto invocado como
# arquivo (`python backend/scripts/backfill_shadow_trade_context.py`)
# quanto como módulo (`python -m scripts.backfill_shadow_trade_context`
# rodado de dentro de `backend/`). Adiciona o diretório `backend/` ao
# path para resolver `from app.…`.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_HERE)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import select, text  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backfill_shadow_trade_context")


async def _run(dry_run: bool, batch_size: int, limit: int | None) -> int:
    # Imports tardios pra que o script seja standalone (requer só o
    # módulo `app` no PYTHONPATH — `python -m scripts.foo` resolve isso).
    from app.database import AsyncSessionLocal
    from app.models.shadow_trade import ShadowTrade
    from app.services import shadow_trade_service

    # Etapa 1 — descobrir candidatos (sessão própria, somente leitura).
    async with AsyncSessionLocal() as db:
        q = (
            select(ShadowTrade)
            .where(
                ShadowTrade.entry_timestamp.isnot(None),
                (
                    ShadowTrade.btc_price_at_entry.is_(None)
                    | ShadowTrade.btc_change_1h_pct.is_(None)
                    | ShadowTrade.funding_rate_at_entry.is_(None)
                    | ShadowTrade.n_concurrent_signals.is_(None)
                ),
            )
            .order_by(ShadowTrade.created_at.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        # Carrega só ids/symbols/entry_ts/decision_id pra não segurar o
        # snapshot na sessão durante o loop de update (que abre sessões
        # próprias por batch). Trabalhamos com uma lista de tuplas.
        rows = await db.execute(q)
        candidates: List[tuple] = [
            (s.id, s.symbol, s.entry_timestamp, s.decision_id)
            for s in rows.scalars().all()
        ]

    total = len(candidates)
    if total == 0:
        logger.info("Nenhum shadow_trade pendente de enriquecimento — nada a fazer.")
        return 0

    logger.info("Processando %d shadow_trades sem contexto%s...",
                total, " (DRY-RUN)" if dry_run else "")

    updated = 0
    errors = 0

    for offset in range(0, total, batch_size):
        chunk = candidates[offset:offset + batch_size]
        # Uma sessão por batch — UPDATE direto via SQL (mais leve que
        # carregar/instanciar o ORM de novo).
        async with AsyncSessionLocal() as db:
            try:
                async with db.begin():
                    for idx, (sid, symbol, entry_ts, decision_id) in enumerate(
                        chunk, start=offset + 1
                    ):
                        try:
                            ctx = await shadow_trade_service.enrich_market_context(
                                db,
                                symbol=symbol,
                                entry_timestamp=entry_ts,
                                decision_id=decision_id,
                            )
                            btc_change_disp = (
                                f"{ctx['btc_change_1h_pct']:+.2f}%"
                                if ctx["btc_change_1h_pct"] is not None
                                else "NULL"
                            )
                            funding_disp = (
                                f"{ctx['funding_rate_at_entry']:.6f}"
                                if ctx["funding_rate_at_entry"] is not None
                                else "NULL"
                            )
                            concur_disp = (
                                str(ctx["n_concurrent_signals"])
                                if ctx["n_concurrent_signals"] is not None
                                else "NULL"
                            )
                            logger.info(
                                "[%d/%d] %s — btc_change_1h: %s | funding: %s | concurrent: %s",
                                idx, total, symbol,
                                btc_change_disp, funding_disp, concur_disp,
                            )
                            if dry_run:
                                continue

                            # COALESCE preserva o que já estava (não
                            # sobrescreve com NULL caso uma fonte de
                            # dados esteja temporariamente indisponível).
                            await db.execute(
                                text(
                                    """
                                    UPDATE shadow_trades
                                       SET btc_price_at_entry =
                                             COALESCE(btc_price_at_entry, :btc_price),
                                           btc_change_1h_pct =
                                             COALESCE(btc_change_1h_pct, :btc_change),
                                           funding_rate_at_entry =
                                             COALESCE(funding_rate_at_entry, :funding),
                                           n_concurrent_signals =
                                             COALESCE(n_concurrent_signals, :concur)
                                     WHERE id = :sid
                                    """
                                ),
                                {
                                    "btc_price": ctx["btc_price_at_entry"],
                                    "btc_change": ctx["btc_change_1h_pct"],
                                    "funding": ctx["funding_rate_at_entry"],
                                    "concur": ctx["n_concurrent_signals"],
                                    "sid": sid,
                                },
                            )
                            updated += 1
                        except Exception:
                            errors += 1
                            logger.exception(
                                "[%d/%d] %s — erro ao enriquecer (id=%s) — pulando",
                                idx, total, symbol, sid,
                            )
            except Exception:
                errors += 1
                logger.exception(
                    "Batch falhou (offset=%d size=%d) — rollback automático",
                    offset, len(chunk),
                )

    logger.info(
        "Concluído: %d registros atualizados, %d erros (de %d candidatos)%s",
        updated, errors, total, " (DRY-RUN — nada gravado)" if dry_run else "",
    )
    return 0 if errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill market-context columns of shadow_trades (migration 052).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Só loga, não grava no banco.")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Shadows por commit (default 50).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limite máximo de shadows nesta execução.")
    args = parser.parse_args()

    return asyncio.run(_run(args.dry_run, args.batch_size, args.limit))


if __name__ == "__main__":
    sys.exit(main())
