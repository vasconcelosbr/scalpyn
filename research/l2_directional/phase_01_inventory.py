"""
Phase 1 — Inventário de features
L2 Validação Direcional (v2)

Execução:
    railway run python -m research.l2_directional.phase_01_inventory

O que faz:
  Para cada feature em features_snapshot dos shadow_trades L1_SPECTRUM:
    - % disponível (não-null, não-NaN)
    - % constante (valor único no período)
    - variância
    - é_stub: variância ≈ 0 OU retorno de valor único constante
    - tipo: float | bool | str | missing

  Gate de saída: quais features de fluxo (taker_ratio, volume_delta, CVD...)
  estão VIVAS (não-stub). A Fase 3 só constrói derivados sobre features vivas.

  Referência spec: feature_engine.py:726,749 — taker_ratio e volume_delta
  potencialmente stubs. Este script confirma o estado atual no DB.
"""
from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from ._db import connect

# Features que a spec especificamente instrui verificar
FLOW_FEATURES = {"taker_ratio", "volume_delta", "cvd_slope", "taker_ratio_change"}
DIRECTIONAL_FEATURES = {
    "ema9_gt_ema21", "ema50_gt_ema200", "ema_distance_pct",
    "ema50_distance_pct", "ema200_distance_pct",
    "adx", "adx_acceleration", "macd_histogram_pct", "macd_histogram_slope",
    "rsi",
}
VOLATILITY_FEATURES = {"bb_width", "atr_pct", "spread_pct"}
VOLUME_FEATURES = {"volume_24h_usdt", "orderbook_depth_usdt", "volume_spike"}
VWAP_FEATURES = {"vwap_distance_pct"}

STUB_VARIANCE_THRESHOLD = 1e-9  # variância abaixo disso = stub


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _classify_group(feature_name: str) -> str:
    if feature_name in FLOW_FEATURES:
        return "fluxo"
    if feature_name in DIRECTIONAL_FEATURES:
        return "direcional"
    if feature_name in VOLATILITY_FEATURES:
        return "volatilidade"
    if feature_name in VOLUME_FEATURES:
        return "volume"
    if feature_name in VWAP_FEATURES:
        return "vwap"
    return "outro"


