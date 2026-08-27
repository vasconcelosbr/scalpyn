# Correção confirmada da instrumentação do Shadow Portfolio

## Estado da entrega

- Fonte: `origin/main` em `88009d54e9c7e30dcc130a613e92ae313ece2b8f` `[query: git rev-parse]`.
- Branch: `codex/shadow-instrumentation-contract-20260827` `[query: git status]`.
- Nenhum deploy, migration online ou backfill com escrita foi executado.
- O checkout original permaneceu separado e não foi alterado por esta entrega.

## Causa raiz confirmada

O caminho rápido do monitor encerrava o Shadow pelo preço corrente antes de
percorrer todas as velas OHLCV ainda pendentes. Como o estado legado de
`min_price_post_entry`/`max_price_post_entry` não representava necessariamente
toda a vida do trade, `_finalize_outcome` persistia MFE/MAE incompletos. O clamp
existente não é a causa raiz e não foi usado como correção.

O preço de entrada possuía um segundo relógio: `_create_from_decision` consultava
OHLCV depois da decisão. A correção usa primeiro o `price_envelope` imutável que
o pipeline efetivamente avaliou; qualquer fallback legado fica explicitamente
`DEGRADED` e inelegível para treino.

Os timestamps ausentes vieram de duas perdas de proveniência: `alpha_scores.time`
não era transportado para os cinco scores de contexto, e as duas condições EMA
derivadas apagavam seus timestamps. O contrato passa a ser
`point-in-time-v3`, preservando o mapa completo das dependências e validando o
input mais novo contra decisão/captura/entrada.

## Implementação

- Tabela `shadow_trade_measurement_revisions`, append-only, com trigger contra
  `UPDATE`/`DELETE`, chave idempotente, valores legados e recalculados, qualidade
  de entrada, custo fee-only e constraints condicionais para `READY`.
- Reconciliador OHLCV por prioridade configurada, incluindo velas limítrofes e
  distinguindo `PENDING`, `READY`, `UNAVAILABLE` e `ERROR`.
- Retry de revisão `PENDING` no monitor, sem alterar outcome, barreiras ou
  qualquer motor econômico.
- Configuração editável para prioridade de timeframes e lag máximo. Ausência
  resulta em `UNCONFIGURED`, nunca em fallback numérico.
- Elegibilidade fail-closed para qualidade de entrada, linhagem e contrato de
  profile `MATCH`; divergência recebe `INVALID_PROFILE_CONTRACT`.
- `NO_ELIGIBLE_MODEL_FOR_LANE` foi preservado e ganhou o detalhe aditivo
  `NO_ACTIVE_MODEL_FOR_L3_PROFILE`; a lane continua `L3_PROFILE`.
- Relatório/export aceitam TP, SL, stop móvel, prazo operacional e abertos. A
  seleção persiste a base temporal por status e `excluded_count_by_outcome`.
- Retorno líquido fee-only é o valor principal; bruto fica como detalhe e spread
  é declarado `NOT_APPLIED`.

## Evidências E1–E8

### E1 — sentinelas sintéticas

Fixtures cobrem TP após drawdown, SL após excursão positiva, vela parcial de
entrada, vela de saída ainda aberta, OHLCV indisponível e hash determinístico.
Resultado: `6 passed` `[query: pytest test_shadow_trade_measurement_service.py]`.

### E2 — SUI conhecido

Dry-run read-only, sem inserção:

| Campo | Legado | Recalculado | Fonte |
|---|---:|---:|---|
| MFE | `0.0` | `0.5057053941908807` | `[query: dry-run OHLCV 1m]` |
| MAE | `-0.7261410788381807` | `-0.790975103734437` | `[query: dry-run OHLCV 1m]` |
| MFE em | `NULL` | `2026-08-27 13:03:00+00:00` | `[query: dry-run OHLCV 1m]` |
| MAE em | `2026-08-27 13:32:00+00:00` | `2026-08-27 13:32:00+00:00` | `[query: dry-run OHLCV 1m]` |
| inserções | — | `0` | `[query: mode=DRY_RUN]` |

### E3 — cohort do report

Sobre o report `62a1d380-ca88-4b7f-85ee-234d9d765dc8` `[query]`:

