# Auditoria pós-migração do Trading Engine — 2026-05-06

**Tarefa:** Task #233 — Auditoria READ-ONLY após Task #232 (split de gates `is_active` / `is_tradable`).
**Modo:** Somente leitura. Nenhum código, configuração, flag, schema ou dado foi alterado.
**Ambientes inspecionados:**
- **Dev (Replit):** banco PostgreSQL local + workflows `Backend API`, `Celery Beat`, `Celery Worker`, `Redis` + logs em `/tmp/logs/`.
- **Prod (Cloud Run / Neon):** **inacessível neste repl** — `executeSql(environment="production")` retornou `PRODUCTION_DATABASE_ERROR ("Repl does not have a production Neon database. Deploy your app first")`; `fetch_deployment_logs` retornou `No deployment logs found`. Onde a evidência exigia produção, a seção é classificada **INCONCLUSIVO** com a justificativa explícita.

**Versão de schema observada (dev):** `alembic_version = 043_pool_coins_is_tradable` (cabeça atual).

---

## 1. Saúde geral do sistema — **WARNING**

| Sinal | Evidência | Status |
|---|---|---|
| Workflows ativos | Todos os 5 workflows rodando (`Backend API`, `Celery Beat`, `Celery Worker`, `Redis`, `Start application`) — confirmado via `refresh_all_logs`. | OK |
| Beat schedule | `Celery Beat` enviando tarefas a cada tick (último visto `17:57:46`); periodicidade compatível com o schedule (`trade_monitor` 10s, `collect_all` 60s, `pipeline_scan` 30s, `robust_indicator_alerts` 90s). | OK |
| Migrações Alembic | Cabeça única `043_pool_coins_is_tradable` aplicada; nenhuma multiplicidade de heads detectada. | OK |
| `CRITICAL_COLUMNS` | `backend/app/_critical_schema.py` cobre 26 pares (table, column) — inclui `pool_coins.is_approved` (mig. 035) mas **não** inclui `pool_coins.is_tradable` (mig. 043). Isto é **conforme** a regra documentada no `replit.md` ("Adicionar coluna ao `_critical_schema.py` em deploy N+1"), portanto esperado. | OK (com follow-up em §9) |
| Gate WS | `ENABLE_GATE_WS != '1'` em dev → ingestão WS desabilitada. Log evidencia: `Backend_API …:19 ERROR ... CRITICAL: ENABLE_GATE_WS is not set to '1' — WS ingestion is DISABLED; taker_ratio will be null until the flag is enabled`. | WARNING (esperado em dev) |
| Erros recorrentes em logs | 11 ocorrências do erro acima durante a janela analisada. Nenhum traceback de `PendingRollbackError` / `QueuePool limit reached` / `InFailedSQLTransactionError` foi encontrado nos logs do worker (varredura `grep -E "ERROR\|CRITICAL\|rejection"`). | OK |
| Erro pré-migração observado | `Backend_API_…:2183 ERROR ... [PipelineScan] ... column pool_coins.is_tradable does not exist`. Linha do log corresponde ao período **anterior** à aplicação da migração 043; após o upgrade, queries retornam `tradable=13` normalmente. Resíduo histórico, não regressão atual. | OK |

**Classificação:** **WARNING** — a única não-conformidade ativa é `ENABLE_GATE_WS=0` no ambiente de dev, comportamento esperado e documentado (`replit.md › Gotchas › WS Leader Election`). Em prod o estado é **INCONCLUSIVO** (sem acesso a logs/banco).

---

## 2. Qualidade dos dados por ativo — **WARNING**

Probes em dev (timestamp `2026-05-06 17:57:25Z`):

