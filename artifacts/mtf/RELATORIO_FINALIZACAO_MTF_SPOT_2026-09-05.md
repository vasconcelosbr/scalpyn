# Relatório consolidado — Finalização MTF Spot

Data da execução: `[query] 2026-09-05`  
Escopo: Spot, `[config] L1/1h → L2/15min → L3/5min`  
Commit de implementação: `[git] 2b424a472b222142007fd99a0cfb0f5097426905`

## Veredito

**Código e infraestrutura: implantados. Ativação observacional: corretamente bloqueada por `CONFIG_REQUIRED`.**

Os produtores governados, contratos encadeados, validações de integridade, importação MTF, persistência shadow, auditoria de runtime, interface e rollback foram implementados e publicados. O caminho legado de ordens permanece autoritativo e a configuração MTF continua desabilitada.

A calibração não foi executada porque não existe configuração ativa `mtf_calibration` com o mínimo amostral e demais parâmetros exigidos. O calibrador respondeu literalmente:

```json
{"profiles_activation_mode":"DRAFT","reason":"CONFIG_REQUIRED:min_samples,fold_count,train_window_rows,test_window_rows,candidate_quantiles,cost_field,return_field","status":"CONFIG_REQUIRED","thresholds_emitted":false}
```

Consequentemente, nenhum threshold foi inventado, nenhum profile MTF foi importado e nenhuma decisão MTF foi registrada como observação. O JSON entregue permanece `DRAFT`.

## Artefatos

- Código backend/frontend: commit `[git] 2b424a472b222142007fd99a0cfb0f5097426905`.
- Branch auditável: `[git] codex/mtf-spot-1h-15m-5m-20260905`.
- JSON importável DRAFT: `artifacts/mtf/profiles-mtf-l1-1h-l2-15m-draft.json`.
- Relatório consolidado: este arquivo.
- Migração: `[query] NÃO CRIADA`; os JSONB e campos existentes comportaram os envelopes e vereditos.
- Grafo: `[query] graphify update .` concluído; `[query] 22.001 nós`, `[query] 32.486 arestas`, `[query] 1.647 comunidades`.

## Matriz requisito × evidência

| Requisito | Estado | Evidência |
|---|---|---|
| Preservar checkout sujo | ATENDIDO | Implementação em worktree limpo baseado em `[git] origin/main@f6b1ce53741a9c4c8da560f67226ae4342b2c211`; checkout original não alterado. |
| Produzir `[config] 1h` e `[config] 15min` após candles fechados | ATENDIDO | Collectors canônicos filtram candles abertos, fazem commit e enfileiram o produtor exato; beat enviou a tarefa `[query] collect_mtf_15m_after_close`. |
| Não reutilizar `[config] 30min` como `[config] 15min` | ATENDIDO | Provider MTF exige igualdade de símbolo, mercado, timeframe e scheduler group; não possui fallback temporal. |
| Config ativa + versão/hash no cálculo | ATENDIDO | Todos os envelopes recentes possuem o mesmo hash governado `[query] 6f8e413697fbd48c833e04cf71c2afc255118d04aef20d4fb5a49de0e4528418` e versão de produtor. |
| `UNAVAILABLE` sem fallback | ATENDIDO | Inputs ausentes, vencidos, incompatíveis, com hash inválido ou candle aberto fecham em indisponibilidade/`WAIT`. |
| L1/1h, L2/15min, L3/5min estritos | ATENDIDO | `ProfileEngine(strict_timeframe_mode=True)` e providers exatos; testes de contradição e fallback plano passaram. |
| Contextos versionados e encadeados | ATENDIDO | `L1DecisionContextV2`, `L2DecisionContextV1` e `MultilayerDecisionContextV2`; L2 sela o hash L1 e L3 sela os dois contextos. |
| Verificação de adulteração/replay | ATENDIDO | Hashes de envelopes e contextos são recomputados na leitura; testes de adulteração, expiração e identidade passaram. |
| Indicadores L1/L2/L3 | ATENDIDO NO CÓDIGO | L1: EMA, DMI completo, ADX, ATR relativo e estrutura. L2: direção, ATR normalizado, VWAP, bandas/volume e setup state. L3: snapshot canônico `[config] 5min` com proteções legadas. Thresholds permanecem ausentes por governança. |
| Semântica `REJECT/WAIT` | ATENDIDO | `REJECT` domina; `INSUFFICIENT_DATA` e `UNAVAILABLE` geram `WAIT`; nunca aprovam implicitamente. |
| Import MTF seguro | ATENDIDO | Aceita somente `MTF_LAYER` L1/L2 em `DRAFT` ou `SHADOW`; `ACTIVE` é rejeitado antes de escrita. |
| Associação imutável | ATENDIDO | Contrato exige `profile_id`, `profile_version_id` e `profile_config_hash`; nome/funil não autorizam camada. |
| JSONs finais calibrados | BLOQUEADO PELO PLANO | `[query] 0` configs `mtf_calibration`; entregue JSON DRAFT com thresholds nulos e `thresholds_emitted:false`. |
| Auditoria de runtime | ATENDIDO | Endpoint autenticado novo informa produtores, cobertura, profiles, hashes e decisões; cross-check independente executado no banco. |
| Dataset/calibração walk-forward | PRIMITIVAS IMPLEMENTADAS; EXECUÇÃO BLOQUEADA | Folds cronológicos, gate amostral, expectativa líquida, drawdown, dispersão e complexidade implementados; execução interrompeu em `CONFIG_REQUIRED` antes de dataset/thresholds. |
| Shadow não altera ordens | ATENDIDO | Contexto é anexado somente em métricas e shadow; `operational_effect:false`; nenhuma variável legada de decisão é substituída. |
| Rollback reversível | ATENDIDO | Endpoint dedicado desabilita observação preservando histórico; procedimento completo abaixo. |

