"""
Phase 2 — Construção de Labels (retorno forward como alvo)
L2 Validação Direcional (v2)

Execução:
    railway run python -m research.l2_directional.phase_02_labels

O que faz:
  Para cada shadow_trade L1_SPECTRUM com outcome fechado:
  1. Determina entry_price = open do próximo candle 5m após o sinal L1
     (NÃO o close do candle do sinal — evita look-ahead)
  2. Computa labels:
     - future_return_30m_net = ((close_30m - entry_price)/entry_price)*100 - cost_total
     - future_return_60m_net = ((close_60m - entry_price)/entry_price)*100 - cost_total
  3. Diagnósticos (NÃO são alvo de treino):
     - MFE_30m = ((max_high após entry) - entry_price)/entry_price)*100
     - MAE_30m = ((min_low após entry) - entry_price)/entry_price)*100
  4. Salva resultado em ml_experiment_labels (cria se não existir)
  5. Imprime estatísticas de distribuição dos labels

  Regras aplicadas:
  - Entry sem look-ahead: open do candle APÓS o sinal (>= created_at)
  - MFE/MAE: diagnóstico APENAS — não usar como alvo de treino
  - Todos os retornos em % líquidos de cost_total
  - Candle ausente: label = NULL (não imputar)
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from ._db import connect


async def ensure_labels_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_experiment_labels (
            id                    BIGSERIAL PRIMARY KEY,
            shadow_trade_id       UUID NOT NULL,
            symbol                TEXT NOT NULL,
            signal_at             TIMESTAMPTZ NOT NULL,
            entry_candle_time     TIMESTAMPTZ,
            entry_price           DOUBLE PRECISION,
            close_30m             DOUBLE PRECISION,
            close_60m             DOUBLE PRECISION,
            high_30m              DOUBLE PRECISION,
            low_30m               DOUBLE PRECISION,
            future_return_30m_net DOUBLE PRECISION,
            future_return_60m_net DOUBLE PRECISION,
            mfe_30m               DOUBLE PRECISION,
            mae_30m               DOUBLE PRECISION,
            cost_total            DOUBLE PRECISION NOT NULL,
            pnl_pct_actual        DOUBLE PRECISION,
            outcome               TEXT,
            run_at                TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (shadow_trade_id)
        )
    """)


