"""Backfill: fecha shadow trades abertos com barreira TP/SL já rompida.

Uso:
    python backend/scripts/fix_shadow_open_trades_breached_barriers.py --dry-run
    python backend/scripts/fix_shadow_open_trades_breached_barriers.py --apply

Fonte de preço: market_metadata.price (mesma fonte do shadow monitor).
Stale guard: ignora market_metadata mais antigo que 10 min (backfill mais
             conservador que o monitor, que usa 5 min).
Idempotência: WHERE status IN ('RUNNING','PENDING') — não reprocessa COMPLETED.
Auditoria: grava em shadow_trade_closure_audit (migration 119).

Safety:
  - Afeta SOMENTE shadow_trades (simulação).
  - Não toca ordens reais, profiles, modelos ou watchlists.
  - Dry-run não altera nada.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Permite rodar de backend/ ou da raiz do projeto.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import asyncpg
except ImportError:
    print("asyncpg não instalado. Use: pip install asyncpg", file=sys.stderr)
    sys.exit(1)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("DATABASE_URL não definida.", file=sys.stderr)
    sys.exit(1)

# Converte postgresql+asyncpg:// → postgresql:// (asyncpg nativo)
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

STALE_SECONDS = int(os.environ.get("BACKFILL_STALE_SECONDS", 600))  # 10 min


async def _fetch_breached(conn, stale_cutoff: datetime) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
            st.id,
            st.symbol,
            st.source,
            st.status,
            st.entry_price,
            st.tp_price,
            st.sl_price,
            st.amount_usdt,
            st.entry_timestamp,
            st.watchlist_id,
            st.profile_id,
            st.created_at,
            mm.price       AS current_price,
            mm.last_updated AS price_ts,
            CASE
                WHEN mm.price <= st.sl_price THEN 'SL_HIT'
                WHEN mm.price >= st.tp_price THEN 'TP_HIT'
            END AS closure_reason
        FROM shadow_trades st
        JOIN market_metadata mm ON mm.symbol = st.symbol
        WHERE st.status IN ('RUNNING', 'PENDING')
          AND st.tp_price IS NOT NULL
          AND st.sl_price IS NOT NULL
          AND (mm.price <= st.sl_price OR mm.price >= st.tp_price)
          AND (mm.last_updated IS NULL OR mm.last_updated >= $1)
        ORDER BY st.created_at ASC
        """,
        stale_cutoff,
    )
    return [dict(r) for r in rows]