## Evidência de testes

- Regressão focada MTF/contratos/providers/profiles/filas/pipeline: `[query] 145 passed`.
- Frontend: `[query] 83 passed`, TypeScript `[query] exit 0`, build Next.js `[query] 44 rotas`, `[query] exit 0`.
- Suíte backend completa: `[query] 2.352 passed`, `[query] 5 skipped`, `[query] 27 failed`, `[query] 13 errors`.
- As falhas da suíte completa foram classificadas: dependência de Postgres local ausente, API local em `localhost:8001` ausente, teste legado preso ao head Alembic `[query] 204` enquanto `origin/main` já usa `[query] 215`, e fixtures/expectativas preexistentes. A regressão focada dos módulos alterados ficou integralmente verde.
- Análise estática Ruff: `NÃO DISPONÍVEL`; o módulo não está instalado no ambiente local.

## Evidência de produção

### Fonte e deploy

- Paridade Git antes do deploy: `[git] HEAD = origin/main = 2b424a472b222142007fd99a0cfb0f5097426905`.
- Railway API: `[query] SUCCESS`, deployment `5fb97f31-ca82-410f-9123-6a66b88c593a`.
- Railway beat: `[query] SUCCESS`, deployment `a4179d33-aaa4-408e-b5a6-75b1a4db8dd1`, commit `2b424a4`.
- Railway worker estrutural: `[query] SUCCESS`, deployment `fc726539-370c-4ae6-bfe4-1d8003235f89`, commit `2b424a4`.
- Railway worker compute: `[query] SUCCESS`, deployment `7be6ecd2-b671-4f5a-b60f-7a03474b5839`, commit `2b424a4`.
- Railway worker micro: `[query] SUCCESS`, deployment `362feb4c-3cba-41e1-aa38-3c8d5ce92a96`, commit `2b424a4`.
- Vercel frontend: `[query] READY`, deployment `dpl_yWFFpu8uTX57qzp1U2nYah9RNhLE`, produção `https://frontend-ozddgiusx-ricardovasconcelos-1177s-projects.vercel.app`.
- Rota publicada `/settings/strategies`: `[query] HTTP 200` e `X-Matched-Path: /settings/strategies`.
- Interface autenticada: **NÃO CONFIRMADA**; o controlador de navegador falhou duas vezes antes de abrir a tela com `failed to write kernel assets`.

O primeiro upload manual da API falhou por raiz duplicada (`backend` aplicado pelo upload e pelo serviço). O upload foi refeito a partir da raiz do repositório e terminou em `SUCCESS`; a revisão anterior permaneceu ativa durante a tentativa falha.

