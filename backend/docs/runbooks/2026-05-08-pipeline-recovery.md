# 2026-05-08 — Pipeline Recovery (3 incidentes correlacionados)

Três incidentes do mesmo dia compartilham timeline e código. Este runbook é o consolidado dos 3 gotchas removidos do `replit.md` (mantido apenas como pointer).

## Timeline

| Hora UTC | Evento |
|---|---|
| 2026-05-07 21:15 | Última candle 5m persistida — pipeline 5m congela |
| 2026-05-08 ~17:00 | Operador percebe atraso no dashboard (`atraso ingest 23h+`) |
| 2026-05-08 ~17:30 | Login Vercel falhando em massa (sem relação direta com pipeline, mesmo dia) |
| 2026-05-08 ~18:30 | Diagnóstico: `_MICRO_GUARDS` calibrado pra pool pequeno + ingress `internal-and-cloud-load-balancing` em service sem LB |
| 2026-05-08 ~21:00 | Deploy `_MICRO_GUARDS` 480/420s + ingress=all |
| 2026-05-08 ~21:35 | Pipeline volta a rodar mas surge novo sintoma: TXs órfãs `idle in transaction` segurando row-locks |
| 2026-05-08 ~22:00 | Deploy `idle_in_transaction_session_timeout=300s` + remoção de UPSERT redundante de `market_metadata` |
| 2026-05-08 22:01 | Confirmado em prod: candles 5m fresh (`age 6min32s`, `rows_15m=171`) |

---

## Gotcha 1 — `_MICRO_GUARDS` time_limit 480s/soft 420s, NÃO 180/150

`collect_5m` e `compute_5m` rodam o universo inteiro (95+ símbolos × ~1s/cada Gate.io). Limite original 150s foi calibrado pra pool pequeno e estourou silenciosamente quando pool cresceu pra 95 ativos.