async def _fetch_stale_count(conn, stale_cutoff: datetime) -> int:
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS cnt
        FROM shadow_trades st
        JOIN market_metadata mm ON mm.symbol = st.symbol
        WHERE st.status IN ('RUNNING', 'PENDING')
          AND st.tp_price IS NOT NULL
          AND st.sl_price IS NOT NULL
          AND (mm.price <= st.sl_price OR mm.price >= st.tp_price)
          AND mm.last_updated IS NOT NULL
          AND mm.last_updated < $1
        """,
        stale_cutoff,
    )
    return row["cnt"] if row else 0


def _compute_pnl(entry_price, exit_price, amount_usdt):
    if entry_price and exit_price and entry_price > 0:
        pnl_pct = (float(exit_price) - float(entry_price)) / float(entry_price) * 100.0
        pnl_usdt = (float(amount_usdt) if amount_usdt else 1000.0) * pnl_pct / 100.0
        return round(pnl_pct, 6), round(pnl_usdt, 4)
    return None, None


async def dry_run(conn) -> None:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)
    trades = await _fetch_breached(conn, stale_cutoff)
    stale_cnt = await _fetch_stale_count(conn, stale_cutoff)

    print("\n=== DRY-RUN — fix_shadow_open_trades_breached_barriers ===\n")

    by_source: Dict[str, Dict[str, int]] = {}
    total_sl = 0
    total_tp = 0
    oldest = None
    est_pnl = 0.0

    for t in trades:
        src = t["source"] or "UNKNOWN"
        reason = t["closure_reason"] or "UNKNOWN"
        by_source.setdefault(src, {"SL_HIT": 0, "TP_HIT": 0})
        by_source[src][reason] = by_source[src].get(reason, 0) + 1
        if reason == "SL_HIT":
            total_sl += 1
        else:
            total_tp += 1
        exit_p = float(t["sl_price"]) if reason == "SL_HIT" else float(t["tp_price"])
        _, pu = _compute_pnl(t["entry_price"], exit_p, t["amount_usdt"])
        if pu is not None:
            est_pnl += pu
        if oldest is None or t["created_at"] < oldest:
            oldest = t["created_at"]

    print(f"open_below_sl_count  : {total_sl}")
    print(f"open_above_tp_count  : {total_tp}")
    print(f"total_breached       : {len(trades)}")
    print(f"stale_skipped        : {stale_cnt}")
    print(f"estimated_pnl_usdt   : {round(est_pnl, 2)}")
    print(f"oldest_open_breached : {oldest}")
    print(f"\nby_source:")
    for src, counts in sorted(by_source.items()):
        sl = counts.get("SL_HIT", 0)
        tp = counts.get("TP_HIT", 0)
        print(f"  {src:20s}  SL={sl:4d}  TP={tp:4d}")

    print("\nby_watchlist (top 10 by count):")
    wl_counts: Dict[str, int] = {}
    for t in trades:
        wk = str(t["watchlist_id"]) if t["watchlist_id"] else "NULL"
        wl_counts[wk] = wl_counts.get(wk, 0) + 1
    for wk, cnt in sorted(wl_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {wk[:40]:40s}  {cnt}")

    print("\nby_profile (top 10 by count):")
    pr_counts: Dict[str, int] = {}
    for t in trades:
        pk = str(t["profile_id"]) if t["profile_id"] else "NULL"
        pr_counts[pk] = pr_counts.get(pk, 0) + 1
    for pk, cnt in sorted(pr_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {pk[:40]:40s}  {cnt}")

    print("\nNenhuma alteração feita (dry-run).")


async def apply(conn) -> None:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)
    trades = await _fetch_breached(conn, stale_cutoff)

    if not trades:
        print("Nenhum trade breachado encontrado — nada a fazer.")
        return

    run_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc)
    closed_sl = 0
    closed_tp = 0
    errors = 0
    audit_rows = []

    print(f"\n=== APPLY — fix_shadow_open_trades_breached_barriers ===")
    print(f"run_id      : {run_id}")
    print(f"total_found : {len(trades)}")
    print(f"started_at  : {now_utc.isoformat()}")

    for t in trades:
        reason = t["closure_reason"]
        if not reason:
            continue
        exit_price = float(t["sl_price"]) if reason == "SL_HIT" else float(t["tp_price"])
        pnl_pct, pnl_usdt = _compute_pnl(t["entry_price"], exit_price, t["amount_usdt"])
        price_ts = t["price_ts"]
        price_age = int((now_utc - price_ts.replace(tzinfo=timezone.utc if price_ts.tzinfo is None else None)).total_seconds()) if price_ts else None

        try:
            async with conn.transaction():
                result = await conn.execute(
                    """
                    UPDATE shadow_trades
                    SET status       = 'COMPLETED',
                        outcome      = $1,
                        exit_price   = $2,
                        exit_timestamp = $3,
                        completed_at = $3,
                        pnl_pct      = $4,
                        pnl_usdt     = $5,
                        barrier_touched = CASE WHEN $1='TP_HIT' THEN 'TP' ELSE 'SL' END,
                        barrier_touched_at = $3,
                        intrabar_convention = 'SL_FIRST'
                    WHERE id = $6
                      AND status IN ('RUNNING', 'PENDING')
                    """,
                    reason,
                    exit_price,
                    now_utc,
                    pnl_pct,
                    pnl_usdt,
                    t["id"],
                )
                if result == "UPDATE 1":
                    if reason == "SL_HIT":
                        closed_sl += 1
                    else:
                        closed_tp += 1
                    audit_rows.append((
                        t["id"],
                        t["source"],
                        t["symbol"],
                        t["status"],
                        float(t["entry_price"]) if t["entry_price"] else None,
                        exit_price,
                        float(t["tp_price"]) if t["tp_price"] else None,
                        float(t["sl_price"]) if t["sl_price"] else None,
                        pnl_pct,
                        pnl_usdt,
                        reason,
                        "market_metadata",
                        price_ts,
                        price_age,
                        uuid.UUID(run_id),
                    ))
        except Exception as e:
            errors += 1
            print(f"  ERROR shadow_id={t['id']} symbol={t['symbol']}: {e}", file=sys.stderr)

    # Audit insert (best-effort, single tx)
    if audit_rows:
        try:
            await conn.executemany(
                """
                INSERT INTO shadow_trade_closure_audit
                (shadow_trade_id, source, symbol, previous_status,
                 entry_price, exit_price, tp_price, sl_price,
                 pnl_pct, pnl_usdt, closure_reason,
                 price_source, price_timestamp, price_age_seconds, closer_run_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT DO NOTHING
                """,
                audit_rows,
            )
        except Exception as e:
            print(f"  WARNING: audit write failed: {e}", file=sys.stderr)

    end_utc = datetime.now(timezone.utc)
    print(f"\nclosed_sl   : {closed_sl}")
    print(f"closed_tp   : {closed_tp}")
    print(f"errors      : {errors}")
    print(f"ended_at    : {end_utc.isoformat()}")
    print(f"run_id      : {run_id}")


async def main(mode: str) -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if mode == "dry-run":
            await dry_run(conn)
        else:
            await apply(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill shadow trades com barreira TP/SL rompida"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Apenas relata, não altera")
    group.add_argument("--apply", action="store_true", help="Fecha os trades breachados")
    args = parser.parse_args()

    mode = "dry-run" if args.dry_run else "apply"
    asyncio.run(main(mode))
