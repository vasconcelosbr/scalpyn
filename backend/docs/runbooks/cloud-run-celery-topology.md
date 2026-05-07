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

### Caminho A — recovery rápido sem Cloud Build (recomendado quando o trigger está quebrado)

Quando os builds do Cloud Build estão verdes mas a topologia continua incompleta (sintoma observado 2026-05-07: 6 builds verdes, `gcloud run services list` retorna só `scalpyn`), use o script de recovery commitado no repo. Ele lê a imagem que `scalpyn` está rodando e cria/reconcilia os 4 workers/beat com os flags exatos do `cloudbuild.yaml`. Idempotente.

```bash
# No Cloud Shell (não precisa clonar o repo — copie o conteúdo do script):
bash scripts/promote-cloud-run-topology.sh
# OU, se já tiver o repo clonado:
cd ~/scalpyn && bash scripts/promote-cloud-run-topology.sh
```

Esperado ao final: tabela com 5 linhas, todas `status=True`. Tempo total ~5 minutos.

### Caminho B — re-rodar o Cloud Build inteiro

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

**Próximo passo executado:** `suggest_deploy` do `8351ba6` para promover uma revisão limpa que rompa o ciclo da `00436-gp5`.

**Validação manual (Cloud Shell, 2026-05-07 ~17:50 UTC):**

- `gcloud run services list --region=us-central1 --filter="metadata.name~scalpyn"` retornou **um único serviço**: `scalpyn` (LAST DEPLOYED AT 2026-05-07T17:43:50Z, conta `330575088921-compute@developer.gserviceaccount.com`). Os 4 serviços previstos pela Task #239 (`scalpyn-worker-{micro,structural,execution}` + `scalpyn-beat`) **não existem em prod** — a tentativa do operador de rodar `gcloud run services proxy scalpyn-worker-execution` falhou com `Service [scalpyn-worker-execution] could not be found in project [clickrate-477217] region [us-central1]`. Conclusão: o build da Task #239 que criou o `topology-check` aparentemente nunca promoveu os 4 workers/beat com sucesso, ou foi rolled back, e ninguém percebeu porque o `scalpyn` API continuou respondendo HTTP normalmente. **Sintoma idêntico ao incidente original do Task #239 (2026-05-05).**
- **Quem estava drenando `microstructure`/`structural` no snapshot 17:36 — premissa inicial INCORRETA, refutada pela Task #243.** A primeira hipótese foi: "o `Celery Worker` do workflow Replit aponta para o `REDIS_URL` de prod e drena filas por acidente". Verificação no início da Task #243 (2026-05-07): `backend/app/config.py:8` define `REDIS_URL: str = "redis://localhost:6379/0"` como default; no Replit a env `REDIS_URL` resolve para `redis://localhost:6379/0`; o workflow `Redis` local responde `PONG` na porta 6379. Portanto o Celery Worker do Replit conecta no Redis **local**, não no broker de prod, e **não** estava drenando filas de prod. A origem real do `worker_count=1` que `/api/system/celery-status` reportava permanece indefinida — candidatos prováveis: (a) instância órfã de revisão Cloud Run antiga (pré-Task #216, com worker embedded) ainda não reaped, (b) revisão de algum dos 4 serviços `scalpyn-worker-*` que existiu em algum momento e foi rolled back mas mantém réplica viva. Confirmar exige `gcloud run revisions list --service=… --region=us-central1` + `gcloud run services describe …` — fica como passo 1 da fase manual da Task #243. **Implicação prática:** o Step 4 original da Task #243 ("pausar Celery Worker do Replit") é **no-op** — pode ser pulado.
- **Purge da fila `execution`:** `redis-cli -u 'redis://default:***@redis-18005.c279.us-central1-1.gce.cloud.redislabs.com:18005/0' DEL execution` — antes: `LLEN=11838`; depois: `LLEN=0`. Comando `celery -A app.tasks.celery_app purge` falhou no Cloud Shell (não tem o pacote Python instalado), por isso o caminho via `redis-cli` direto.
- **Revisão final que ficou de pé:** `scalpyn-00436-gp5` (commit `da680b3` ou superior — a revisão promovida pelo `suggest_deploy` do `8351ba6` ainda não tinha aparecido como "Last deployed" no momento da consulta; preencher na próxima passagem).
- **Timestamp de recuperação:** ~17:50 UTC após o purge da fila.

**Estado pós-recuperação parcial:** o sintoma agudo (backlog `execution` crescendo + connection storm derivado dele) está mitigado. **Mas a causa raiz estrutural permanece**: prod tem topologia mono-serviço, não a topologia 5-serviços da Task #239. Beat (se estiver vivo em alguma revisão órfã) schedulando tasks que ninguém em Cloud Run consome.

**Task #243 — escopo final reconciliado** (fase agente já executada; itens abaixo restam para fase manual no Cloud Shell):

1. Investigar por que o `cloudbuild.yaml` da Task #239 não criou (ou rolou de volta) os 4 serviços `scalpyn-worker-{micro,structural,execution}` + `scalpyn-beat` em prod. O step `topology-check` do `cloudbuild.yaml` deveria ter falhado vermelho — verificar histórico com `gcloud builds list --limit=20` filtrando builds posteriores ao merge da Task #239 e abrir os logs dos vermelhos. **A partir desta task, o `cloudbuild.yaml` tem um step `preflight-diagnostic` no início** que loga identidade do Cloud Build, projeto, região, serviços Cloud Run existentes e existência da `scalpyn-service-account` — então no próximo build vermelho a causa raiz (IAM/quota/SA missing) já estará no log sem precisar re-rodar. Todos os 5 `gcloud run deploy` agora também rodam com `--quiet` para eliminar a hipótese de prompt interativo travado.
2. Aplicar a correção identificada (provavelmente IAM da SA do Cloud Build sobre os novos serviços, quota Cloud Run, ou argumento inválido em `cloudbuild.yaml`).
3. Disparar `gcloud builds submit --config=cloudbuild.yaml` e validar que termina verde. Confirmar via `gcloud run services list --region=us-central1 --filter="metadata.name~scalpyn"` que os 5 serviços ficam `Serving`.
4. Identificar a origem real do `worker_count=1` órfão observado em `/api/system/celery-status` antes da promoção (`gcloud run revisions list --service=scalpyn --region=us-central1` + idem para os 4 nomes esperados — pode haver revisão pré-Task #216 com worker embedded ainda viva).
5. Monitorar 30 minutos pós-deploy: `execution.depth ≤ 50`, `oldest_age_s < 60` em todas as filas, sem novos alertas `pool_starved`/`ingestion_stale`.
6. Atualizar este runbook com (a) erro real do build vermelho, (b) correção aplicada, (c) timestamp de recuperação, (d) snapshot final do `gcloud run services list`.

**Item descartado pela fase agente:** "garantir que o `Celery Worker` do Replit deixa de apontar para `REDIS_URL` de prod" — refutado acima (config dev é `redis://localhost:6379/0`, Replit Redis local responde PONG; Replit nunca tocou o broker prod). Não é pré-requisito da promoção.

**Hardening pós-#243** (não fazer dentro da #243): alerta no Centro Operacional quando algum dos 5 serviços some — Task #240 já existe para isso, confirmar status. Se o connection storm voltar mesmo após topologia correta, abrir Task #244 focada em (a) tirar `start_gate_ws_with_leader_election` do critical path do lifespan e (b) reduzir `pool_recycle` ou aumentar `authentication_timeout` no Cloud SQL.
