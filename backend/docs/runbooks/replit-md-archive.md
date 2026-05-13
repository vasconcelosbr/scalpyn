# `replit.md` Gotchas — Arquivo Histórico

Gotchas estabilizadas, movidas do `replit.md` na limpeza de 2026-05-12 para
liberar espaço de contexto sem perder rastreabilidade. Cada item ainda é
**autoritativo** — re-ler antes de mexer no subsistema correspondente.

Itens vivos / em flux ficam no `replit.md`. Re-promover daqui pra lá se
voltar a queimar em produção.

---

## Celery sentinel queue (`__no_default__`) — Task #216 / #220

Declared em `task_queues` para que `kombu._create_task_sender` consiga resolver
`task_default_queue` (Celery ≥ 5.6 levanta `KeyError` em todo `send_task` /
beat tick caso contrário). Nenhum worker consome — qualquer task que escapar
de `TASK_ROUTES` empilha visivelmente lá, preservando o intent loud-failure
do invariante #4 do lint test (`backend/tests/test_celery_routing_invariants.py`).
**Não remover.**

## `compute_indicators_robust` window

`window_seconds=300s` é o padrão para order flow data. Inconsistências causam
flapping `VALID` ↔ `NO_DATA`.

## Nested-savepoint rollback rule — Task #222

Nunca chamar `await db.rollback()` dentro de um callback que usa
`async with db.begin_nested()` — o SAVEPOINT já é revertido pelo context
manager na exceção. O `db.rollback()` extra fecha a OUTER transaction
(aberta por `run_db_task` via `async with session.begin()`) e poisona toda
chamada DB subsequente com `PendingRollbackError` / `Can't operate on closed
transaction inside context manager`. Regressão Cloud Run maio/2026.

## Scheduler concurrency ceiling

`BACKGROUND_SCHEDULER_CONCURRENCY` default 3 (era 8). Pool = 5+5=10 por worker;
schedulers structural + micro podem sobrepor → max combinado 6 sessions,
deixando 4 para API handlers. Setar > 4 esgota o pool e dispara cascata
`QueuePool limit of size 5 reached`.

## `pool_starved` ≠ `ingestion_stale` — Task #232

`OperationalSnapshotService` amostra `COUNT(DISTINCT symbol) WHERE is_active=true`
no mesmo probe da janela de freshness OHLCV. Pool vazio → `ingestion_stale`
suprimido + alerta `severity="info" code="pool_starved"`. Paging intencionalmente
desativado. **Não "consertar"** re-habilitando o stale alert: zero símbolos
ativos = zero candles, e o operador já sabe.

## No inline Celery/Redis probes em HTTP handlers — Task #225

`/api/dashboard/*` lê APENAS do cache do `OperationalSnapshotService`.
Adicionar `celery_app.control.inspect()` ou `redis.info()` direto no
handler trava a resposta user-facing na dependência mais lenta (5+ s
quando broker está mudo). Sempre estender o snapshot service.

## Cloud Run topology = 5 serviços — Task #239

Prod precisa: `scalpyn` (API) + `scalpyn-worker-{micro,structural,execution}`
+ `scalpyn-beat`. Faltar qualquer um deixa o pipeline silenciosamente parado
enquanto a API responde HTTP normal (incidente 2026-05-05: só `scalpyn`,
ohlcv 5m congelou em 21:35Z, trade_tracking zerou). Step `topology-check`
no `cloudbuild.yaml` falha vermelho se algum estiver ausente — **não remover**.
Pausar = `min/max=0` (não deletar). Detalhes: `cloud-run-celery-topology.md`.

## Cloud Run recovery script — Task #244

`scripts/promote-cloud-run-topology.sh`: quando o Cloud Build trigger
silenciosamente perder workers/beat (visto 2026-05-07: 6 builds verdes
consecutivos com só `scalpyn` em prod por causa do flag inválido
`--timeout-startup=540` que NÃO existe em `gcloud run deploy`), rodar no
Cloud Shell. Faz `gcloud run services describe scalpyn --format=export`
e clona o spec inteiro (incluindo Secret Manager bindings) para os 4
workers/beat via `gcloud run services replace`. **NÃO** usar
`gcloud run deploy --update-env-vars` para criar workers do zero — só
carrega envs explícitas e o container morre no `start.sh:39-45` antes de
abrir porta 8080. Pré-requisito: `gcloud services enable
cloudresourcemanager.googleapis.com`.

## Celery `--hostname` obrigatório no Cloud Run — Task #244

`HOSTNAME=localhost` em todo container Cloud Run; sem `--hostname`, todos
os workers se anunciam ao broker como `celery@localhost` e
`celery_app.control.inspect()` recebe respostas colidindo no mesmo nodename
→ dedup silencioso → `Workers: 0` mesmo com workers drenando filas.
`start.sh` agora gera `CELERY_NODENAME="${K_SERVICE:-celery}-<uuid8>"`
e passa `--hostname="celery@${CELERY_NODENAME}"`. **Não remover**.

## `procps` obrigatório no backend Dockerfile