async def main() -> None:
    conn = await connect()

    # Ler config de pré-registro
    cfg_row = await conn.fetchrow("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml_research' AND is_active = true LIMIT 1
    """)
    cfg: dict = dict(cfg_row["config_json"]) if cfg_row else {}
    lookback_days = int(cfg.get("ml.lookback_days", 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    print("=" * 70)
    print("FASE 1 — Inventário de Features")
    print("L2 Validação Direcional (v2)")
    print(f"Executado em: {datetime.now(timezone.utc).isoformat()}")
    print(f"Janela: últimos {lookback_days} dias (cutoff={cutoff.date()})")
    print("=" * 70)

    # Carregar features_snapshot de todos os shadows L1_SPECTRUM
    rows = await conn.fetch("""
        SELECT features_snapshot
        FROM shadow_trades
        WHERE source = 'L1_SPECTRUM'
          AND features_snapshot IS NOT NULL
          AND features_snapshot::text <> '{}'
          AND created_at >= $1
        ORDER BY created_at ASC
    """, cutoff)

    n_rows = len(rows)
    print(f"\n  Registros carregados [query shadow_trades]: {n_rows}")

    if n_rows == 0:
        print("\n  ⚠️  Sem dados — aguardar acúmulo de shadow_trades L1_SPECTRUM.")
        await conn.close()
        return

    # Agregar valores por feature
    feature_values: dict[str, list[float]] = defaultdict(list)
    feature_null_count: dict[str, int] = defaultdict(int)
    all_features: set[str] = set()

    for row in rows:
        snap = row["features_snapshot"]
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except Exception:
                continue
        if not isinstance(snap, dict):
            continue
        all_features.update(snap.keys())
        for k, v in snap.items():
            fv = _safe_float(v)
            if fv is None:
                feature_null_count[k] += 1
            else:
                feature_values[k].append(fv)

    # Calcular estatísticas por feature
    stats: list[dict] = []
    for feat in sorted(all_features):
        vals = feature_values.get(feat, [])
        nulls = feature_null_count.get(feat, 0)
        n_total_feat = len(vals) + nulls
        avail_pct = len(vals) / n_total_feat if n_total_feat > 0 else 0.0

        if len(vals) == 0:
            variance = 0.0
            n_unique = 0
            const_pct = 1.0
        else:
            mean = sum(vals) / len(vals)
            variance = sum((x - mean) ** 2 for x in vals) / len(vals)
            n_unique = len(set(round(v, 6) for v in vals))
            const_pct = 1.0 if n_unique <= 1 else 0.0

        is_stub = (variance < STUB_VARIANCE_THRESHOLD) or (const_pct == 1.0 and len(vals) > 0)
        group = _classify_group(feat)

        stats.append({
            "feature": feat,
            "n_total": n_total_feat,
            "n_valid": len(vals),
            "avail_pct": avail_pct,
            "null_pct": nulls / n_total_feat if n_total_feat > 0 else 0.0,
            "const_pct": const_pct,
            "n_unique": n_unique,
            "variance": variance,
            "is_stub": is_stub,
            "group": group,
        })

    # Ordenar: stubs primeiro, depois por grupo, depois por nome
    stats.sort(key=lambda x: (not x["is_stub"], x["group"], x["feature"]))

    n_stubs = sum(1 for s in stats if s["is_stub"])
    n_live = len(stats) - n_stubs

    print(f"  Features únicas encontradas: {len(stats)}")
    print(f"  Stubs (variância≈0 ou constante): {n_stubs}")
    print(f"  Features vivas (não-stub):         {n_live}")

    # Tabela de inventário
    print("\n" + "-" * 100)
    header = f"{'feature':<35} {'grupo':<14} {'disponível':>10} {'% nulos':>8} {'variância':>12} {'n_único':>8} {'stub':>6}"
    print(header)
    print("-" * 100)

    for s in stats:
        stub_mark = "⚠️ SIM" if s["is_stub"] else "não"
        var_str = f"{s['variance']:.2e}" if s["variance"] < 0.01 else f"{s['variance']:.4f}"
        print(
            f"{s['feature']:<35} {s['group']:<14} {s['avail_pct']*100:>9.1f}% "
            f"{s['null_pct']*100:>7.1f}% {var_str:>12} {s['n_unique']:>8} {stub_mark:>8}"
        )

    # Gate de saída: features de fluxo vivas
    print("\n" + "=" * 70)
    print("GATE DE SAÍDA — Features de Fluxo Vivas")
    print("(a Fase 3 só constrói derivados sobre features confirmadas não-stub)")
    print("=" * 70)

    flow_status: list[tuple[str, str]] = []
    for feat in sorted(FLOW_FEATURES | {"cvd_slope", "taker_ratio_change"}):
        s_list = [s for s in stats if s["feature"] == feat]
        if not s_list:
            flow_status.append((feat, "NÃO DISPONÍVEL [ausente em features_snapshot]"))
        else:
            s = s_list[0]
            if s["is_stub"]:
                flow_status.append((feat, f"❌ STUB (variância={s['variance']:.2e}, n_único={s['n_unique']})"))
            else:
                flow_status.append((feat, f"✅ VIVA (avail={s['avail_pct']*100:.1f}%, var={s['variance']:.4f})"))

    for feat, status in flow_status:
        print(f"  {feat:<30} {status}")

    live_flow = [f for f, st in flow_status if "✅" in st]
    print(f"\n  Features de fluxo vivas para Fase 3: {live_flow if live_flow else 'NENHUMA'}")

    # Features direcionais
    print("\n" + "=" * 70)
    print("FEATURES DIRECIONAIS — estado atual")
    print("=" * 70)
    for feat in sorted(DIRECTIONAL_FEATURES):
        s_list = [s for s in stats if s["feature"] == feat]
        if not s_list:
            print(f"  {feat:<35} NÃO DISPONÍVEL")
        else:
            s = s_list[0]
            mark = "❌ STUB" if s["is_stub"] else "✅"
            var_str = f"{s['variance']:.2e}" if s["variance"] < 0.01 else f"{s['variance']:.4f}"
            print(f"  {feat:<35} {mark}  avail={s['avail_pct']*100:.1f}%  var={var_str}")

    # Ledger
    print("\n" + "=" * 70)
    print("LEDGER DE EVIDÊNCIAS")
    print("=" * 70)
    print(f"{'NÚMERO':<30} | {'ORIGEM':<35} | VALOR LITERAL")
    print("-" * 90)
    print(f"{'n_rows_carregados':<30} | [query shadow_trades]              | {n_rows}")
    print(f"{'n_features_unicas':<30} | [calc: set(all features_snapshot)] | {len(stats)}")
    print(f"{'n_stubs':<30} | [calc: var<{STUB_VARIANCE_THRESHOLD}]        | {n_stubs}")
    print(f"{'n_live':<30} | [calc: n_features - n_stubs]       | {n_live}")
    for feat, status in flow_status:
        print(f"{'flow:'+feat:<30} | [calc inventário]                  | {status[:60]}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