async def main() -> None:
    conn = await connect()

    # Config
    cfg_row = await conn.fetchrow("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml_research' AND is_active = true LIMIT 1
    """)
    if cfg_row:
        raw = cfg_row["config_json"]
        cfg = json.loads(raw) if isinstance(raw, str) else dict(raw)
    else:
        cfg = {}
    lookback_days = int(cfg.get("ml.lookback_days", 90))
    cost_roundtrip = float(cfg.get("ml.cost_roundtrip_pct", 0.0040))
    slippage = float(cfg.get("ml.slippage_pct", 0.0005))
    cost_total = cost_roundtrip + 2 * slippage
    horizon_min = int(cfg.get("ml.future_return_horizon_min", 30))
    horizon_sec_min = int(cfg.get("ml.future_return_horizon_sec_min", 60))
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    print("=" * 70)
    print("FASE 2 — Construção de Labels (retorno forward)")
    print("L2 Validação Direcional (v2)")
    print(f"Executado em: {datetime.now(timezone.utc).isoformat()}")
    print(f"cost_total [calc] = {cost_total:.4f} ({cost_total*100:.3f}%)")
    print(f"Horizonte primário: {horizon_min}m | secundário: {horizon_sec_min}m")
    print("=" * 70)

    await ensure_labels_table(conn)

    # Carregar shadows fechados sem label ainda
    trades = await conn.fetch("""
        SELECT st.id, st.symbol, st.created_at, st.pnl_pct, st.outcome
        FROM shadow_trades st
        LEFT JOIN ml_experiment_labels el ON el.shadow_trade_id = st.id
        WHERE st.source = 'L1_SPECTRUM'
          AND st.outcome IN ('TP_HIT', 'SL_HIT')
          AND st.created_at >= $1
          AND el.id IS NULL
        ORDER BY st.created_at ASC
    """, cutoff)

    print(f"\n  Trades a processar [query]: {len(trades)}")
    if not trades:
        print("  Nada a fazer — todos já têm labels ou não há dados suficientes.")
        # Mesmo sem novos trades, mostrar stats do que existe
        await _print_stats(conn, cost_total, horizon_min)
        await conn.close()
        return

    n_ok = 0
    n_no_candle = 0
    n_no_fwd = 0

    for trade in trades:
        symbol = trade["symbol"]
        signal_at = trade["created_at"]

        # Entry: primeiro candle 5m com time > signal_at para o símbolo
        entry_row = await conn.fetchrow("""
            SELECT time, open
            FROM ohlcv
            WHERE symbol = $1
              AND timeframe = '5m'
              AND time > $2
            ORDER BY time ASC
            LIMIT 1
        """, symbol, signal_at)

        if entry_row is None:
            n_no_candle += 1
            await conn.execute("""
                INSERT INTO ml_experiment_labels
                    (shadow_trade_id, symbol, signal_at, cost_total, pnl_pct_actual, outcome)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (shadow_trade_id) DO NOTHING
            """, trade["id"], symbol, signal_at, cost_total, trade["pnl_pct"], trade["outcome"])
            continue

        entry_candle_time = entry_row["time"]
        entry_price = float(entry_row["open"])

        # Forward candles para horizonte primário
        n_candles_primary = horizon_min // 5
        n_candles_sec = horizon_sec_min // 5

        fwd_rows = await conn.fetch("""
            SELECT time, open, high, low, close
            FROM ohlcv
            WHERE symbol = $1
              AND timeframe = '5m'
              AND time >= $2
            ORDER BY time ASC
            LIMIT $3
        """, symbol, entry_candle_time, n_candles_sec + 1)  # +1 inclui o próprio candle de entry

        if len(fwd_rows) < n_candles_primary + 1:
            # Não há candles suficientes para o horizonte primário
            n_no_fwd += 1
            await conn.execute("""
                INSERT INTO ml_experiment_labels
                    (shadow_trade_id, symbol, signal_at, entry_candle_time, entry_price,
                     cost_total, pnl_pct_actual, outcome)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (shadow_trade_id) DO NOTHING
            """, trade["id"], symbol, signal_at, entry_candle_time, entry_price,
               cost_total, trade["pnl_pct"], trade["outcome"])
            continue

        # Primary horizon (30m = 6 candles após o de entry)
        primary_candle = fwd_rows[min(n_candles_primary, len(fwd_rows) - 1)]
        close_30m = float(primary_candle["close"])
        ret_30m = ((close_30m - entry_price) / entry_price) * 100 - cost_total * 100

        # Secondary horizon (60m = 12 candles), se disponível
        close_60m: Optional[float] = None
        ret_60m: Optional[float] = None
        if len(fwd_rows) >= n_candles_sec + 1:
            sec_candle = fwd_rows[min(n_candles_sec, len(fwd_rows) - 1)]
            close_60m = float(sec_candle["close"])
            ret_60m = ((close_60m - entry_price) / entry_price) * 100 - cost_total * 100

        # MFE / MAE diagnósticos (somente candles primários, sem o de entry)
        primary_slice = fwd_rows[1:n_candles_primary + 1]
        mfe_30m: Optional[float] = None
        mae_30m: Optional[float] = None
        if primary_slice and entry_price > 0:
            highs = [float(r["high"]) for r in primary_slice]
            lows = [float(r["low"]) for r in primary_slice]
            mfe_30m = (max(highs) - entry_price) / entry_price * 100
            mae_30m = (min(lows) - entry_price) / entry_price * 100

        await conn.execute("""
            INSERT INTO ml_experiment_labels
                (shadow_trade_id, symbol, signal_at, entry_candle_time, entry_price,
                 close_30m, close_60m, high_30m, low_30m,
                 future_return_30m_net, future_return_60m_net,
                 mfe_30m, mae_30m, cost_total, pnl_pct_actual, outcome)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (shadow_trade_id) DO UPDATE SET
                close_30m = EXCLUDED.close_30m,
                future_return_30m_net = EXCLUDED.future_return_30m_net,
                close_60m = EXCLUDED.close_60m,
                future_return_60m_net = EXCLUDED.future_return_60m_net,
                mfe_30m = EXCLUDED.mfe_30m,
                mae_30m = EXCLUDED.mae_30m,
                run_at = NOW()
        """,
            trade["id"], symbol, signal_at, entry_candle_time, entry_price,
            close_30m, close_60m,
            max([float(r["high"]) for r in primary_slice]) if primary_slice else None,
            min([float(r["low"]) for r in primary_slice]) if primary_slice else None,
            ret_30m, ret_60m, mfe_30m, mae_30m,
            cost_total, trade["pnl_pct"], trade["outcome"],
        )
        n_ok += 1

    print(f"\n  Processados:          {n_ok}")
    print(f"  Sem candle de entry:  {n_no_candle}")
    print(f"  Sem candle forward:   {n_no_fwd}")

    await _print_stats(conn, cost_total, horizon_min)
    await conn.close()


async def _print_stats(conn, cost_total: float, horizon_min: int) -> None:
    stats = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE future_return_30m_net IS NOT NULL)  AS n_labeled,
            AVG(future_return_30m_net)                                 AS mean_30m,
            PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY future_return_30m_net) AS p10,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY future_return_30m_net) AS p25,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY future_return_30m_net) AS p50,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY future_return_30m_net) AS p75,
            PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY future_return_30m_net) AS p90,
            COUNT(*) FILTER (WHERE future_return_30m_net > 0)          AS n_positive,
            AVG(mfe_30m)                                               AS avg_mfe,
            AVG(mae_30m)                                               AS avg_mae
        FROM ml_experiment_labels
        WHERE future_return_30m_net IS NOT NULL
    """)

    n_labeled = int(stats["n_labeled"] or 0)
    print(f"\n[STATS] ml_experiment_labels — {n_labeled} registros com label {horizon_min}m")

    if n_labeled == 0:
        print("  Sem dados suficientes para distribuição.")
        return

    print(f"\n  future_return_{horizon_min}m_net [query ml_experiment_labels] (líquido de cost_total={cost_total*100:.3f}%):")
    print(f"    média  = {float(stats['mean_30m'] or 0):.4f}%")
    print(f"    p10    = {float(stats['p10'] or 0):.4f}%")
    print(f"    p25    = {float(stats['p25'] or 0):.4f}%")
    print(f"    p50    = {float(stats['p50'] or 0):.4f}%")
    print(f"    p75    = {float(stats['p75'] or 0):.4f}%")
    print(f"    p90    = {float(stats['p90'] or 0):.4f}%")
    pct_positive = int(stats["n_positive"] or 0) / n_labeled
    print(f"    % positivos líquidos [calc] = {pct_positive*100:.1f}%")
    if stats["avg_mfe"] is not None:
        print(f"\n  MFE_{horizon_min}m [diagnóstico, NÃO alvo]: média = {float(stats['avg_mfe']):.4f}%")
        print(f"  MAE_{horizon_min}m [diagnóstico, NÃO alvo]: média = {float(stats['mae_30m'] if hasattr(stats, 'mae_30m') else stats['avg_mae'] or 0):.4f}%")

    print(f"\n  Ledger de Evidências:")
    print(f"    n_labeled [query ml_experiment_labels] = {n_labeled}")
    print(f"    mean_30m_net [query] = {float(stats['mean_30m'] or 0):.4f}%")
    print(f"    pct_positive [calc: n_positive/n_labeled] = {pct_positive*100:.1f}%")
    print(f"    cost_total [config ml_research] = {cost_total:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