O prebuild Vercel local compilou o Next.js, mas o empacotamento encontrou `EPERM` ao criar symlink no Windows. O build remoto oficial compilou, tipou, gerou as rotas e publicou com estado `READY`.

### Saúde e schema

- `/api/health`: `[query] status=ok`, versão `[query] 0.2.0`.
- `/api/health/schema`: `[query] schema_ok=true`, `[query] 41` itens verificados, `[query] 0` ausentes.
- Alembic em produção: `[query] 215_r6_multilayer_contracts`.
- Migração MTF adicional: `[query] 0`.

### Produtores e identidade temporal

Rodada explícita canônica para prova imediata:

- `[query] 15min`: alvo `[query] 65`, sucesso `[query] 65`, falhas `[query] 0`, cálculo `[query] 65`, skips `[query] 0`.
- `[query] 1h`: alvo `[query] 65`, sucesso `[query] 65`, falhas `[query] 0`, cálculo `[query] 65`, skips `[query] 0`.

Rodada automática comprovada:

- Beat enviou `collect_mtf_15m_after_close`.
- Worker estrutural recebeu e concluiu: alvo `[query] 65`, sucesso `[query] 65`, falhas `[query] 0`, `compute_enqueued=true`.
- Worker compute recebeu com espera `[query] 0,01s` e concluiu: calculados `[query] 65`, skips `[query] 0`.

Cross-check independente das últimas linhas por símbolo/grupo:

| Identidade | Linhas mais recentes | Hash inválido | Identidade inválida | Candle aberto | Produtor |
|---|---:|---:|---:|---:|---|
| `[config] 1h:structural` | `[query] 65` | `[query] 0` | `[query] 0` | `[query] 0` | `mtf_indicator_producer_v1` |
| `[config] 15m:structural` | `[query] 65` | `[query] 0` | `[query] 0` | `[query] 0` | `mtf_indicator_producer_v1` |
| `[config] 5m:structural` | `[query] 65` | `[query] 0` | `[query] 0` | `[query] 0` | `compute_structural_5m_v2` |
| `[config] 5m:microstructure` | `[query] 65` | `[query] 0` | `[query] 0` | `[query] 0` | `compute_5m_v2` |

### Estado seguro de ativação

- Símbolos Spot ativos: `[query] 65`.
- Configurações ativas `mtf_calibration`: `[query] 0`.
- Profiles `MTF_LAYER`: `[query] 0`.
- Contrato persistido: `[query] enabled=false`; `activation_mode` e `operational_effect` ainda não materializados na configuração legada.
- Decisões nas últimas `[query] 24h`: total `[query] 119.702`; com `multilayer_decision_context_v2`: `[query] 0`.

Isso prova que o deploy do código não alterou as decisões reais nem iniciou observação sem profiles calibrados.

## Riscos e pendências

1. **Governança de calibração ausente.** Criar por interface/config versionada `mtf_calibration` com `min_samples`, folds, janelas, quantis candidatos, campo de custo real e campo de retorno. Não preencher por conveniência.
2. **Interface autenticada não comprovada.** Repetir a validação quando o controlador de navegador estiver disponível; a rota e o bundle publicados já estão comprovados.
3. **Espaço temporário do Postgres.** Uma consulta ampla de auditoria, sem filtro temporal, retornou `DiskFullError: No space left on device`. A consulta foi substituída por janela recente e concluiu. Investigar volume/temp e índices antes de auditorias históricas amplas.
4. **Latência da fila estrutural.** A tarefa automática foi recebida após backlog, embora tenha concluído integralmente e encadeado compute com espera curta. Monitorar tempo entre emissão do beat e recepção.
5. **Avisos existentes.** Build Next.js emite aviso de dimensão de gráfico; `npm audit` reporta `[query] 2` vulnerabilidades (`[query] 1 low`, `[query] 1 moderate`). Nenhum auto-fix foi aplicado nesta entrega.

## Próximo gate para iniciar SHADOW

