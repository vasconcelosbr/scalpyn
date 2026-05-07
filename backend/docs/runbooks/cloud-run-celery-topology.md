# Cloud Run Celery Topology — Runbook

**Owner:** Plataforma · **Última revisão:** 2026-05-07 (Task #239)

## TL;DR

Produção precisa de **5 serviços Cloud Run** em `us-central1`. Se faltar **qualquer um** deles, o pipeline fica silenciosamente parado: o `scalpyn` (API) continua respondendo HTTP normalmente, mas nenhum candle é coletado, nenhum indicador é computado e nenhuma decisão de execução é avaliada.

```
scalpyn                     ← API (FastAPI), WORKER_QUEUES="", RUN_BEAT=0
scalpyn-worker-micro        ← consome `microstructure` (collect_5m, compute_5m)
scalpyn-worker-structural   ← consome `structural` (collect_all, pipeline_scan, …)
scalpyn-worker-execution    ← consome `execution` (evaluate, execute_buy, anti_liq)
scalpyn-beat                ← único agendador (RUN_BEAT=1, sem worker)
```

Definição canônica em [`cloudbuild.yaml`](../../../cloudbuild.yaml) (Task #216).

## Comando de verificação

```bash
gcloud run services list \
  --region=us-central1 \
  --format="value(metadata.name)" \
  --filter="metadata.name=(scalpyn OR scalpyn-worker-micro OR scalpyn-worker-structural OR scalpyn-worker-execution OR scalpyn-beat)"
```

Saída esperada: **5 linhas**, uma por serviço acima. Qualquer outro número é incidente.

O mesmo check roda como último step do `cloudbuild.yaml` (`topology-check`); builds futuros que percam um serviço falham vermelho na hora.

## Sintomas de topologia incompleta

| Faltando | Sintoma observável |
|---|---|
| `scalpyn-beat` | `MAX(time)` em `ohlcv` (qualquer timeframe) congela; `decision_log` para de receber linhas; `/api/system/celery-diagnostics` mostra `last_collect_all_start` antigo. |
| `scalpyn-worker-micro` | `MAX(time)` em `ohlcv WHERE timeframe='5m'` congela mesmo com beat vivo; `microstructure` enche em `/api/system/celery-status`. |
| `scalpyn-worker-structural` | `pipeline_scan`/`compute_indicators`/`collect_all` ficam pendurados em `structural`; indicadores 1h param. |
| `scalpyn-worker-execution` | `trade_tracking` zera (`pending=0` por horas); `evaluate_signals` enfileirado mas não consumido. |
| `scalpyn` (API) | 404/5xx no front; usuário percebe imediatamente. |

## Recuperação

1. Confirmar quais serviços existem com o comando acima.
2. Re-rodar o pipeline completo:

   ```bash
   gcloud builds submit --config=cloudbuild.yaml \
     --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)
   ```

   O Cloud Build é idempotente: serviços já corretos só recebem nova revisão; faltantes são criados. O step final `topology-check` valida o resultado.

3. Validar que os 5 estão `Serving` no console e que `MAX(time) FROM ohlcv WHERE timeframe='5m' AND symbol='BTC_USDT'` avança em até 10 minutos.

## Não fazer

- **Não deletar** nenhum dos 5 manualmente. Se um serviço precisa pausar, escalar `--min-instances=0 --max-instances=0` em vez de deletar — assim o `topology-check` não quebra builds futuros.
- **Não mover** `RUN_BEAT=1` para outro serviço (`scalpyn-beat` é o único agendador por design — Task #216, evita double-fire).
- **Não fundir** workers em um só (`WORKER_QUEUES=microstructure,structural,execution` num único serviço viola o isolamento de latência da fila `execution`).

## Incidente 2026-05-07 — instância `scalpyn` patológica pós-deploy (Task #241)

**Sintoma:** após o deploy do commit `14592ac5` (Task #239, revisão `scalpyn-00434-459`, build `e3f71977-0626-49f0-b72c-cd320dd73835`), 100% dos endpoints (`/api/auth/login`, `/api/dashboard/overview`, `/api/custom-watchlists/`, `/api/watchlists/`, `/api/profiles/`, `/api/pools/`, `/ws/alerts`, `/api/system/celery-status`) começaram a retornar **504 com latência exatamente `300.000s`** (timeout default do Cloud Run). Todos os logs apontavam para o **mesmo `instanceId` `0007b734…`** — ou seja, uma única instância travada servindo tudo.

**Causa raiz: não confirmada** (sem `gcloud logging read` os logs de aplicação da revisão são opacos para o agente). Hipóteses, em ordem de plausibilidade:

1. **Instância única patológica.** Cloud Run só promove novas instâncias quando observa concurrency > target — uma instância que aceita conexões mas nunca responde mantém o tráfego de 100% dos usuários numa única réplica travada. Compatível com o `instanceId` único nos logs de request.
2. **Pool DB exausto.** Improvável neste serviço: `scalpyn` API roda com `SKIP_STRUCTURAL_SCHEDULER=1`, `SKIP_MICROSTRUCTURE_SCHEDULER=1`, `SKIP_PIPELINE_SCHEDULER=1` (ver `cloudbuild.yaml`), então os schedulers que dominariam o pool não rodam aqui.
3. **Cold-start storm de boot.** Os 5 serviços re-deployaram juntos; algum probe síncrono no lifespan (Gate WS leader election, ops snapshot, warmup DB) pode ter ficado pendurado. Redis Labs estava **saudável** no momento da investigação (`evictions=0`, conn≈54), o que enfraquece a sub-hipótese "Redis saturado". Mas não dá para descartar uma janela de saturação que já tinha drenado quando olhei.

Sem os logs de aplicação da revisão `00434-459`, qualquer atribuição definitiva é especulação. **Antes do próximo recovery, rodar o passo 3 abaixo primeiro.**

**Mitigação executada:** bumpei `FORCE_RESTART` no env do `scalpyn` de `2026-05-04T00:00:00Z` para `2026-05-07T17:30:00Z`. Isso força o Cloud Run a criar uma nova revisão (env diff detectado) e mata a instância patológica. Como Redis já está saudável (backlog drenado pelos workers que sobreviveram), o cold start da nova revisão é leve.

**Não foi feito** (exige `gcloud`, não disponível no agente de dev):
- `gcloud run services update-traffic scalpyn --to-revisions=scalpyn-00433-p7c=100` (rollback explícito).
- `gcloud logging read … severity>=ERROR` na revisão `00434-459` para confirmar onde a instância travou.

**Se o incidente repetir após este deploy**, executar manualmente os dois comandos acima e abrir follow-up para reduzir o trabalho de boot do API service (mover Gate WS leader election para depois do `yield` no lifespan, ou movê-la para um serviço dedicado).

### Update 17:36-17:44 UTC — diagnóstico mudou de natureza (Task #242)

Snapshot novo de `/api/system/celery-status` às 17:36 e logs Cloud SQL/Cloud Run anexados às 17:41-17:44 mostraram que o quadro **não é mais** "instância única travada com latência 300s":

- `microstructure.depth=0` (drenando), `structural.depth=563` `oldest_age=5s` (drenando), `execution.depth=11738` (cresceu de 11547 → confirma worker `execution` atolado, mas vivo).
- Latência caiu para ~2s com **HTTP 503** "malformed response or connection error" — app passou a crashar rápido em vez de pendurar.
- Cloud SQL: `ERROR: deadlock detected at character 34` (17:41), seguido às 17:44 de `FATAL: canceling authentication due to timeout` em duas conexões consecutivas.
- Stack trace no `scalpyn-00436-gp5`: `sqlalchemy/pool/base.py:_close_connection → asyncpg.terminate()` — pool queimando conexões e forçando reconexão em loop.

**Reinterpretação:** a revisão `00436-gp5` está em **connection storm** — pool reciclando conexões mais rápido que o Cloud SQL consegue autenticar (default `authentication_timeout=60s`), o que reabre o ciclo. O 504-storm das 17:15-17:20 era outra fase (instância travada); a partir de 17:30+ o sintoma virou storm de auth/deadlock. **`commit-sha` da `00436-gp5` é `da680b3`** (housekeeping de sessão, sem código relevante) — ou seja, o fix da Task #241 (`8351ba6`) ainda não tinha chegado a essa revisão quando os logs foram capturados.

**Próximo passo executado:** `suggest_deploy` do `8351ba6` para promover uma revisão limpa que rompa o ciclo da `00436-gp5`. Aguardando o usuário confirmar:

- **Revisão final que ficou de pé:** `_____` (preencher após deploy)
- **`/api/system/celery-status` pós-deploy + purge:** `execution.depth=____` (esperado: < 50)
- **Comando de purge usado:** `_____` (`celery -A app.tasks.celery_app purge -Q execution -f` ou `redis-cli -u $REDIS_URL DEL execution`)
- **Timestamp de recuperação:** `_____ UTC`

Se o connection storm persistir mesmo na revisão nova, **abrir Task #243** focada em (a) tirar `start_gate_ws_with_leader_election` do critical path do lifespan e (b) reduzir `pool_recycle` ou aumentar `authentication_timeout` no Cloud SQL para evitar realimentação do storm.