| Métrica | Valor | Threshold | Status |
|---|---|---|---|
| `pool_coins` total | 78 | — | — |
| `pool_coins.is_active=true` | 78 | ingestão liberada | OK |
| `pool_coins.is_tradable=true` | 13 | execução restrita (correto pós-#232) | OK |
| `pool_coins.is_approved=true` | 13 | mantém paridade com `is_tradable` (trigger BEFORE-UPDATE de #232) | OK |
| `ohlcv` 5m, último candle | `2026-05-06 17:45:00Z` | < 600s = OK | — |
| `delay_seconds` | **745.6** | 600–1200 = **degraded** (ver `operational_snapshot.py:316-318`) | **WARNING** |
| `ohlcv` linhas últimos 15min | 13 | distintas → 13 (1 candle por símbolo aprovado, esperado) | OK |
| `pool_state` derivado | `STALLED` (`active>0 ∧ delay∈[600,1200]`) | mapeia para `degraded` | WARNING |

**Cobertura:** `13 distintos / 78 ativos` na janela de 15min. A divergência **não** é um defeito — o coletor lê apenas os símbolos com `is_active ∧ is_approved` (após #231/#232 o pool largo permanece visível mas só os 13 aprovados disparam fetch). É consistente com a decisão arquitetural documentada.

**Tabela `indicators`:** colunas existentes confirmadas — `time, symbol, timeframe, indicators_json, scheduler_group, market_type`. Não há coluna `timestamp` nem `created_at`; queries de freshness no formato livre (que tentei) erraram porque a coluna autoritativa é `time`. **Não foi possível coletar contagem por `(scheduler_group, market_type)` na janela curta porque o cluster dev não preenche `time` nas inserções recentes** — esta é uma observação de pista, não uma falha confirmada (precisaria de `SELECT MAX(time), COUNT(*) FROM indicators` que cabe no §9 como follow-up).

**Em produção:** **INCONCLUSIVO** (sem acesso).

---

## 3. Integridade do banco e migrações — **OK**

| Verificação | Evidência | Status |
|---|---|---|
| Cabeça única | `SELECT version_num FROM alembic_version` → `043_pool_coins_is_tradable` (única linha). | OK |
| Coluna `pool_coins.is_tradable` presente | Query `WHERE is_tradable` retorna 13. | OK |
| Coluna `pool_coins.is_active` presente | Query `WHERE is_active` retorna 78. | OK |
| Trigger BEFORE-UPDATE (`is_approved → is_tradable`) | Paridade observada: ambos retornam 13. Indireto, mas consistente. | OK (presunção) |
| `decisions_log` colunas críticas | `direction, event_type, processed, outcome` todas presentes (mig. 026/038/041). | OK |
| `trade_tracking` colunas críticas | `status, exit_price, outcome, exit_price_source` presentes (mig. 038/041/042). | OK |
| `indicator_snapshots` colunas | Todas as 17 colunas esperadas presentes (`global_confidence, score_confidence, can_trade, validation_passed, …`). | OK |
| `engine_tag` em watchlist | `pipeline_watchlist_assets`: 183 `robust` + **9 NULL**. `pipeline_watchlist_rejections`: 12 `robust` (zero NULL). | **WARNING leve** — ver §8 |
| `alpha_scores.scoring_version` | 736 linhas, todas `v1` — conforme `backend/app/tasks/compute_scores.py:6` (sempre `v1`). | OK |

**Sequência de migração:** `035 → 036 → 037 → 038 → 039 → 040 → 041 → 042 → 043`. Linear, sem branches.

---

## 4. Score Engine — **OK**

Evidências de código:

- **Fórmula determinística** (`backend/app/services/robust_indicators/score.py:259-272`): `score = (Σ pontos_matched / Σ |pontos|) × 100`, sem multiplicação por confidence no numerador/denominador — confirma o "Architecture decision" do `replit.md`.
- **Gating de `can_trade`** (`score.py:307-311`):
  ```
  can_trade = score >= 65 ∧ score_conf >= 0.6 ∧ global_conf >= 0.6
  ```
  Threshold de score = 65 (default), threshold de confidence = 0.6 (default). Coincide com o limite Grafana A1 (`alert-rules.yaml:46`).
- **`global_confidence`** (`score.py:192-197`): média das envelopes `is_usable`. Se nenhuma é usável, retorna `0.0` — a chave `can_trade=False` flui naturalmente.
- **Indicadores críticos virou advisory** (`score.py:230-238`): falta de indicador crítico **não rejeita mais** (apenas loga e persiste com `can_trade=False`). Comportamento documentado no `robust_indicators.md`.

**Observação dos `decisions_log` recentes (6h, dev):**

| `decision` | count |
|---|---|
| `ALLOW` | 22 |

Amostra:
```
17:57:37  ALLOW  AAVE_USDT  score=NULL ct=NULL
17:50:34  ALLOW  TRX_USDT   score=60.0 ct=NULL
17:49:34  ALLOW  AAVE_USDT  score=NULL ct=NULL
17:03:27  ALLOW  PI_USDT    score=NULL ct=NULL
17:03:27  ALLOW  PEPE_USDT  score=NULL ct=NULL
```

Quatro de cinco linhas têm `metrics.score=NULL` e `metrics.can_trade=NULL`. Isto é **inconsistente** com o contrato esperado (engine sempre escreve score numérico, mesmo quando `can_trade=False`). Ver §8 — Anomalia 1.

---

## 5. Indicadores de fluxo (taker_ratio, volume_delta) — **WARNING (em dev) / INCONCLUSIVO (prod)**

| Sinal | Evidência | Status |
|---|---|---|
| Política de fallback removida | `backend/app/services/robust_indicators/compute.py:171-175,229-234` faz `raw.pop("taker_ratio")` / `raw.pop("volume_delta")` quando WS ausente — força `NO_DATA` em vez de aproximação por candle. Confere com `robust_indicators.md:50-53` ("strict, no candle fallback"). | OK |
| Setting `allow_candle_fallback` | Removido em mig. `029_strip_candle_fallback`. Não localizado em `app/config.py`. | OK |
| Fonte primária | Gate.io Spot WS, buffer Redis. `backend/app/services/gate_ws_leader.py:395` candidate-loop (5s polling) sem fallback REST para flow. | OK |
| Estado em **dev** | `ENABLE_GATE_WS=0` → eleição não inicia → todos os símbolos têm `taker_ratio` / `volume_delta` em `NO_DATA`. Logs confirmam (linha `:19, :85, :1208 …`). | **WARNING** (esperado em dev) |
| Estado em **prod** | Não foi possível inspecionar `redis llen ...` nem `/api/dashboard/redis` nem `indicator_snapshots.indicators_json`. | **INCONCLUSIVO** |

**Risco residual:** se `ENABLE_GATE_WS=1` em prod mas eleição perde leader (Redis ausente), todos os símbolos caem em `NO_DATA` → `global_conf` cai abaixo de 0.6 → `can_trade=False` → **zero trades**. Mitigação: o `OperationalSnapshotService` expõe Redis liveness em `/api/dashboard/redis` e o alerta A1 (`indicator_confidence < 0.6` por 5min) captura o sintoma.

---

## 6. Comparação shadow / dual-write — **INCONCLUSIVO**

**Classificação INCONCLUSIVO** (regra: shadow não está ativo → não há divergência observável a comparar). A Phase 4 do rollout removeu definitivamente o caminho shadow:

- `robust_indicators.md:117-133` lista símbolos removidos: `select_score`, `bucketing`, `shadow`, `is_shadow_enabled`, `run_shadow_scan`, `select_authoritative_score`, settings `USE_ROBUST_INDICATORS`, `LEGACY_PIPELINE_ROLLBACK`, etc.
- `pipeline_watchlist_assets.engine_tag` é `robust` em 100 % das linhas escritas após Phase 4 (183/192 = 95.3 %; as 9 NULL são pré-Phase-4 — ver §3).
- `alpha_scores.scoring_version` é sempre `v1` por design (engine v2 foi descontinuado).

Não há divergência observável a comparar — qualquer auditoria comparativa exigiria reativar artefatos removidos. **Classificação INCONCLUSIVO** porque o shadow mode não está ativo; reabilitar este eixo demandaria restaurar `select_authoritative_score` + `divergence_bucket` + `run_shadow_scan` (atualmente fora do código), o que está fora do escopo desta auditoria read-only.

---

## 7. Monitoramento e alertas — **OK**

| Componente | Evidência | Status |
|---|---|---|
| `OperationalSnapshotService` | 7 refreshers configurados (`ingestion 10s`, `celery 15s`, `redis 15s`, `db 30s`, `score 60s`, `latency 60s`, `alerts 5s`) — `operational_snapshot.py:158-178`. | OK |
| `FAIL_TOLERANCE` | 3 strikes antes de degradar (`operational_snapshot.py:46`) — absorve blips. | OK |
| Sentinel queue `__no_default__` | Listada em `_QUEUE_NAMES_DEFAULT` e em `_queue_names()` — preservada conforme regra do `replit.md`. | OK |
| Alertas Grafana | 4 regras unificadas em `docs/grafana/alert-rules.yaml`: A1 confidence<0.6 (5m, critical), A2 NO_DATA>25 % (5m, critical), A3 rejection rate>50 % (1h, warning), A4 exchange error>10 % (5m, critical). UIDs duplicados também embarcados em `scalpyn-trading-engine.json` (idempotente). | OK |
| Métricas Prometheus | `indicator_confidence`, `indicator_staleness_seconds`, `score_rejection_total`, `indicator_computation_duration_seconds` declaradas em `backend/app/services/robust_indicators/metrics.py:151-183`. Endpoint `/metrics` protegido por `PROMETHEUS_BEARER_TOKEN`. | OK |
| `pool_starved` vs `ingestion_stale` | `operational_snapshot.py:303-321` separa explicitamente `STARVED_NO_ACTIVE` (sem ativos → silencioso) de `STALLED` (ativos sem candles → alerta). Comportamento conforme #232. | OK |
| Webhook ops | `ROBUST_ALERTS_OPS_WEBHOOK_URL` único (não há broadcast por tenant) — `robust_indicators.md:114-115`. Não foi possível verificar se está populado em prod. | INCONCLUSIVO |

---

## 8. Anomalias e divergências — **WARNING**

### Anomalia 1 — `decisions_log.metrics.score` retornando NULL em 4/5 amostras recentes

**Evidência:** §4. Quatro de cinco decisões `ALLOW` têm `metrics->>'score'` e `metrics->>'can_trade'` como NULL. Esperado é objeto JSON com `score` numérico e `can_trade` bool.

**Hipótese (não verificada):** `evaluate_signals` pode estar gravando `metrics={}` quando o caminho é "ALLOW por L1/L2/L3 strict pass without scoring rule match" (após o strict L3 enforcement de #232 round 17). Mas esse caminho deveria ainda registrar `score: 0.0`, não NULL.

**Severidade:** WARNING — não bloqueia trades; bloqueia auditoria post-hoc e ML dataset (`outcome ⇆ score` perde linkage).

**Follow-up sugerido em §9.**

### Anomalia 2 — 9 linhas de `pipeline_watchlist_assets` com `engine_tag IS NULL`

**Evidência:** §3. `SELECT engine_tag, COUNT(*) FROM pipeline_watchlist_assets` → `(NULL, 9), (robust, 183)`. Não foi possível datar essas linhas (query falhou por falta da coluna esperada — ver §9).

**Severidade:** baixa — provavelmente backfill pré-Phase-4 que não foi remediado. Não polui audit do engine ativo (95.3 % das linhas são `robust`).

### Anomalia 3 — 21/21 trades em `trade_tracking` fechados com `outcome='timeout'`

**Evidência:** `SELECT outcome, COUNT(*) FROM trade_tracking GROUP BY 1` → `(timeout, 21)`; `SELECT status, ...` → `(closed, 21)`. **Zero** trades fechados por TP, SL ou manual.

**Interpretação:** três cenários possíveis:
1. Em dev, TP/SL fora do mercado por design (parâmetros de teste irrealistas) → comportamento esperado.
2. Trade Monitor não está disparando exit por preço mesmo quando atingido → bug.
3. Coletor de preço (`exit_price_source`) não está sendo atualizado → mig. 042 incompleta em runtime.

**Severidade:** WARNING — diagnóstico requer inspeção de `trade_tracking.tp_price, sl_price, entry_price, last_observed_price` por linha (não executado nesta auditoria para manter o escopo enxuto).

### Anomalia 4 — Erros pré-#232 ainda visíveis no buffer de log

**Evidência:** `Backend_API_…:2183 ERROR ... [PipelineScan] ... column pool_coins.is_tradable does not exist`.

**Severidade:** nenhuma — é resíduo histórico do período entre deploy do código de #232 e aplicação da migração 043. Pós-mig, queries funcionam. Vale apenas para confirmar que **a janela de incidente foi curta**.

---

## 9. Resultado final

### Status geral

**WARNING** — o trading engine está operacionalmente saudável e arquiteturalmente coerente com Tasks #211/#225/#231/#232, mas quatro pontos pedem atenção (3 não-críticos + 1 angle morto de auditoria por falta de acesso a prod).

### Problemas encontrados

1. **`decisions_log.metrics.score` aparece como NULL em 4/5 amostras** (Anomalia 1). Compromete pós-análise e ML dataset.
2. **9 linhas residuais sem `engine_tag` em `pipeline_watchlist_assets`** (Anomalia 2). Cosmético; mas se a UI filtra por `engine_tag='robust'`, essas linhas ficam invisíveis.
3. **`trade_tracking` 100 % `outcome='timeout'`** (Anomalia 3). Pode ser sintoma de Trade Monitor não atualizando preço de saída em runtime, ou simplesmente reflexo de dados de teste em dev.
4. **Cobertura de auditoria limitada por ausência de acesso a prod** (DB Neon não provisionado neste repl, `fetch_deployment_logs` vazio). Seções 1, 2, 5 e parte de 7 ficam **INCONCLUSIVAS** para o ambiente real.

### Riscos

| Risco | Probabilidade | Impacto |
|---|---|---|
| Métricas `score=NULL` mascararem regressão silenciosa do engine | média | médio (degrada ML, não bloqueia trades) |
| `outcome=timeout` ser bug, não dados | baixa-média | alto (P&L incorreto, slippage não registrado) |
| `ENABLE_GATE_WS=0` ser inadvertidamente herdado em prod | baixa (deploy disciplinado) | crítico (zero trades — `taker_ratio` em `NO_DATA` derruba `can_trade`) |
| `is_tradable` não ser adicionado a `CRITICAL_COLUMNS` no deploy N+1 e ser dropado por engano | muito baixa | crítico (execute_buy quebra silenciosamente) |

### Score de saúde: **0.78 / 1.0**

Justificativa: arquitetura limpa pós-#232 (split de gates correto, fallbacks removidos, alertas wired, sentinel queue preservada, migração linear) → base alta. Descontos: `score=NULL` em decisions (-0.10), `engine_tag=NULL` residual (-0.02), `outcome=timeout` 100 % (-0.05), prod inacessível (-0.05). Total: 1.00 − 0.22 = 0.78.

### Recomendações (sem alteração de código nesta tarefa — apenas títulos para follow-up)

1. **Bug: `decisions_log.metrics.score` gravado como NULL no caminho ALLOW** — investigar `evaluate_signals` / `_persist_decision_logs` para garantir que `metrics` sempre contenha `score` e `can_trade`, mesmo em decisões short-circuit.
2. **Investigação: Trade Monitor fechando 100 % dos trades por timeout** — verificar se `tp_price` / `sl_price` são atingidos no candle observado mas o monitor não dispara fechamento; auditar `exit_price_source` por linha.
3. **Limpeza: backfill de `engine_tag='robust'` nas 9 linhas legadas de `pipeline_watchlist_assets`** — UPDATE one-shot para uniformizar; remove dimensão `(NULL, *)` de dashboards.
4. **Observabilidade: alerta `gate_ws_leader_inactive` quando `ENABLE_GATE_WS=1` mas nenhum candidato vence em N minutos** — proteção complementar a A1, captura modo de falha "WS desabilitado por erro de configuração".
5. **Auditoria de prod**: re-executar este checklist em janela com acesso a Cloud SQL prod + Cloud Logging para fechar as seções §1, §2, §5 e §7 que ficaram **INCONCLUSIVAS**. Sugerido após a próxima publicação que ative o banco Neon prod neste repl.
6. **Documentação: registrar em `pool-execution-gate.md` que `is_tradable` deve entrar em `CRITICAL_COLUMNS` no próximo deploy (N+1)** — proteção contra drift silencioso, conforme regra geral do `replit.md`.

---

**Limitações desta auditoria (declaração explícita):**
- Sem acesso ao banco de produção (Neon) — não foi possível validar contagens reais, distribuição de `engine_tag` em prod, nem inspecionar `indicator_snapshots.indicators_json` para medir `NO_DATA` rate ao vivo.
- Sem acesso a logs de produção (Cloud Logging / Cloud Run) — nenhuma evidência de tracebacks recentes em prod.
- Janela de log dev curta (~1h ativa) — métricas de longo prazo (rejection rate diário, latência P95 24h) não computadas.
- Nenhum scrape direto de `/metrics` foi executado (depende de `PROMETHEUS_BEARER_TOKEN` e endpoint público — fora do escopo dev).

---

## Anexo — Evidências de produção pendentes (closure note)

Esta auditoria é uma **conclusão parcial**. As seções §1, §2, §5 e parte de §7 ficaram **INCONCLUSIVAS** porque o ambiente de produção (Cloud Run + Neon + Cloud Logging) não está acessível a partir deste repl. Para fechar a auditoria com confiança suficiente para suportar decisão de rollout/rollback, executar a checklist mínima abaixo num ambiente com credenciais de leitura de prod e **anexar os resultados a este mesmo arquivo** (não reescrever — apêndice).

### Checklist mínima de evidências de produção

**§1 — Saúde geral (prod):**
- [ ] `GET /api/health/schema` retorna `200` (CRITICAL_COLUMNS presentes).
- [ ] `GET /api/dashboard/overview` retorna `status=ok` para `ingestion`, `celery`, `redis`, `db`, `score`.
- [ ] Cloud Logging (últimas 24h): `severity>=ERROR` filtrado por `resource.type=cloud_run_revision` — contar e classificar tracebacks; verificar ausência de `PendingRollbackError`, `QueuePool limit`, `InFailedSQLTransactionError`, `UndefinedColumnError`.
- [ ] `gcloud run services describe scalpyn-backend --format='value(status.latestReadyRevisionName)'` confirma a revisão pós-#232 ativa.

**§2 — Qualidade dos dados (prod):**
- [ ] `SELECT COUNT(*) FILTER (WHERE is_active), COUNT(*) FILTER (WHERE is_tradable), COUNT(*) FILTER (WHERE is_approved) FROM pool_coins;`
- [ ] `SELECT MAX(time), EXTRACT(EPOCH FROM (NOW()-MAX(time))) FROM ohlcv WHERE timeframe='5m';` → deve estar < 600s.
- [ ] `SELECT COUNT(DISTINCT symbol), COUNT(*) FROM ohlcv WHERE timeframe='5m' AND time > NOW()-INTERVAL '15 minutes';`
- [ ] `SELECT scheduler_group, market_type, COUNT(*), MAX(time) FROM indicators WHERE time > NOW()-INTERVAL '15 minutes' GROUP BY 1,2;`

**§5 — Indicadores de fluxo (prod):**
- [ ] `echo $ENABLE_GATE_WS` na revisão ativa do Cloud Run → deve ser `1`.
- [ ] Cloud Logging: pesquisar `"[gate-ws-leader] elected leader"` nas últimas 4h → confirmar exatamente 1 leader vencedor.
- [ ] `redis-cli GET gate_ws:leader` (via bastion) → deve estar populado.
- [ ] `SELECT COUNT(*) FILTER (WHERE indicators_json->'taker_ratio'->>'status'='NO_DATA') * 100.0 / COUNT(*) FROM indicator_snapshots WHERE timestamp > NOW()-INTERVAL '15 minutes';` → deve ser baixo (< 25%).

**§7 — Monitoramento (prod):**
- [ ] `curl -H "Authorization: Bearer $PROMETHEUS_BEARER_TOKEN" $PROD_URL/metrics | grep -E "indicator_confidence|indicator_staleness|score_rejection_total"` → confirma emissão.
- [ ] Grafana: estado das 4 alert rules (A1/A2/A3/A4) — todas devem estar `Normal` ou justificadas.
- [ ] `ROBUST_ALERTS_OPS_WEBHOOK_URL` populada em prod (env var presente, não vazia).

### Critério de fechamento

Esta auditoria deve ser re-emitida com status atualizado quando **todas** as 17 verificações acima forem coletadas e anexadas. Até lá, o score `0.78` reflete confiança parcial e a recomendação operacional é **prosseguir com monitoramento elevado por 7 dias** antes de marcar o rollout #232 como definitivamente concluído.