- selecionados: `31` `[query]`;
- revisões `READY`: `31` `[query]`;
- TP: `17`; SL: `14` `[query]`;
- MAE zero entre TP: `14 → 4` `[query: legado → recalculado]`;
- MFE zero entre SL: `11 → 2` `[query: legado → recalculado]`;
- indisponíveis: `0` `[calc: selected 31 - READY 31]`.

Zeros remanescentes não foram alterados artificialmente: representam o limite
observável do OHLCV disponível segundo a convenção de velas limítrofes.

### E4 — slippage realizado

**Não executável.** Shadow não cria fill e não existe trade tape/book histórico
persistido que permita reconstruir `entry_price_realized`. O campo permanece
`NULL`; produzir média, IC ou expectancy “corrigida” inventaria uma execução que
não ocorreu.

### E5 — elegibilidade

O transporte dos sete timestamps e o fail-closed estão cobertos por testes. A
série pós-correção não existe sem deploy e novas capturas; portanto o resultado
pós-correção é `NÃO DISPONÍVEL`. Os contratos de profile divergentes são
classificados, não reescritos por este ticket.

### E6 — export sem censura

Contrato, API e UI foram implementados e testados com todos os outcomes. Uma
reexportação real pelo novo endpoint é `NÃO DISPONÍVEL` porque não houve deploy,
como exigido pelo escopo. O report histórico original contém TP=`17` e SL=`14`
`[query]`; não se declara win rate incondicional sem executar a versão nova.

### E7 — invariantes

O dry-run do cohort produziu `READY=31` e `ERROR=0` `[query]`. A migration contém
o `CHECK` condicional e a compilação PostgreSQL confirmou constraint, unicidade e
FK `RESTRICT` `[query: SQLAlchemy CreateTable]`. A prova sobre a tabela completa
pós-migração é `NÃO DISPONÍVEL`, pois a migration não foi aplicada.

### E8 — escopo

Nenhum arquivo de Signal Engine, Score Engine, Block Engine ou Risk Engine foi
alterado. `pipeline_scan.py` foi tocado somente para transportar timestamps e o
envelope de preço; `shadow_trade_service.py` somente para congelar entrada e
elegibilidade; `shadow_trade_monitor.py` somente para revisões observacionais.

## Validação

- regressão focada: `258 passed` `[query]`;
- frontend Next/TypeScript: build concluído `[query]`;
- Alembic: um head, `203_shadow_trade_measurement_revisions` `[query]`;
- grafo: atualizado por `graphify update .` `[query]`;
- três falhas em `test_shadow_profile_attribution.py` também ocorrem sem este
  diff no `origin/main` (`3 failed`, `4 passed`) `[query: baseline]`.

O dry-run integral encontrou `307895` Shadows concluídos `[query: count-only]`.
A tentativa monolítica excedeu o timeout antes de concluir; portanto resultados
integrais não são reportados. O backfill entregue é idempotente e suporta lotes,
mas uma execução futura deve ser paginada/orquestrada no ambiente apropriado.

## Itens não executados

- Deploy Railway/Vercel: fora do escopo autorizado.
- Migration online: fora do escopo autorizado.
- Backfill com escrita: fora do escopo autorizado.
- Slippage realizado/IC/expectancy com fill: evidência inexistente.
- Backfill integral em uma única sessão: volume `307895` `[query]` excedeu a
  janela operacional; nenhuma escrita ocorreu.

## Ledger de Evidências

| Número reportado | Origem | Valor literal da fonte |
|---|---|---|
| base commit | `[query: git]` | `88009d54e9c7e30dcc130a613e92ae313ece2b8f` |
| sentinelas | `[query: pytest]` | `6 passed` |
| regressão focada | `[query: pytest]` | `258 passed in 8.19s` |
| SUI MFE legado/recalculado | `[query: dry-run]` | `0.0`; `0.5057053941908807` |
| SUI MAE legado/recalculado | `[query: dry-run]` | `-0.7261410788381807`; `-0.790975103734437` |
| cohort | `[query: dry-run]` | `selected:31; READY:31; TP_HIT:17; SL_HIT:14` |
| zeros TP MAE | `[query: dry-run]` | `legacy:14; corrected:4` |
| zeros SL MFE | `[query: dry-run]` | `legacy:11; corrected:2` |
| histórico concluído | `[query: count-only]` | `selected:307895` |
| head Alembic | `[query: alembic heads]` | `203_shadow_trade_measurement_revisions (head)` |
| baseline conhecido | `[query: pytest origin/main]` | `3 failed, 4 passed` |
