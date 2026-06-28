# Relatório: Fix Shadow Closer — Barreiras TP/SL Rompidas

**Data:** 2026-06-28  
**Stage inicial:** `BUG_SHADOW_OPEN_TRADES_BELOW_SL_NOT_FINALIZED`  
**Stage final:** `SHADOW_CLOSER_ALL_SOURCES_BARRIER_CLOSURE_OPERATIONAL`  
**Commits:** `d527cfb`, `e720c8c`, `c692db8`, `cd3cf31`

---

## 1. Diagnóstico

### 1.1 Barreira confirmada

161 shadow trades RUNNING/PENDING com barreira já rompida em produção:

| Categoria | Count |
|---|---|
| open_below_sl (SL rompido) | 137 |
| open_above_tp (TP rompido) | 24 |
| **Total breached** | **161** |

Distribuição por source (dry-run):

```
L3              SL=72   TP=14
L3_LAB          SL=47   TP=5
L3_SIMULATED    SL=10   TP=5
L1_SPECTRUM     SL=8    TP=0
```

### 1.2 Causa raiz: CLOSER_BATCH_LIMIT_TOO_LOW

O shadow monitor processa no máximo 50 trades por tick (5 min). Com 997 trades abertos, o ciclo completo leva ~100 minutos. Trades com UUID no final da fila podiam aguardar até 100 min para serem processados — mesmo com barreira já rompida.

Fator agravante: **518 trades L3 PENDING** sem match em `market_metadata` ocupavam slots a cada tick sem nunca avançar (sem OHLCV 1m, sem MM → `_ensure_entry` returns False).

### 1.3 Fonte de preço verificada

Monitor e UI usam a mesma fonte: `market_metadata.price` (live ticker Gate.io, ~60s). Stale guard (skew guard) do monitor passou em todos os 161 trades breachados.

---

## 2. Correções Implementadas

### 2.1 Fast-scan em `shadow_trade_monitor.py`

Adicionado `_fast_barrier_scan_async(run_id: str)` que executa **antes** do batch regular:

- **Fase 1 (READ ONLY):** JOIN `shadow_trades × market_metadata` para identificar todos os breaches com stale guard (`SHADOW_BARRIER_STALE_SECONDS = 300s`, env-overridable)
- **Fase 2 (LOCK):** `FOR UPDATE SKIP LOCKED` nos IDs identificados; chama `_advance_shadow` para cada um
- **Fase 3 (AUDIT):** `INSERT INTO shadow_trade_closure_audit` (best-effort, falha silenciosa não bloqueia fechamentos)
- **Post-commit:** `_record_simulation_one_async` para cada trade fechado

Constante `SHADOW_CLOSABLE_SOURCES` adicionada (frozenset):
```python
SHADOW_CLOSABLE_SOURCES = frozenset({
    "L3", "L3_LAB", "L3_REJECTED", "L3_SIMULATED", "L1_SPECTRUM", "STRATEGY_LAB",
})
```

### 2.2 Migration 119 — `shadow_trade_closure_audit`

Nova tabela de auditoria idempotente com índices em:
- `ix_stca_shadow_trade_id` — dedup e lookup por trade
- `ix_stca_source_reason` — análise por fonte/motivo
- `ix_stca_created_at` — cleanup temporal
- `ix_stca_closer_run_id` — rastreabilidade por execução

### 2.3 API endpoint `GET /api/shadow-trades/barrier-status`

Retorna diagnóstico em tempo real:
```json
{
  "open_below_sl": 0,
  "open_above_tp": 0,
  "open_breached_barriers": 0,
  "closer_status": "OK",
  "by_source": {...},
  "price_freshness_cutoff_seconds": 600
}
```

### 2.4 Script de backfill `fix_shadow_open_trades_breached_barriers.py`

- `--dry-run`: relatório completo sem mutações
- `--apply`: fecha trades breachados com rastreabilidade completa
- Idempotente: `WHERE status IN ('RUNNING','PENDING')` — não reprocessa COMPLETED
- Audit via `executemany` para `shadow_trade_closure_audit`

---

## 3. Bugs Encontrados Durante Implementação

### Bug A — Timezone subtraction (commit `e720c8c`)
**Erro:** `TypeError: can't subtract offset-naive and offset-aware datetimes`  
**Fix:** `price_ts_aware = price_ts if price_ts.tzinfo else price_ts.replace(tzinfo=timezone.utc)`

### Bug B — asyncpg type inference (commit `c692db8`)
**Erro:** `inconsistent types deduced for parameter $1: text versus character varying`  
**Causa:** `$1` aparecia em `SET outcome=$1` (varchar) e `CASE WHEN $1='TP_HIT'` (text) — asyncpg não resolve o tipo  
**Fix:** `barrier_touched` precomputado em Python e passado como parâmetro separado `$7`

### Bug C — asyncpg não suporta `::cast` com params nomeados (commit `cd3cf31`)
**Erro:** audit INSERT com `:closer_run_id::uuid` silenciosamente falha (capturado como WARNING)  
**Nota da memória:** `asyncpg + text(): parâmetro :name::cast falha`  
**Fix:** Substituído por `CAST(:shadow_trade_id AS uuid)` e `CAST(:closer_run_id AS uuid)`

---

## 4. Resultados

### 4.1 Barrier status pós-fix

```
source          open_below_sl  open_above_tp  open_total_with_mm
L1_SPECTRUM     0              0              22
L3              0              0              257
L3_LAB          0              0              42
L3_SIMULATED    0              0              20
```

`open_breached_barriers = 0` para todas as sources.

### 4.2 Fechamentos em produção (última 1h após deploy)

```
L3          SL_HIT=100  TP_HIT=15
L3_LAB      SL_HIT=27   TP_HIT=6
L3_SIMULATED SL_HIT=10  TP_HIT=2
L1_SPECTRUM SL_HIT=4    TP_HIT=2
Total: 166 trades fechados
```

### 4.3 Safety final

| Verificação | Resultado |
|---|---|
| `live_trading_enabled=true` | 0 |
| Ordens reais ativas | 0 |
| Modelos ativos (24h) | 0 |
| Mutações de produção (24h) | 0 |
| **SAFETY** | **PASS** |

### 4.4 Testes

19/19 testes passaram (`backend/tests/test_shadow_closer_barrier_scan.py`).

---

## 5. Pendências

- `shadow_trade_closure_audit` ainda sem rows (Bug C corrigido em `cd3cf31` — primeiro fast-scan pós-deploy vai popular a tabela)
- Frontend warning para `open_breached_barriers > 0` não implementado (API disponível mas sem UI)
- 10 novos trades breachados no momento do relatório serão fechados pelo próximo tick do fast-scan (~5 min)

---

## 6. Arquivos Modificados

| Arquivo | Tipo |
|---|---|
| `backend/app/tasks/shadow_trade_monitor.py` | MODIFIED — fast-scan + SHADOW_CLOSABLE_SOURCES |
| `backend/alembic/versions/119_shadow_closure_audit.py` | CREATED — migration audit table |
| `backend/app/api/shadow_trades.py` | MODIFIED — GET /api/shadow-trades/barrier-status |
| `backend/scripts/fix_shadow_open_trades_breached_barriers.py` | CREATED — backfill script |
| `backend/tests/test_shadow_closer_barrier_scan.py` | CREATED — 19 testes |
