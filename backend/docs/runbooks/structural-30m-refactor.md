# Structural 30m Refactor (Task #262)

## Resumo

O pipeline estrutural foi separado de `collect_all` para uma cadência
dedicada de 30 minutos, alinhada ao fechamento da candle Gate.io.

### Antes

```
collect_all (60s)
  ├─ fetch_all_tickers → bulk UPSERT market_metadata
  ├─ for symbol in symbols:                        ← OHLCV 1h por símbolo
  │     fetch_ohlcv(1h) → INSERT ohlcv
  └─ task_dispatch.enqueue("compute_indicators.compute") (1h)
        └─ task_dispatch.enqueue("compute_scores.score")
              └─ task_dispatch.enqueue("evaluate_signals.evaluate")
```

Custo: ~5 700 chamadas OHLCV/h ao Gate.io, contenção em `ohlcv` /
`market_metadata`, `command_timeout` overrun, transações órfãs.

### Depois

```
collect_all (60s)                              # ticker/metadata only
  └─ fetch_all_tickers → bulk UPSERT market_metadata

collect_structural_30m (crontab 0,30 UTC)      # NOVO
  ├─ for symbol in active_spot:
  │     fetch_ohlcv(30m) → INSERT ohlcv
  └─ task_dispatch.enqueue("compute_indicators.compute_30m")
        └─ task_dispatch.enqueue("compute_scores.score")
              └─ task_dispatch.enqueue("evaluate_signals.evaluate")

compute_indicators.compute (1h)                # DEPRECATED stub
  └─ no-op (mantido apenas para satisfazer invariant #4 do lint
            até a próxima limpeza de código).
```

## Decisão arquitetural — Opção A

**Escolhida:** `scheduler_group = "structural"` (mesma tag do antigo
path 1h). Zero alteração em `indicator_merge` / `indicators_provider` —
o read-side continua mesclando structural+microstructure pelo grupo,
sem distinguir 1h vs 30m. Quando o stub `compute()` for removido, o tag
"structural" passa a representar APENAS dados 30m (transição transparente
para os consumidores).

Alternativa rejeitada (Opção B): `scheduler_group = "structural_30m"`
exigiria estender `indicator_merge` para reconhecer o novo grupo, com
risco de regressão no read-path durante o rolling deploy.

## Cost guards

`collect_structural_30m.run` e `compute_indicators.compute_30m`:
* `time_limit = 600s`, `soft_time_limit = 540s` (mesmo perfil do
  `_STRUCTURAL_GUARDS`).
* `rate_limit = "2/h"` (alinha com `crontab(minute="0,30")` — duas
  execuções/hora exatas).
* `acks_late = False` (`_NO_REQUEUE_ON_WORKER_LOSS`) — task idempotente
  e beat-driven; perda em SIGKILL é recuperada no próximo tick (≤30 min)
  sem requeue infinito.

## Operação

### Verificar que beat dispara nos minutos certos

```
celery -A app.tasks.celery_app inspect scheduled
```

Deve aparecer `app.tasks.collect_structural_30m.run` com ETA em
`HH:00:00` ou `HH:30:00`.

### Verificar fluxo end-to-end

```sql
-- candles 30m frescos
SELECT timeframe, COUNT(DISTINCT symbol), MAX(time)
FROM ohlcv WHERE timeframe = '30m'
GROUP BY timeframe;

-- indicadores estruturais frescos
SELECT timeframe, scheduler_group, COUNT(DISTINCT symbol), MAX(time)
FROM indicators WHERE timeframe = '30m'
GROUP BY timeframe, scheduler_group;
```

Esperado: ambas queries devem refletir o último candle close (00 ou 30).

### Topology (Cloud Run, Task #239)

A nova task roda na fila `structural` — **mesmo worker**
`scalpyn-worker-structural` que já consome `collect_all` /
`compute_scores.score` / `pipeline_scan.scan`. Nenhum novo serviço
Cloud Run necessário.

### Rollback

#### Passo 1 — Reverter código

Reverter o commit da Task #262. `collect_all` volta a fazer OHLCV 1h
+ chain para `compute()`. O stub `compute()` ainda aceita o chain
(apenas loga DEPRECATED e retorna), então o rollback é seguro mesmo
com workers mistos durante o rolling deploy.

#### Passo 2 — Limpar dados 30m (opcional)

Se for necessário remover candles e indicadores 30m gerados pelo novo
pipeline (por exemplo, se um bug no `_compute_30m_async` poluiu a
tabela), substitua `<DEPLOY_TS>` pelo timestamp UTC do deploy original
(formato `'2026-05-11 14:00:00+00'`):

```sql
-- Apaga indicadores 30m gerados pelo novo pipeline.
DELETE FROM indicators
 WHERE timeframe = '30m'
   AND scheduler_group = 'structural'
   AND time > '<DEPLOY_TS>';

-- Apaga candles 30m persistidos pelo novo collector.
DELETE FROM ohlcv
 WHERE timeframe = '30m'
   AND time > '<DEPLOY_TS>';
```

Rodar via Cloud Shell em janela de manutenção (Cloud SQL,
`--database=scalpyn`). Reabilitar o consumo do worker estrutural após
o `DELETE` para evitar reinserção concorrente.

#### Checklist de verificação pós-rollback

```sql
-- 1. Indicadores 1h voltaram a ser persistidos.
SELECT MAX(time) FROM indicators
 WHERE timeframe = '1h' AND scheduler_group = 'structural';

-- 2. OHLCV 1h voltou a chegar.
SELECT MAX(time) FROM ohlcv WHERE timeframe = '1h';

-- 3. Nenhum 30m novo aparecendo (se o DELETE foi feito).
SELECT COUNT(*) FROM indicators
 WHERE timeframe = '30m' AND time > NOW() - INTERVAL '30 minutes';
```

## Limpeza pendente (próxima task)

* Remover `compute_indicators.compute` (stub) e sua entrada em
  `TASK_ROUTES` / `TASK_ANNOTATIONS` após ≥48h estável em prod.
* Remover `_REQUIRED_OHLCV_COLUMNS` de `collect_market_data.py` se
  `collect_5m` parar de usá-lo (atualmente ainda é consumido).
