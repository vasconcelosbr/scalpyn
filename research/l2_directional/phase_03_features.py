"""
Phase 3 — Feature Engineering Direcional
L2 Validação Direcional (v2)

Execução:
    python -m research.l2_directional.phase_03_features  (com DATABASE_URL)

O que faz:
  Para cada shadow_trade L1_SPECTRUM com label em ml_experiment_labels:
  1. Extrai features_snapshot (limpas de stubs e não-numéricos)
  2. Busca 12 candles OHLCV 5m anteriores ao sinal
  3. Computa features derivadas:
     - distance_ema9_atr, distance_vwap_atr (em unidades de ATR)
     - price_change_pct_6c, price_change_pct_12c
     - rel_volume_10c, volume_zscore_10c
     - high_low_range_6c_atr, close_position_6c
     - div_price_rsi, div_price_volume_delta (divergência)
  4. Salva em ml_experiment_features (upsert)
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ._db import connect

STUB_FEATURES = {
    "_features_captured_at", "macd_signal", "market_data_confidence",
    "market_data_source", "market_data_symbol", "psar_signal",
    "psar_trend", "taker_source", "taker_window",
}

LOOKBACK_CANDLES = 12   # 60min
SHORT_LOOKBACK   = 6    # 30min para slopes e divergência


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _compute_derived(snapshot: dict, candles: list[dict]) -> dict:
    """Calcula features derivadas a partir do snapshot e dos candles OHLCV."""
    d: dict[str, float] = {}

    ema9_dist = _safe_float(snapshot.get("ema9_distance_pct"))
    vwap_dist = _safe_float(snapshot.get("vwap_distance_pct"))
    atr_pct   = _safe_float(snapshot.get("atr_percent") or snapshot.get("atr_pct"))
    rsi       = _safe_float(snapshot.get("rsi"))
    vol_delta = _safe_float(snapshot.get("volume_delta"))

    # Derivadas do snapshot
    if ema9_dist is not None and atr_pct and atr_pct > 0:
        d["distance_ema9_atr"] = ema9_dist / atr_pct
    if vwap_dist is not None and atr_pct and atr_pct > 0:
        d["distance_vwap_atr"] = vwap_dist / atr_pct

    if not candles:
        return d

    # candles[0] = mais recente (ORDER BY time DESC)
    closes  = [float(c["close"])  for c in candles]
    highs   = [float(c["high"])   for c in candles]
    lows    = [float(c["low"])    for c in candles]
    volumes = [float(c["volume"]) for c in candles]

    close_now = closes[0]

    # Variação de preço
    n6 = min(SHORT_LOOKBACK, len(closes))
    if n6 >= 2 and close_now > 0:
        c6_ago = closes[n6 - 1]
        if c6_ago > 0:
            d["price_change_pct_6c"] = (close_now - c6_ago) / c6_ago * 100

    if len(closes) >= LOOKBACK_CANDLES and close_now > 0:
        c12_ago = closes[LOOKBACK_CANDLES - 1]
        if c12_ago > 0:
            d["price_change_pct_12c"] = (close_now - c12_ago) / c12_ago * 100

    # Volume relativo (10 candles de contexto, excluindo o atual)
    context_vols = volumes[1:min(11, len(volumes))]
    if context_vols:
        mean_vol = sum(context_vols) / len(context_vols)
        if mean_vol > 0:
            d["rel_volume_10c"] = volumes[0] / mean_vol
            if len(context_vols) >= 2:
                std_vol = math.sqrt(
                    sum((v - mean_vol) ** 2 for v in context_vols) / len(context_vols)
                )
                if std_vol > 0:
                    d["volume_zscore_10c"] = (volumes[0] - mean_vol) / std_vol

    # Amplitude e posição da faixa (6c)
    if n6 >= 2:
        max_h = max(highs[:n6])
        min_l = min(lows[:n6])
        rng   = max_h - min_l
        if atr_pct and atr_pct > 0 and close_now > 0:
            atr_abs = close_now * atr_pct / 100
            if atr_abs > 0:
                d["high_low_range_6c_atr"] = rng / atr_abs
        if rng > 0:
            d["close_position_6c"] = (close_now - min_l) / rng

    # Divergência preço × RSI (contínua)
    pc6 = d.get("price_change_pct_6c")
    if pc6 is not None and rsi is not None:
        pc_sign = 1.0 if pc6 > 0.001 else (-1.0 if pc6 < -0.001 else 0.0)
        # +: preço e RSI concordam; -: divergência
        d["div_price_rsi"] = pc_sign * (rsi - 50.0) / 50.0

    # Divergência preço × volume_delta
    if pc6 is not None and vol_delta is not None and vol_delta != 0:
        pc_sign = 1.0 if pc6 > 0.001 else (-1.0 if pc6 < -0.001 else 0.0)
        vd_sign = 1.0 if vol_delta > 0 else -1.0
        d["div_price_volume_delta"] = pc_sign * vd_sign

    return d


async def ensure_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_experiment_features (
            shadow_trade_id  UUID PRIMARY KEY,
            symbol           TEXT NOT NULL,
            signal_at        TIMESTAMPTZ NOT NULL,
            features_json    JSONB NOT NULL,
            derived_json     JSONB,
            n_ohlcv_candles  INTEGER,
            run_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)


async def _print_stats(conn) -> None:
    row = await conn.fetchrow("""
        SELECT
            COUNT(*)                                          AS n_total,
            COUNT(*) FILTER (WHERE derived_json IS NOT NULL) AS n_derived,
            AVG(n_ohlcv_candles)                             AS avg_ohlcv,
            COUNT(*) FILTER (WHERE n_ohlcv_candles = 0)     AS n_no_ohlcv
        FROM ml_experiment_features
    """)
    print(f"\n[STATS] ml_experiment_features")
    print(f"  n_total [query]        = {int(row['n_total'] or 0)}")
    print(f"  n_with_derived [query] = {int(row['n_derived'] or 0)}")
    print(f"  avg_ohlcv_candles      = {float(row['avg_ohlcv'] or 0):.1f}")
    print(f"  n_sem_ohlcv [query]    = {int(row['n_no_ohlcv'] or 0)}")

    sample = await conn.fetchrow(
        "SELECT derived_json FROM ml_experiment_features WHERE derived_json IS NOT NULL LIMIT 1"
    )
    if sample:
        raw = sample["derived_json"]
        keys = sorted((json.loads(raw) if isinstance(raw, str) else dict(raw)).keys())
        print(f"\n  Features derivadas: {keys}")


async def main() -> None:
    conn = await connect()

    cfg_row = await conn.fetchrow("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml_research' AND is_active = true LIMIT 1
    """)
    raw_cfg = cfg_row["config_json"] if cfg_row else None
    cfg     = (json.loads(raw_cfg) if isinstance(raw_cfg, str) else dict(raw_cfg)) if raw_cfg else {}
    lookback_days = int(cfg.get("ml.lookback_days", 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    print("=" * 70)
    print("FASE 3 — Feature Engineering Direcional")
    print("L2 Validação Direcional (v2)")
    print(f"Executado em: {datetime.now(timezone.utc).isoformat()}")
    print(f"Janela: últimos {lookback_days} dias (cutoff={cutoff.date()})")
    print("=" * 70)

    await ensure_table(conn)

    trades = await conn.fetch("""
        SELECT st.id, st.symbol, st.created_at, st.features_snapshot
        FROM shadow_trades st
        JOIN ml_experiment_labels el ON el.shadow_trade_id = st.id
        LEFT JOIN ml_experiment_features ef ON ef.shadow_trade_id = st.id
        WHERE st.source = 'L1_SPECTRUM'
          AND el.future_return_30m_net IS NOT NULL
          AND st.created_at >= $1
          AND ef.shadow_trade_id IS NULL
        ORDER BY st.created_at ASC
    """, cutoff)

    print(f"\n  Trades a processar: {len(trades)}")
    if not trades:
        print("  Nenhum novo — mostrando estatísticas existentes.")
        await _print_stats(conn)
        await conn.close()
        return

    n_ok = n_no_snap = n_no_ohlcv = 0

    for trade in trades:
        snap_raw = trade["features_snapshot"]
        if not snap_raw:
            n_no_snap += 1
            continue
        snapshot = json.loads(snap_raw) if isinstance(snap_raw, str) else dict(snap_raw)

        # Snapshot limpo (sem stubs, apenas numéricos)
        clean: dict[str, float] = {}
        for k, v in snapshot.items():
            if k in STUB_FEATURES:
                continue
            fv = _safe_float(v)
            if fv is not None:
                clean[k] = fv

        # OHLCV lookback (12 candles antes do sinal)
        ohlcv_rows = await conn.fetch("""
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = $1
              AND timeframe = '5m'
              AND time < $2
            ORDER BY time DESC
            LIMIT 12
        """, trade["symbol"], trade["created_at"])

        candles = [dict(r) for r in ohlcv_rows]
        if not candles:
            n_no_ohlcv += 1

        derived = _compute_derived(clean, candles)

        await conn.execute("""
            INSERT INTO ml_experiment_features
                (shadow_trade_id, symbol, signal_at, features_json, derived_json, n_ohlcv_candles)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            ON CONFLICT (shadow_trade_id) DO UPDATE SET
                features_json   = EXCLUDED.features_json,
                derived_json    = EXCLUDED.derived_json,
                n_ohlcv_candles = EXCLUDED.n_ohlcv_candles,
                run_at          = NOW()
        """, trade["id"], trade["symbol"], trade["created_at"],
            json.dumps(clean), json.dumps(derived), len(candles))
        n_ok += 1

    print(f"  Processados:   {n_ok}")
    print(f"  Sem snapshot:  {n_no_snap}")
    print(f"  Sem OHLCV:     {n_no_ohlcv}")

    await _print_stats(conn)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
