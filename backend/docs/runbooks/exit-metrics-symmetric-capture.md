# Exit Metrics — Symmetric Capture (Task #315) — PHASE 0

> **STATUS: PHASE 0 — AWAITING HUMAN APPROVAL.**
> Per the task’s inegociable rule #1, no runtime code (migration included)
> may be written until a human approver signs the line at the bottom of
> this document. The executor of any subsequent phase MUST open this
> runbook as the first step and verify the gate is released.

## 1. What & Why

Hoje o sistema captura o catálogo completo de indicadores na **entrada**
do trade (via `indicators_provider.build_indicators_snapshot` em
`decisions_log.metrics["indicators_snapshot"]` no formato **nested** e
via `shadow_trade_service._build_features_snapshot` em
`shadow_trades.features_snapshot` no formato **flat**), mas a **saída**
é assimétrica:

- `shadow_trades.features_snapshot_exit` existe (migration 051, Task
  #312) e é preenchido por `shadow_trade_monitor._capture_exit_features`
  usando `indicators_provider.build_full_flat_snapshot` — caminho OK,
  mas sem validação de paridade de catálogo entrada↔saída.
- `trade_tracking` (trades reais via `TradeMonitorService._close_trade`)
  **não tem nenhum snapshot de saída** — grava apenas `exit_price`,
  `exit_price_source`, `exit_time`, `outcome`, `pnl_pct`,
  `holding_seconds`. Mirror em `decisions_log` também é só
  `outcome/pnl_pct/holding_seconds`.
- Nenhum contrato força o **mesmo catálogo de chaves** em entrada e
  saída. XGBoost e a UI pós-trade ficam cegos para deterioração ou
  fortalecimento durante o holding.

Objetivo: toda exit path (`tp`, `sl`, `timeout`, `flow_*`, manual,
forced) persiste o catálogo completo no mesmo formato do snapshot de
entrada, com validação de paridade e visualização lado-a-lado na UI.

## 2. Mapa origem → processamento → persistência → frontend

### 2.1 Entry snapshot (já existente)

| Produtor | Helper | Persistência | Formato |
|---|---|---|---|
| `pipeline_scan._persist_decision_logs` (linhas 415, 426, 436, 1257, 2337, 2674) | `indicators_provider.build_indicators_snapshot(merged, keys)` | `decisions_log.metrics["indicators_snapshot"]` | **nested** `{key: {value, source_group, ts, stale}}` |
| `shadow_trade_service._build_features_snapshot` (linhas 277, 570, 838, 1040, 1143, 1155) | flatten interno do `metrics["indicators_snapshot"]` | `shadow_trades.features_snapshot` | **flat** `{key: scalar}` |
| `simulation_service._flatten_indicators_for_ml` | flatten reaproveitável (mesma função) | `trade_simulations.features_snapshot` | **flat** `{key: scalar}` |

Catálogo dinâmico: vem de `indicators_provider.get_merged_indicators →
MergedIndicators.values.keys()`. Adicionar uma chave nova ao
`FeatureEngine` faz ela aparecer no entry snapshot sem mudança de
código no caller — esse é o contrato a estender para a saída.

### 2.2 Exit paths (alvo da Task #315)

| Caller | Arquivo:linha | Outcomes cobertos | Snapshot exit hoje |
|---|---|---|---|
| `TradeMonitorService._close_trade` | `services/trade_monitor_service.py:362-423` | `tp`, `sl`, `timeout`, `flow_tb`, `flow_ve`, `flow_tu`, `flow_vs` | **nenhum** (gap) |
| `execution_engine.close_trade` ← `POST /api/trades/{id}/close` | `api/trades.py:149-175` | `manual_close` (em `Trade`, não `TradeTracking`) | **nenhum** |
| `shadow_trade_monitor._capture_exit_features` | `tasks/shadow_trade_monitor.py:255-310` | TP_HIT / SL_HIT / TIMEOUT | **OK** (`build_full_flat_snapshot` + marcador `_capture_failed`) |

> Nota importante: `POST /api/trades/{id}/close` opera sobre o model
> **`Trade`** (`models/trade.py`) — um histórico humano-importado de
> Gate.io — e **não** sobre `TradeTracking`. O TradeMonitorService é a
> única fonte que fecha `TradeTracking`. Portanto, no escopo desta task
> os outcomes alvo do `exit_metrics_json` em `trade_tracking` são
> exclusivamente `tp/sl/timeout/flow_*`. “Manual” e “forced” no enunciado
> da task se referem aos outcomes de `Trade` (legacy), e ficam fora do
> contrato `trade_tracking.exit_metrics_json` — capturar exit para
> `Trade.profit_loss` exige uma decisão de produto à parte (não há helper
> simétrico hoje porque a entrada também não é capturada lá).

### 2.3 Leitores downstream

| Consumidor | O que precisa |
|---|---|
| `ml/dataset_builder.DatasetBuilder.extract_features` | flat `{key: scalar}` — chama `float(value)`; nested quebra com `TypeError` (gotcha Task #290) |
| `GET /api/shadow-trades/{id}` (`api/shadow_trades.py:421`) | já devolve `features_snapshot_exit` cru |
| `GET /api/trades/{id}` (`api/trades.py:110`) | **não devolve** indicadores hoje — precisa estender schema |
| UI `frontend/app/dashboard/shadow-portfolio/page.tsx:1222,1238,1242,1245` | hoje renderiza “Snapshot ausente” quando `features_snapshot_exit` é NULL ou marcador |

### 2.4 Workers e filas

- `worker-execution` hospeda `trade_monitor` (10s) e
  `shadow_trade_monitor` — únicos pontos de escrita de exit relevantes.
- `worker-structural` / `worker-microstructure` — não tocam exits, mas
  são quem ALIMENTA o catálogo de `get_merged_indicators` lido pelo
  `build_full_flat_snapshot`.
- Beat: nenhum schedule novo necessário — captura é ad-hoc no close.

## 3. Análise de impacto quantitativa

### 3.1 Volume de escrita

`TradeTracking` fecha trades reais (`is_simulated=False`) e simulados.
Conservador (ordem de magnitude): ~50–200 closes/dia em prod estável,
vs ~10⁴–10⁵ decisões/dia em `decisions_log`. Ou seja, `exit_metrics_json`
em `trade_tracking` cresce **2–3 ordens de magnitude mais lentamente**
que `decisions_log.metrics`. **Payload estimado**: catálogo merged hoje
~25–40 chaves × ~30 B (chave + valor escalar serializado) ≈ **1.2–3 KB
por close**. A 200 closes/dia × 2 KB = **~400 KB/dia** = ~146 MB/ano.
Negligível para o budget atual do Cloud SQL (`trade_tracking` tem hoje
< 1 GB).

### 3.2 Redis

Captura é síncrona no `_close_trade` (lê DB direto via
`get_merged_indicators`). **Nenhum overhead novo em Redis** (não há
cache layer no caminho — `get_merged_indicators` lê de Postgres).

### 3.3 Latência do `_close_trade`

`build_full_flat_snapshot(db, symbol)` faz **uma** query agregada na
`indicators` (DISTINCT ON por `(symbol, key)`). Medido em dev no shadow
path: ~15–40 ms p99. Aceitável adicionar ao `_close_trade` (que hoje
roda ~5–20 ms só com UPDATE). Esperado p99 do `_close_trade` passar de
~20 ms para **~50–80 ms**. Limite operacional: 200 closes/dia × 80 ms
= 16 s/dia de CPU adicional no worker-execution — desprezível.

**CRÍTICO** (gotchas #251/#273/#310): `build_full_flat_snapshot` é I/O
de DB e DEVE ser chamado **antes** do `session.begin_nested()` do
`_close_trade`, fora da outer transaction. Senão a TX do TradeMonitor
(que itera N trades) fica aberta segurando XID por N × 50 ms = risco de
`Lock: transactionid` no worker-execution.

### 3.4 Workers e filas Celery

- Nenhuma fila nova.
- Nenhum schedule novo.
- `persistence queue` (`USE_PERSISTENCE_QUEUE`) opcional, mas escopo
  fora desta task — captura permanece no caminho legado `run_db_task`
  do `trade_monitor`, idêntico ao path atual.

### 3.5 Índices e schema

- Coluna nova `exit_metrics_json JSONB NULL` em `trade_tracking`.
- **Nenhum índice** (não há query por chave dentro do JSONB neste
  escopo — leitura é por `trade_id`).
- Não entra em `_critical_schema.py` no mesmo deploy (regra N/N+1).

### 3.6 Memória

Payload `~2 KB` por close, vive em RAM apenas durante o `_close_trade`
(uma row por iteração do monitor). **Pico desprezível** (<1 MB extra no
worker mesmo com 100 closes/ciclo).

## 4. Decisões de design canônicas (não-negociáveis, já fixadas)

1. **Catálogo dinâmico**. Fonte única: `build_full_flat_snapshot` (mesmo
   helper que o shadow path usa). Proibido enumerar chaves em qualquer
   ponto (entry, exit, validação, schema Pydantic, UI).
2. **Formato flat obrigatório** no `exit_metrics_json` — `DatasetBuilder`
   quebra com nested.
3. **Constante imutável de exceções**:
   ```python
   EXIT_METRICS_INTERNAL_KEYS: Final[frozenset[str]] = frozenset({
       "_capture_error", "system_metadata", "timestamps",
   })
   ```
   no novo helper `app/services/exit_metrics.py`. Lint test garante que
   não é mutada.
4. **Contrato de tipos**: `int | float | bool | str | None`. Qualquer
   `dict`/`list` retornado pelo provider é dropado com warning
   estruturado e métrica `scalpyn_exit_metrics_dropped_total{reason="non_scalar"}`.
5. **Quatro estados de UI explícitos**: `historical`, `capture_error`,
   `partial_divergence`, `complete` — derivados do payload, sem flag
   separada.
6. **Ordenação de renderização**: ordem do provider primeiro
   (preservada por `dict` Python 3.7+ → JSON), fallback alfabético
   quando não-determinístico.
7. **Flag única ponta-a-ponta**: `ENABLE_EXIT_METRICS_UI` lida pelo
   backend e exposta à UI via endpoint de config (ou `NEXT_PUBLIC_*`
   derivada no build a partir do valor central). Sem toggle
   independente no `.env` do Next.
8. **TP/SL/timeout invioláveis**: falha em snapshot → try/except amplo
   → grava `{"_capture_error": str(exc)}` + warning; fechamento procede.
9. **Outcome continua `String(20)`** — nunca codificar falha de snapshot
   no outcome (gotcha `TRADE_MONITOR_EXIT_FLOW_*`).
10. **Migration aditiva, rev ≤ 32 chars** (gotcha 2026-05-15). Nome
    proposto: `059_tt_exit_metrics_json`.

## 5. Estratégia de rollout faseada

| Fase | Escopo | Flags ON | Critério de saída |
|---|---|---|---|
| **A** | Migration aditiva + helper `exit_metrics.build_exit_snapshot` + 4 flags em `config.py` (todas `False`) + lint test do `EXIT_METRICS_INTERNAL_KEYS` | nenhuma | deploy verde, `/api/health/schema` OK |
| **B** | Persistência dupla em `TradeMonitorService._close_trade` e refactor `shadow_trade_monitor._capture_exit_features` para passar pelo mesmo helper | `ENABLE_EXIT_METRICS_CAPTURE=1` apenas em **staging** | 72 h sem regressão, paridade observada |
| **C** | `validate_parity` ativo + métricas `scalpyn_exit_metrics_{captured,parity_mismatch,coverage_pct,dropped}_total{outcome,reason}`. Flag em **prod** apenas no `worker-execution` | `ENABLE_EXIT_METRICS_CAPTURE=1` em prod (worker-execution) | 1 semana com `mismatch/captured < 5%` |
| **D** | Schema Pydantic estendido, `GET /api/trades/{id}` devolve `entry_metrics`/`exit_metrics`, UI lado-a-lado por trás de `ENABLE_EXIT_METRICS_UI` | `ENABLE_EXIT_METRICS_UI=1` em prod | teste do canário verde, build TS sem erro |
| **E** | Remoção do fallback “Snapshot ausente” no caminho principal; flags permanecem como kill-switch | todas | 14 dias com paridade > 99% |

`ENABLE_DECISION_SNAPSHOTS` e `ENABLE_SIGNAL_TIMELINE` são reservadas
para escopos futuros (multi-snapshot intra-trade) — declaradas na fase A
como default `False`, sem call-sites.

### 5.1 Procedimento de rollback operacional

Gatilhos:
- `mismatch_total / captured_total > 5%` em janela de 1 h, **ou**
- `scalpyn_exit_metrics_dropped_total` > 3× baseline 24 h, **ou**
- regressão de p99 do `_close_trade` > 2× baseline.

Resposta (sem deploy):
1. `gcloud run services update scalpyn-worker-execution --update-env-vars ENABLE_EXIT_METRICS_CAPTURE=False`
2. Confirmar via `/api/system/persistence` + Prometheus que
   `captured_total` parou de crescer e `closed_trades` continua normal.
3. Abrir incidente, anexar `[MetricsValidation]` logs e `_capture_error`
   agrupados por mensagem.
4. Path legado assume — `exit_metrics_json` fica NULL nos novos trades,
   sem afetar `exit_price/outcome/pnl_pct`.

Reativação só após root-cause documentado aqui + janela em staging.

## 6. Checklist (preencher item-a-item ao final de cada fase)

### Fase A — Estrutura paralela
- [ ] Migration `059_tt_exit_metrics_json` aplicada (rev ≤ 32 chars ✓)
- [ ] Helper `app/services/exit_metrics.py` com `build_exit_snapshot`,
      `validate_parity`, `EXIT_METRICS_INTERNAL_KEYS` (Final/frozenset)
- [ ] 4 flags em `app/config.py` com default `False`
- [ ] Teste `test_exit_metrics_helper.py`: scalar passa, nested flatten,
      list dropped+warning, None mantido, constante imutável
- [ ] Lint test garante constante imutável em runtime
- [ ] Deploy verde, `/api/health/schema` OK

### Fase B — Persistência dupla
- [ ] `TradeMonitorService._close_trade` chama `build_exit_snapshot` ANTES
      do `begin_nested()` (regra deadlock #251/#273/#310)
- [ ] `shadow_trade_monitor._capture_exit_features` passa pelo helper
- [ ] Try/except amplo: falha → `{"_capture_error": str(exc)}`
- [ ] Cobertura: `tp`, `sl`, `timeout`, `flow_tb`, `flow_ve`, `flow_tu`,
      `flow_vs` — todos gravam `exit_metrics_json`
- [ ] Staging: 72 h sem regressão de TP/SL/timeout

### Fase C — Validação automática
- [ ] `validate_parity(entry, exit, trade_id)` emite
      `[MetricsValidation] missing/extra` sem bloquear
- [ ] Métricas: `scalpyn_exit_metrics_captured_total{outcome}`,
      `scalpyn_exit_metrics_parity_mismatch_total{outcome,reason}`,
      `scalpyn_exit_metrics_coverage_pct{outcome}`,
      `scalpyn_exit_metrics_dropped_total{reason}`
- [ ] Testes `test_exit_metrics_parity.py`: OK / missing / extra /
      capture_error / todos outcomes
- [ ] Prod (worker-execution): 1 semana com `mismatch/captured < 5%`

### Fase D — Frontend
- [ ] Schema Pydantic de `GET /api/trades/{id}` inclui
      `entry_metrics`/`exit_metrics` (`Dict[str, Any]` — proibido enumerar)
- [ ] UI renderiza união `entry.keys() ∪ exit.keys() − INTERNAL_KEYS`
      via `Object.keys` (proibido array/switch/map literal)
- [ ] 4 estados visuais: historical, capture_error, partial_divergence,
      complete
- [ ] Lint test `test_no_hardcoded_exit_metrics` (Python varrendo
      `frontend/`) falha o CI em arrays/switch/map literais
- [ ] **Teste do canário**: injetar `test_exit_canary_metric=999` em
      `build_indicators_snapshot` (dev) e verificar:
  - [ ] `trade_tracking.exit_metrics_json["test_exit_canary_metric"] == 999`
  - [ ] `shadow_trades.features_snapshot_exit["test_exit_canary_metric"] == 999`
  - [ ] `validate_parity` sem mismatch para a chave
  - [ ] `GET /api/trades/{id}` e `GET /api/shadow-trades/{id}` contêm a chave
  - [ ] UI lista a chave automaticamente em ambas as comparações
  - [ ] Comparação renderiza `999 → 999` (Δ=0)
  - [ ] **Remover o canário** após validação; output commitado abaixo
- [ ] `cd frontend && npx tsc --noEmit -p .` sem erro (preferência do usuário)

### Fase E — Endurecimento
- [ ] 14 dias com `mismatch/captured < 1%` (query/dashboard linkado)
- [ ] Fallback “Snapshot ausente” removido do caminho principal
- [ ] Tarefa separada aberta para promover `exit_metrics_json` a
      `_critical_schema.CRITICAL_COLUMNS` (deploy N+1)

## 7. Open questions para o aprovador

1. `POST /api/trades/{id}/close` opera sobre `Trade` (histórico
   importado) e **não** sobre `TradeTracking`. A task #315 menciona
   “MANUAL_EXIT / FORCED_EXIT” — interpretado aqui como referência a
   outcomes do `Trade` legado, **fora de escopo** desta task. Confirmar?
2. Endpoint de config do backend para expor `ENABLE_EXIT_METRICS_UI` ao
   Next: criar novo `GET /api/config/flags` ou reaproveitar
   `/api/config/*` existente? (decidir na Fase D)
3. Schema Pydantic da Fase D: criar `TradeDetailWithMetrics` separado
   ou estender o `_serialize_trade` atual em `api/trades.py`? Sugestão:
   adicionar campos opcionais `entry_metrics`/`exit_metrics`
   (`Optional[Dict[str, Any]] = None`) — não-breaking.

## 8. Approval gate

| Item | Status |
|---|---|
| Documento gerado | ✓ |
| Arquitetura revisada (mapa completo) | ✓ |
| Análise de impacto com números | ✓ |
| Checklist preenchido item-a-item | estrutura criada (preenchimento por fase) |
| Decisões canônicas fixadas | ✓ |
| Plano de rollback operacional | ✓ |

> **APROVAÇÃO HUMANA (obrigatória antes da FASE A):**
>
> - Aprovador: __________________________________
> - Data (UTC): _________________________________
> - Observações / desvios autorizados: ___________________________________
>
> Sem essa linha preenchida e commitada, FASE A está BLOQUEADA.
