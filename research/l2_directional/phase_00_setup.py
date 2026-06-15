"""
Phase 0 — Setup de dados, custos e pré-registro
L2 Validação Direcional (v2) — experimento read-only sobre shadow_trades.

Execução:
    railway run python -m research.l2_directional.phase_00_setup

O que faz:
  0-A. Cria/valida as chaves de pré-registro em config_profiles (config_type='ml_research').
       Chaves existentes NÃO são sobrescritas — o operador confirma os valores uma vez e eles
       ficam travados até o veredito da Fase 9.
  0-B. Consulta shadow_trades source='L1_SPECTRUM' para medir volume e janela de dados.
  0-C. Verifica poder estatístico: Top 10% projetado >= ml.min_bucket_n?
       Se não: PARE — acumule mais dados antes de treinar qualquer modelo.
  0-D. Imprime relatório com todas as fontes etiquetadas (regra EVIDÊNCIA-OU-SILÊNCIO).
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

from ._db import connect

# ── Pré-registro — defaults que o operador deve confirmar antes da Fase 4 ─────
# Regra: defaults entram no DB se a chave não existir ainda.
#        Se a chave existir, o valor no DB prevalece (já foi confirmado).
# Alterar um valor pós-treino = fishing de resultado → PROIBIDO pela spec.
PREREGISTRATION_DEFAULTS: dict = {
    # Modelo de custo Gate.io (confirmar antes de travar)
    # Gate.io taker fee = 0.20% por perna (público em gateio.com/fee)
    # Round-trip = entrada taker + saída taker = 2 × 0.20% = 0.40%
    "ml.cost_roundtrip_pct": 0.0040,
    # Slippage por perna — estimativa conservadora; confirmar empiricamente com spread médio L1_SPECTRUM
    "ml.slippage_pct": 0.0005,
    # cost_total = cost_roundtrip + 2 × slippage = 0.0040 + 0.0010 = 0.0050 (calculado, não armazenado)

    # Horizontes de retorno forward (Fase 2 e 4)
    "ml.future_return_horizon_min": 30,      # primário
    "ml.future_return_horizon_sec_min": 60,  # secundário

    # Power check
    "ml.min_bucket_n": 50,                   # n mínimo por bucket para ser reportável

    # Critérios de GO (travados — não tocar até Fase 9)
    "ml.go_spearman_ic": 0.03,              # IC Spearman rank > 0.03 E p < 0.05 no test set
    "ml.go_topdecile_ev": 0.0,             # EV líquido Top 10% > 0 E IC inferior > EV da base
    # go_barrier_survives: verificado na Fase 5.4 (simulação TP/SL) — sem threshold numérico fixo

    # Janela de dados com recência (Fase 0.1)
    "ml.lookback_days": 90,                 # janela máxima (dias)
    "ml.recency_lambda": 0.0231,            # exp(-lambda * 30) ≈ 0.50 → half-life ~30d
    "ml.recency_min_weight": 0.05,          # amostras com peso < 5% são descartadas

    # Marcador de travamento (muda para true APÓS confirmação com Ricardo)
    "ml.preregistration_locked": False,
    "ml.preregistration_date": "",          # preencher com data de confirmação
}


async def main() -> None:
    conn = await connect()

    print("=" * 70)
    print("FASE 0 — Setup, Pré-Registro e Poder Estatístico")
    print("L2 Validação Direcional (v2)")
    print(f"Executado em: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    # ── 0-A. Pré-registro: criar/validar config no DB ───────────────────────
    print("\n[A] PRÉ-REGISTRO DE CONFIG\n")

    existing_row = await conn.fetchrow("""
        SELECT id, config_json
        FROM config_profiles
        WHERE config_type = 'ml_research' AND is_active = true
        LIMIT 1
    """)

    if existing_row is None:
        # Criar novo registro
        new_config = PREREGISTRATION_DEFAULTS.copy()
        await conn.execute("""
            INSERT INTO config_profiles (config_type, config_json, is_active)
            VALUES ('ml_research', $1::jsonb, true)
        """, json.dumps(new_config))
        print("  [DB] Criado: config_profiles config_type='ml_research'")
        final_config = new_config
        new_keys = list(new_config.keys())
        kept_keys: list = []
    else:
        existing_config: dict = dict(existing_row["config_json"])
        # Adicionar apenas chaves faltantes (não sobrescrever valores confirmados)
        new_keys = [k for k in PREREGISTRATION_DEFAULTS if k not in existing_config]
        kept_keys = [k for k in PREREGISTRATION_DEFAULTS if k in existing_config]
        if new_keys:
            merged = {**PREREGISTRATION_DEFAULTS, **existing_config}  # existing wins
            await conn.execute("""
                UPDATE config_profiles
                SET config_json = $1::jsonb
                WHERE config_type = 'ml_research' AND is_active = true
            """, json.dumps(merged))
            print(f"  [DB] Atualizado: {len(new_keys)} chave(s) adicionada(s), {len(kept_keys)} mantida(s)")
            final_config = merged
        else:
            print(f"  [DB] Sem mudanças: todas as {len(kept_keys)} chaves já existem")
            final_config = existing_config

    locked = final_config.get("ml.preregistration_locked", False)
    lock_date = final_config.get("ml.preregistration_date", "")
    print(f"\n  Travamento: {'TRAVADO em ' + lock_date if locked else 'NÃO TRAVADO ⚠️  (confirmar valores antes da Fase 4)'}")

    print("\n  Valores de pré-registro [DB: config_type='ml_research']:")
    key_groups = [
        ("Modelo de custo", ["ml.cost_roundtrip_pct", "ml.slippage_pct"]),
        ("Horizontes", ["ml.future_return_horizon_min", "ml.future_return_horizon_sec_min"]),
        ("Power check", ["ml.min_bucket_n"]),
        ("Critérios GO", ["ml.go_spearman_ic", "ml.go_topdecile_ev"]),
        ("Janela de dados", ["ml.lookback_days", "ml.recency_lambda", "ml.recency_min_weight"]),
    ]
    for group_name, keys in key_groups:
        print(f"\n  {group_name}:")
        for k in keys:
            v = final_config.get(k, "NÃO DISPONÍVEL")
            status = " [NOVO]" if k in new_keys else ""
            print(f"    {k} = {v}{status}")

    cost_roundtrip = float(final_config.get("ml.cost_roundtrip_pct", 0.004))
    slippage = float(final_config.get("ml.slippage_pct", 0.0005))
    cost_total = cost_roundtrip + 2 * slippage
    print(f"\n  cost_total [calc: cost_roundtrip + 2×slippage] = {cost_total:.4f} ({cost_total*100:.3f}%)")

    # ── 0-B. Volume de dados L1_SPECTRUM ────────────────────────────────────
    lookback_days = int(final_config.get("ml.lookback_days", 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    print(f"\n[B] VOLUME DE DADOS — shadow_trades source='L1_SPECTRUM'\n")
    print(f"  Janela: últimos {lookback_days} dias (cutoff={cutoff.date()})")

    volume_row = await conn.fetchrow("""
        SELECT
            COUNT(*)                                           AS n_total,
            COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT'))
                                                               AS n_closed,
            COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT'))
                                                               AS n_with_outcome,
            COUNT(*) FILTER (WHERE outcome = 'TP_HIT')        AS n_tp,
            COUNT(*) FILTER (WHERE outcome = 'SL_HIT')        AS n_sl,
            MIN(created_at)                                    AS oldest,
            MAX(created_at)                                    AS newest,
            EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at))) / 86400.0
                                                               AS span_days
        FROM shadow_trades
        WHERE source = 'L1_SPECTRUM'
          AND created_at >= $1
    """, cutoff)

    n_total = int(volume_row["n_total"] or 0)
    n_closed = int(volume_row["n_closed"] or 0)
    n_outcome = int(volume_row["n_with_outcome"] or 0)
    n_tp = int(volume_row["n_tp"] or 0)
    n_sl = int(volume_row["n_sl"] or 0)
    span_days = float(volume_row["span_days"] or 0)
    oldest = volume_row["oldest"]
    newest = volume_row["newest"]

    print(f"  n_total   [query] = {n_total}")
    print(f"  n_closed  [query] = {n_closed}  (outcome IN TP_HIT, SL_HIT, TIMEOUT)")
    print(f"  n_outcome [query] = {n_outcome}  (outcome IN TP_HIT, SL_HIT — usável para treino)")
    print(f"  n_tp      [query] = {n_tp}")
    print(f"  n_sl      [query] = {n_sl}")
    wr_raw = n_tp / n_outcome if n_outcome > 0 else 0.0
    print(f"  win_rate  [calc: n_tp/n_outcome] = {wr_raw:.3f} ({wr_raw*100:.1f}%)")
    print(f"  span_days [query] = {span_days:.1f} dias")
    print(f"  oldest    [query] = {oldest}")
    print(f"  newest    [query] = {newest}")

    # Recency weighting — n efetivo
    lambda_r = float(final_config.get("ml.recency_lambda", 0.0231))
    min_weight = float(final_config.get("ml.recency_min_weight", 0.05))
    if n_outcome > 0:
        weights_row = await conn.fetch("""
            SELECT EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0 AS age_days
            FROM shadow_trades
            WHERE source = 'L1_SPECTRUM'
              AND outcome IN ('TP_HIT', 'SL_HIT')
              AND created_at >= $1
        """, cutoff)
        weights = [math.exp(-lambda_r * float(r["age_days"])) for r in weights_row]
        weights_kept = [w for w in weights if w >= min_weight]
        n_effective = sum(weights_kept)
        n_dropped_weight = len(weights) - len(weights_kept)
        print(f"\n  Recency weighting (lambda={lambda_r}, min_weight={min_weight}):")
        print(f"    n_bruto   [query] = {len(weights)}")
        print(f"    n_efetivo [calc: Σexp(-λ×age)] = {n_effective:.1f}")
        print(f"    n_dropped [calc: peso < {min_weight}] = {n_dropped_weight}")
    else:
        n_effective = 0.0

    # ── 0-C. Poder estatístico (Gate: Top 10% n >= min_bucket_n) ────────────
    min_bucket_n = int(final_config.get("ml.min_bucket_n", 50))

    print(f"\n[C] VERIFICAÇÃO DE PODER ESTATÍSTICO\n")

    # Projeção do n no bucket Top 10%
    n_top10_projected = n_outcome * 0.10
    n_eff_top10 = n_effective * 0.10
    print(f"  min_bucket_n     [config] = {min_bucket_n}")
    print(f"  n_outcome        [query]  = {n_outcome}")
    print(f"  Top 10% projetado [calc: n_outcome × 0.10] = {n_top10_projected:.1f}")
    print(f"  Top 10% efetivo   [calc: n_eff × 0.10]    = {n_eff_top10:.1f}")

    gate_passed = n_top10_projected >= min_bucket_n
    print(f"\n  Gate de saída: Top 10% >= {min_bucket_n}?  {'✅ PASSOU' if gate_passed else '❌ INSUFICIENTE'}")

    if not gate_passed:
        # Estimar quando teremos n suficiente (baseado na taxa diária atual)
        trades_per_day = n_outcome / max(span_days, 0.1)
        n_needed = min_bucket_n * 10 - n_outcome  # n total para Top 10% >= min_bucket_n
        days_needed = n_needed / max(trades_per_day, 0.1)
        arrive_date = (datetime.now(timezone.utc) + timedelta(days=days_needed)).date()
        print(f"\n  ⚠️  PARAR — n insuficiente para treino direcional.")
        print(f"     Taxa atual:   {trades_per_day:.1f} trades fechados/dia [calc: n_outcome/span_days]")
        print(f"     N necessário: {min_bucket_n*10} (para Top 10% = {min_bucket_n})")
        print(f"     Faltam:       {max(0, n_needed):.0f} trades")
        if trades_per_day > 0:
            print(f"     Estimativa:   {days_needed:.0f} dias ({arrive_date}) — reavaliar nessa data")
        else:
            print(f"     Estimativa:   NÃO DISPONÍVEL (taxa=0 — L1_SPECTRUM pode não estar capturando)")
    else:
        print(f"\n  ✅ Prosseguir para Fase 1 (Inventário de features).")

    # ── Ledger de Evidências ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("LEDGER DE EVIDÊNCIAS")
    print("=" * 70)
    print(f"{'NÚMERO':<30} | {'ORIGEM':<30} | VALOR LITERAL")
    print("-" * 90)
    print(f"{'n_total':<30} | [query shadow_trades]         | {n_total}")
    print(f"{'n_outcome':<30} | [query shadow_trades]         | {n_outcome}")
    print(f"{'n_tp':<30} | [query shadow_trades]         | {n_tp}")
    print(f"{'n_sl':<30} | [query shadow_trades]         | {n_sl}")
    print(f"{'win_rate_bruto':<30} | [calc: n_tp/n_outcome]        | {wr_raw:.4f}")
    print(f"{'span_days':<30} | [query shadow_trades]         | {span_days:.1f}")
    print(f"{'n_efetivo':<30} | [calc: Σexp(-λ×age)]          | {n_effective:.1f}")
    print(f"{'top10_projetado':<30} | [calc: n_outcome×0.10]        | {n_top10_projected:.1f}")
    print(f"{'cost_total':<30} | [calc: roundtrip+2×slip]      | {cost_total:.4f}")
    print(f"{'cost_roundtrip_pct':<30} | [config ml_research]          | {cost_roundtrip:.4f}")
    print(f"{'slippage_pct':<30} | [config ml_research]          | {slippage:.4f}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