**Combinação fatal com `acks_late=False` (gotcha #245)**: `SoftTimeLimitExceeded` mata a task sem re-queue, beat só re-roda em 5min, mesmo timeout, mesma queda → loop silencioso por 24h enquanto `collect_all` (structural, 540s) continuava persistindo `timeframe=1h` normalmente.

**Sintomas que confundem**:
- Dashboard mostra "SÍMBOLOS 0 / CANDLES (15M) 0 / ATRASO INGEST 23h+"
- MAS `pool_coins.is_active=true` retorna 95
- `[COLLECT][OK]` aparece nos logs do worker-structural
- `ohlcv` tem 1h fresca mas 5m parada

Raiz só fica visível em:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="scalpyn-worker-micro" AND textPayload:"Soft time limit"' \
  --limit=10 --project=clickrate-477217
```

Fix em `celery_app.py:160-173`. **NÃO baixar de volta** se o pool crescer mais — ajustar pra cima até o teto de 540s (structural). Acima disso: paralelizar/sharding do universo, não esticar timeout.

---

## Gotcha 2 — `idle_in_transaction_session_timeout=300s` no Celery engine (`lock_timeout=30s` REVERTIDO em 2026-05-09)

> **Atualização 2026-05-09**: o `lock_timeout=30000` original deste gotcha foi **REMOVIDO** após gerar regressão em <12h em prod. Sintoma: hot symbols (ETH_USDT, GT_USDT, FLOKI_USDT, NEXO_USDT) falhando cronicamente com `LockNotAvailableError` a cada ciclo de `collect_5m`/`collect_all`, `pipeline_scan.scan` re-queueing, backlog `execution=2446` em 14h, `atraso ingest 14h13m`. Causa: 30s é insuficiente pra contenção legítima entre 5 workers Cloud Run + Gate.io WS UPSERTing nas mesmas hot rows. **`command_timeout=180s` é o teto correto** — absorve contenção transiente sem starvar os hot symbols. Detalhes do incidente de 2026-05-09 na seção **Apêndice A** abaixo. Manter o `idle_in_transaction_session_timeout=300s`, esse continua válido (defesa contra TX órfã de container morto, problema diferente).


Sequela do bump do gotcha 1. Subir `_MICRO_GUARDS` revelou um problema mais profundo que era mascarado pelo `SoftTimeLimitExceeded`: container Cloud Run morto mid-transaction (SIGTERM/OOM/network blip) deixa o backend Postgres `idle in transaction` indefinidamente segurando row-locks.

**Observado em prod**: PID 601816 com `RELEASE SAVEPOINT sa_savepoint_38` por **11 min**. Próximas execuções de `collect_5m`/`collect_all` enfileiraram em `market_metadata` esperando o lock, estouraram `command_timeout=180s` → `canceling statement due to user request` → poison da TX → bola de neve infinita.

**Fix em `database.py:_celery_connect_args.server_settings`**:
- `idle_in_transaction_session_timeout=300000` (5min, ms): Postgres mata qualquer session idle > 5min mid-tx. Elimina órfãs de containers mortos.
- ~~`lock_timeout=30000` (30s, ms)~~: **REVERTIDO 2026-05-09** — gerou regressão em hot symbols. Ver Apêndice A.

Combinado com `acks_late=False` (gotcha #245), beat re-roda em ≤5min com estado limpo.

**Mudança paralela no mesmo deploy**: removido UPSERT redundante de `market_metadata` no `_collect_5m_async` (linha 712, agora só comentário) — `collect_all` (1h) + Gate.io WS tickers + SAVEPOINT do orderbook já mantêm `price` fresh. O UPSERT extra era a fonte mais hot da contenção entre worker-micro e worker-structural. **NÃO re-adicionar** sem nova estratégia de mitigação (SKIP LOCKED, session separada).

**Recovery manual quando o problema reaparecer** (logs: `pg_stat_activity` mostrando TX `idle in transaction` > 2min):

```sql
-- Cloud Shell — destrava em segundos
SELECT pid, NOW() - state_change AS idle_for, pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname='scalpyn'
  AND state IN ('idle in transaction','idle in transaction (aborted)')
  AND NOW() - state_change > INTERVAL '2 minutes';
```

**Verificação pós-recovery**:

```bash
gcloud sql connect scalpyn --user="$PGUSER" --database=scalpyn --project=clickrate-477217 <<'SQL'
SELECT timeframe, MAX(time), NOW() - MAX(time) AS age,
       COUNT(*) FILTER (WHERE time > NOW() - INTERVAL '15 min') AS rows_15m
FROM ohlcv WHERE timeframe IN ('5m','1h') GROUP BY timeframe;
SQL
```

Esperado: `5m` com `age < 10min` e `rows_15m > 0`.

> **Pitfall do `gcloud sql connect`**: `$PGDATABASE` extraído do DATABASE_URL pode vir como `postgres` (DB default do Cloud SQL) em vez de `scalpyn`. **Sempre passar `--database=scalpyn` explicitamente**. Se não, queries dão `relation "ohlcv" does not exist` mesmo com pipeline funcionando.

---

## Gotcha 3 — `scalpyn` API ingress = `all`, NÃO `internal-and-cloud-load-balancing`

Task #167 originalmente setou ingress do service `scalpyn` (API) pra `internal-and-cloud-load-balancing` planejando provisionar Cloud LB depois. **O LB nunca foi provisionado.**

Frontend Vercel (`scalpyn.vercel.app`) bate direto em `https://scalpyn-wm56dfqgta-uc.a.run.app` via `BACKEND_URL` no `frontend/app/api/[...path]/route.ts` — com ingress restrito, o GFE retorna **404 (não 403!)** em TUDO antes do request chegar no container.

**Sintomas que confundem**:
- Container `Ready=True`, traffic 100%
- Logs mostram `DB pool stats` regulares (lifespan rodando)
- MAS zero logs de HTTP access (porque request nem chega)
- `curl -v` mostra `server: Google Frontend` no 404
- Login falha em massa com mensagem genérica "Login failed" no front

**Fix imediato**:
```bash
gcloud run services update scalpyn --region=us-central1 --ingress=all
```

**Fix permanente**: `cloudbuild.yaml:93-94` agora deploya com `--ingress=all`.

Os 4 services não-API (`scalpyn-worker-{micro,structural,execution}` + `scalpyn-beat`) MANTÊM `--ingress=internal` — eles não recebem HTTP externo.

**Se um dia provisionar Cloud LB de verdade**: trocar de volta pra `internal-and-cloud-load-balancing` E repointar `BACKEND_URL` na Vercel pro hostname do LB no MESMO deploy — nunca um sem o outro.

---

## Apêndice A — Regressão `lock_timeout=30s` (2026-05-09, ~12h após o deploy original)

### Timeline

| Hora UTC | Evento |
|---|---|
| 2026-05-08 ~22:00 | Deploy de Gotcha 2 com `lock_timeout=30s` ativo |
| 2026-05-08 ~22:01 | Pipeline confirmado healthy (candles 5m fresh) |
| 2026-05-08 ~19:35 | (NOTA: refere-se a ~9-10h depois — workers começam a ter falhas crescentes em ETH_USDT) |
| 2026-05-09 ~03:24 | Workers Cloud Run auto-restart (provavelmente OOM ou max-instances scale-to-zero), saem online novamente, mas pipeline já está degradado |
| 2026-05-09 ~09:48 BRT (12:48 UTC) | Operador percebe Centro Operacional CRITICAL: atraso 14h13m, símbolos=0, candles=0, backlog `execution=2446` + `structural=699` |
| 2026-05-09 12:56 UTC | Recovery manual: `pg_terminate_backend(685036)` (TX órfã de 4min32s segurando INSERT em `ohlcv`) + rolling restart dos 3 workers |
| 2026-05-09 13:04-13:19 UTC | Workers voltam a rodar mas `LockNotAvailableError` persiste em ETH_USDT (6x), GT_USDT, FLOKI_USDT, NEXO_USDT — confirmando que o problema NÃO é só TX órfã |
| 2026-05-09 ~13:30 UTC | Patch de remoção do `lock_timeout=30s` deployado |

### Diagnóstico definitivo

Log de `scalpyn-worker-structural` filtrado por `LockNotAvailableError`:

```
13:07:47 [FAILED symbol=FLOKI_USDT] LockNotAvailableError: canceling statement due to lock timeout
13:09:24 [FAILED symbol=GT_USDT]    LockNotAvailableError: canceling statement due to lock timeout
13:11:04 [FAILED symbol=ETH_USDT]   LockNotAvailableError
13:12:45 [FAILED symbol=ETH_USDT]   LockNotAvailableError
13:14:27 [FAILED symbol=ETH_USDT]   LockNotAvailableError
13:15:42 [PipelineScan] Fatal error: LockNotAvailableError
13:15:42 Task pipeline_scan.scan raised unexpected: DBAPIError(LockNotAvailableError)
13:16:07 [FAILED symbol=ETH_USDT]   LockNotAvailableError
13:16:37 [FAILED symbol=ETH_USDT]   LockNotAvailableError
13:17:49 [FAILED symbol=ETH_USDT]   LockNotAvailableError
13:18:41 [FAILED symbol=NEXO_USDT]  LockNotAvailableError
```

Padrão: ETH_USDT falha em ~6 ciclos de beat em 7min — não é loop dentro do mesmo worker, são re-runs do beat batendo na mesma contenção. ETH é o mais hot (mais contenção entre WS Gate.io ticker + collect_5m UPSERT + collect_all INSERT). 30s não é suficiente.

### Por que o code path defensivo não cobriu

`collect_market_data.py:297-336` JÁ tem detecção de outer poisoned (`PendingRollbackError` + `not db.is_active` → break). Mas `LockNotAvailableError` dentro de SAVEPOINT NÃO marca `db.is_active=False` — o SAVEPOINT rolla limpo, outer continua válida. Então o loop continua tentando os outros símbolos sem problema.

O verdadeiro estrago foi o `pipeline_scan.scan` (que NÃO usa SAVEPOINT por símbolo) explodindo com `Fatal error` e re-queueing — backlog `execution` cresce porque `evaluate_signals` mantém `acks_late=True` (preservação financeira de ordens de compra).

### Fix aplicado (2026-05-09)

Remover apenas `lock_timeout` do `server_settings`. Manter `idle_in_transaction_session_timeout=300000` — esse continua sendo defesa válida contra TX órfã de container morto (problema raiz original de 2026-05-08). Comentário inline em `database.py:252-263` documenta a regressão pra prevenir re-introdução cega.

### Recovery manual (mesmo padrão de antes)

```sql
-- Cloud Shell — kill TXs órfãs
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname='scalpyn'
  AND state IN ('idle in transaction','idle in transaction (aborted)')
  AND NOW() - state_change > INTERVAL '1 minute';
```

```bash
# Rolling restart dos 3 workers (usa --max-instances=2, NÃO 0 — Cloud Run rejeita 0)
for svc in scalpyn-worker-execution scalpyn-worker-structural scalpyn-worker-micro; do
  gcloud run services update "$svc" --region=us-central1 --max-instances=1
  sleep 10
  gcloud run services update "$svc" --region=us-central1 --max-instances=2
done
```

### Lições

1. **Nunca deployar timeouts agressivos sem medir p95 antes**. `lock_timeout=30s` foi escolhido por intuição ("30s é mais que tempo razoável de contenção"), não por dado. Deveria ter rodado `pg_locks` + `pg_stat_activity` snapshot por 24h pra ver p95 real de wait time em ETH_USDT antes do deploy.
2. **Defesa em profundidade tem limites**: o code path defensivo do `collect_market_data` cobre outer poisoned, mas não cobre tarefas paralelas (`pipeline_scan`) que falham pelo mesmo motivo subjacente.
3. **`acks_late=True` em tasks de execução é arma de dois gumes**: preserva ordens de compra (ótimo) mas também acumula backlog quando a falha é determinística (péssimo). Considerar dead-letter queue com max-retries pra tasks de execução em incidentes futuros.

---

## Apêndice B — Cascata de deadlocks `market_metadata` (2026-05-09 14:22-14:31 UTC, Task #251)

### Timeline

| Hora UTC | Evento |
|---|---|
| 2026-05-09 14:22 | Primeiro `deadlock detected` (40P01) em prod. Workers continuam UPSERTando. |
| 2026-05-09 14:22-14:31 | **140 deadlocks em 9min**. PID 693296 com **64 consecutivos** (mesmo backend acumulando vítimas). |
| 2026-05-09 14:25 | Snapshot reporta `ingestion_stale` + `no_decisions` + alerta `queue_backlog_500`. |
| 2026-05-09 14:31 | Cessa naturalmente (cycle de beat termina, próximo cycle pega o universo já parcialmente atualizado). |
| 2026-05-09 ~15:00 | Diagnóstico: NÃO é `LockNotAvailableError` (lock_timeout reverted). É deadlock determinístico por **ordem de aquisição cruzada**. |
| 2026-05-09 ~16:00 | Patch Task #251 (sorting determinístico em 8 callsites). |

### Causa raiz

8 callsites independentes UPSERTam em `market_metadata` dentro de UMA outer transaction (`run_db_task` → `session.begin()`):

- `collect_market_data.py`: linhas 92 (loop OHLCV), 410 (tickers 1h), 660 (loop OHLCV 5m), 915 (tickers 5m), 1009 (fallback per-symbol stale).
- `microstructure_scheduler_service.py:478` (gather de `_refresh_one_symbol`).
- `structural_scheduler_service.py:319` (idem).
- `scheduler_service.py:347` (idem).

SAVEPOINT por símbolo (`async with db.begin_nested()`) **NÃO libera row-locks** — só o COMMIT do outer libera (gotcha "Nested-savepoint rollback rule"). Quando 5 workers Cloud Run iteram `valid_symbols` ou `tickers` em ordens diferentes (por exemplo, dict insertion order varia entre processos por causa de hash randomization e ordem de seed do pool), Postgres detecta deadlock e mata uma das TXs com erro `40P01`. Worker re-roda na próxima beat tick → mesma ordem caótica → mesmo deadlock. Loop por 9min.

**Por que não é o mesmo bug do `lock_timeout=30s` revertido**: aquele era contenção legítima (ETH_USDT hot, 5 workers, 30s insuficiente). Este é deadlock matemático — qualquer `lock_timeout` desencadeia o mesmo resultado, só varia a vítima. A solução é eliminar a ordem cruzada, não esticar timeouts.

### Fix aplicado (Task #251)

Adicionar `sorted()` em 8 callsites:

```python
# collect_market_data.py
symbols = sorted(valid_symbols)  # 2 ocorrências (collect_all + collect_5m)
for ticker in sorted(tickers, key=lambda t: t.get("currency_pair", "")):  # 2 ticker loops
for sym in sorted(stale_syms):  # fallback

# 3 schedulers
*[_refresh_one_symbol(s, semaphore, ...) for s in sorted(symbols)]
```

Ordem alfabética é convenção compartilhada → todos os workers pegam locks na mesma ordem → deadlock por ordem cruzada vira matematicamente impossível.

### Validação pós-deploy

```sql
-- Cloud Shell, janela de 24h pós-deploy
SELECT date_trunc('hour', log_time) AS hora,
       count(*) FILTER (WHERE message ~* 'deadlock detected') AS deadlocks
FROM postgres_logs
WHERE log_time > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1 DESC;
```

Esperado: zero deadlocks em `market_metadata`. Se reaparecerem: investigar novo callsite que escapou do sorting (provavelmente um callsite novo adicionado depois da Task #251 que não seguiu a convenção).

### Follow-up adiado

**Bulk UPSERT dos 2 ticker loops** (~500 USDT pairs/cycle, atualmente 1 SAVEPOINT por ticker): substituir por 1 `INSERT ... VALUES (...), (...) ON CONFLICT` único reduz I/O e elimina contenção residual. Não feito agora porque perda em batch (1 ticker ruim aborta os 500) precisa de estratégia de retry per-row mais cuidadosa. Não bloqueante — sorting já elimina o deadlock determinístico.