1. Persistir configuração de calibração governada.
2. Executar dataset point-in-time e walk-forward.
3. Exigir baseline superado na mediana dos folds e pior drawdown não pior.
4. Gerar profiles com thresholds e validade calibrados, `activation_mode:"SHADOW"`.
5. Importar os profiles e validar `profile_id + profile_version_id + profile_config_hash`.
6. Confirmar cobertura/warmup; o endpoint de ativação refaz hashes e bloqueia qualquer símbolo/grupo ausente ou vencido.
7. Ativar somente via `/api/strategy-settings/multilayer-shadow/activate?apply=true`.
8. Confirmar `operational_effect:false` e iniciar relatório de divergência legado × MTF.

Nova autorização humana será necessária para qualquer promoção do MTF às ordens reais.

## Procedimento de rollback

### Configuração

Se SHADOW tiver sido ativado no futuro, chamar primeiro:

```text
POST /api/strategy-settings/multilayer-shadow/disable?apply=true
```

O endpoint define `enabled=false`, `activation_mode=DRAFT`, `operational_effect=false`, desabilita as três camadas e preserva todo histórico.

### Código Railway

1. Criar um revert auditável do commit `[git] 2b424a472b222142007fd99a0cfb0f5097426905` e avançar `main` por fast-forward.
2. Confirmar os workers Git-linked no novo commit.
3. Reimplantar a API manual a partir da raiz do repositório.
4. Como referência, deployments anteriores: API `3f6c4736-aec3-45c5-867f-c64e239fd286`; beat `1e5f51ea-c6cd-4214-b18f-527e726ce54c`; estrutural `afdda1eb-12fb-4ec6-8079-f4069194daa9`; compute `961c4861-54d9-4877-b4ac-ca13999ead52`; micro `eeeb8be6-fd13-44fb-bfa8-dd9a479cae89`.
5. Repetir `/api/health`, `/api/health/schema`, logs e consulta de contrato.

### Frontend Vercel

Executar rollback para o deployment anterior comprovado:

```text
vercel rollback https://frontend-frz1w64zg-ricardovasconcelos-1177s-projects.vercel.app
```

Depois verificar a alias de produção e `/settings/strategies` com `vercel inspect` e `vercel curl`.

## Ledger de evidências numéricas

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| Commit | `[git]` | `2b424a472b222142007fd99a0cfb0f5097426905` |
| Regressão focada | `[query] pytest` | `145 passed in 4.94s` |
| Suíte backend | `[query] pytest` | `27 failed, 2352 passed, 5 skipped, 13 errors in 90.90s` |
| Testes frontend | `[query] npm test` | `tests 83; pass 83; fail 0` |
| Build frontend | `[query] next build` | `Generating static pages using 11 workers (44/44); exit_code=0` |
| Grafo | `[query] graphify update` | `22001 nodes, 32486 edges, 1647 communities` |
| Saúde schema | `[query] GET /api/health/schema` | `schema_ok=true; checked_count=41; missing=[]` |
| Coleta explícita 15min | `[query] collector` | `target_symbols=65; successful_symbols=65; failed_symbols=0; closed_rows_submitted=32435` |
| Cálculo explícito 15min | `[query] producer` | `computed=65; skipped=0` |
| Coleta explícita 1h | `[query] collector` | `target_symbols=65; successful_symbols=65; failed_symbols=0; closed_rows_submitted=32435` |
| Cálculo explícito 1h | `[query] producer` | `computed=65; skipped=0` |
| Coleta automática 15min | `[query] worker log` | `target_symbols=65; successful_symbols=65; failed_symbols=0; closed_rows_submitted=32421; compute_enqueued=true` |
| Cálculo automático 15min | `[query] worker log` | `queue_wait_s=0.01; computed=65; skipped=0` |
| Cobertura recente | `[query] DB` | `rows=65` em cada uma das quatro identidades; `hash_invalid=0`; `identity_invalid=0`; `open_candle_inputs=0` |
| Config calibração | `[query] DB` | `active_mtf_calibration_configs=0` |
| Profiles MTF | `[query] DB` | `mtf_layer_profiles=0` |
| Decisões MTF | `[query] DB` | `total=119702; with_mtf=0` nas últimas `24 hours` |
| Vulnerabilidades npm | `[query] npm ci` | `2 vulnerabilities (1 low, 1 moderate)` |