Base `python:3.12-slim` não inclui `ps`. Sem `procps`, o watchdog em
`start.sh:281` (`is_process_alive` → `ps -o stat= -p`) sempre retorna
sucesso (string vazia + `case` sem match = exit 0) → workers/beat zumbis
NÃO são detectados, container responde HTTP indefinidamente sem consumir
do broker. `procps` está em `apt-get install` no `backend/Dockerfile`.
**Não remover**.

---

# Bloco 2 — Movido de `replit.md` em 2026-05-13

Critério: gotchas estáveis há ≥4 dias com lint test ou runbook dedicado.

## `acks_late=False` em tasks idempotentes beat-driven — Task #245

O global `task_acks_late=True` + `task_reject_on_worker_lost=True`
(`celery_app.py:246-247`) faz o broker re-entregar QUALQUER task que estoure
`time_limit` (SIGKILL) — fora do contador de `max_retries=3` (que só conta
`task.retry()` explícito). Em maio/2026 isso gerou loop infinito: UPSERT em
`market_metadata` contendido estourava `command_timeout=60s` → outer tx
poisoned → `_inner` raise → reject_on_worker_lost requeue → repete forever.
Backlog `structural=473`/`execution=1206`. Fix em `celery_app.py:179-209`:
`_NO_REQUEUE_ON_WORKER_LOSS = {"acks_late": False}` aplicado a `collect_5m`,
`collect_all`, `compute_5m`, `compute`, `compute_30m`, `score`,
`pipeline_scan.scan`, `health_checks.check_structural_coverage` (todas
idempotentes — beat re-roda em ≤60s/5min/30min). Tasks de execução
(`evaluate_signals`, `execute_buy_cycle`) MANTÊM o global `acks_late=True` —
perder uma decisão de compra sem retry é inaceitável.
**Não desligar acks_late nessas duas.**

## Dois budgets distintos no probe Celery — Task #246

`OperationalSnapshotService._refresh_celery` usa **dois** budgets independentes
que NÃO devem ser unificados. `CELERY_INSPECT_TIMEOUT_S` (default 2s) é passado
a cada `inspect(timeout=...)`; `CELERY_INSPECT_BUDGET_S` (default 8s) envolve
o probe inteiro via `asyncio.wait_for`. Em maio/2026 os dois eram a MESMA
constante (2s) — com Redis Labs externo em us-central1 + workers ocupados, as
4 chamadas sequenciais passavam de 2s no agregado → snapshot reportava
`Workers: 0` mesmo com 5 workers vivos. Fix complementar: snapshot path chama
APENAS `active()` + `registered()` (2 broadcasts em vez de 4); `reserved`/
`scheduled` continuam no shape mas sempre 0 (backlog real coberto pelo `LLEN`
em `_refresh_redis`). Não restaurar essas duas chamadas ao snapshot.

## `command_timeout=180s` para sessions Celery — Task #245

API engine usa 60s (HTTP precisa falhar rápido); engine Celery
(`database.py:240`) usa 180s porque `collect_market_data.collect_all/_5m`
envolve o loop do universo inteiro em UMA outer transaction. SAVEPOINTs
internos NÃO liberam row-locks — só o COMMIT do outer libera. Com 5 workers
Cloud Run UPSERTando concorrentemente em `market_metadata`, contenção
transiente regularmente passava de 60s. 180s dá folga sem ultrapassar
`soft_time_limit=540s` do structural. Override via env
`CELERY_DB_COMMAND_TIMEOUT`.

## Pipeline recovery 2026-05-08/09 (4 incidentes correlacionados)

`_MICRO_GUARDS` time_limit 180→480s + `idle_in_transaction_session_timeout=300s`
no Celery engine + `scalpyn` API ingress
`internal-and-cloud-load-balancing`→`all` + **`lock_timeout=30s` REVERTIDO em
2026-05-09** (regressão em <12h: hot symbols ETH_USDT/GT_USDT/FLOKI_USDT/
NEXO_USDT falhando com `LockNotAvailableError` cronicamente, backlog
execution=2446). **NÃO re-adicionar `lock_timeout` < 120s sem medir p95
lock-wait por símbolo em janela de 24h**. `command_timeout=180s` é o teto
correto. Recovery manual (TX órfã):
`SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='scalpyn' AND state LIKE 'idle in transaction%' AND NOW()-state_change > INTERVAL '1 minute';`
(Cloud Shell, com `--database=scalpyn` explícito). Rolling restart dos workers
usa `--max-instances=1` depois `=2` (Cloud Run rejeita `=0`). Detalhes em
`backend/docs/runbooks/2026-05-08-pipeline-recovery.md`.

## Cloud Build trigger / YAML escape / secrets / GitHub sync (2026-05-08)

5 lições do recovery em `backend/docs/runbooks/cloudbuild-trigger-history.md`.
Resumo: SA do trigger é `330575088921-compute@`; shell vars em scripts inline
precisam de `$$VAR`; `--update-secrets` é incremental (use `--remove-secrets`);
`gcloud run services describe` NÃO aceita `--filter`; origin do Replit é
gitsafe-backup.
