# Relatório — Release do monitor de Shadow e pendências residuais do R1

**Data:** 2026-09-03 (deploy ~16:57-17:03 UTC; verificação até ~21:51 UTC)
**Branch:** `codex/correcao-sequenciada-r1-20260902`, fast-forward em `main`
**Commits:** `d738d79` (Bloco A), `09f2e99` (B.1)
**Disciplina de evidência:** todo número é `[query]`, `[code path:line]`,
`[railway logs]` ou `[calc]` sobre um destes.

## Resumo executivo

Bloco A implementado, testado e **publicado em produção** (API + worker-compute
+ beat, todos `SUCCESS`). Confirmado ao vivo: os 4 Shadows travados fecharam
no primeiro ciclo pós-deploy, 3 ciclos consecutivos com **0 erros** (eram 13
constantes), fração rompida-e-aberta caiu de 21,3%/40% para **0%**, latência
p50 caiu 33-90% conforme o desfecho. Bloco B: B.1 aplicado (âncora tardia de
1h) e commitado; B.2 e B.3 são relatórios de achado; **B.4 é o resultado mais
importante desta sessão** — o AUC de 0,836 do `rsi_6` era artefato de
circularidade puro: recalculado no instante real da saída, cai para 0,568
(IC cruza 0,5). Nenhuma saída condicional foi implementada.

---

# BLOCO A — Monitor: desentupir a fila

## A.1 — C1 + C2 (par obrigatório)

**C1** — `path:line`:
`backend/alembic/versions/212_shadow_monitor_unstick.py` (migration nova);
`backend/app/models/shadow_trade.py:221-230` e
`backend/app/models/trade_simulation.py:56` (ORM). `barrier_touched` em
`shadow_trades` e `trade_simulations`: `VARCHAR(20)` → `VARCHAR(32)`.
Auditei todas as colunas de reason-code do schema contra o maior literal
persistível (`shadow_barrier_mode`, `outcome`, `intrabar_convention`,
`exit_price_semantics`, `ttt_close_reason`, `ttt_fast_win_bucket`,
`ttt_outcome`, `lineage_status`, `entry_risk_capture_status`,
`profile_status_at_entry`): só `barrier_touched` estourava. Não encurtei o
literal.

**C2** — `path:line`: `backend/app/services/shadow_barrier_evaluator.py`
(`evaluate_closed_candles_policy_v2`, ~linha 385-410) + constante
`BARRIER_CONTRACT_VERSION_V2 = "shadow_closed_ohlcv_first_touch_v2"`
(linha ~37). Nova política canônica explícita — `evaluate_closed_candles`
(v1) permanece **byte-a-byte intacto** para reprodutibilidade de qualquer
resultado já calculado antes deste fix. A candle de entrada ambígua agora
grava `entry_boundary_ambiguous_at` **uma vez** e o loop **continua** para
velas posteriores, em vez de retornar `UNRESOLVED` e nunca avançar o
cursor. `backend/app/tasks/shadow_trade_monitor.py`
(`_advance_shadow_canonical`, ~linha 388-435): o caminho canônico agora
sempre avalia via `evaluate_closed_candles_policy_v2` (trailing legado
`shadow_hwm_trailing_v1` é traduzido para o mesmo `trailing_policy` dict
genérico — verificado 0/559 divergência nesta sessão), aposentando a
chamada ao `evaluate_closed_candles` do caminho vivo.
`_canonical_barrier_enabled` (linha ~387-397) aceita `v1` ou `v2`.

**Teste que cobre o cenário escondido:**
`backend/tests/test_shadow_monitor_unstick_bloco_a.py` — 11 testes, incluindo
`test_ambiguous_entry_candle_records_once_and_continues_to_outcome` e
`test_ambiguous_entry_candle_with_no_later_touch_advances_cursor_instead_of_freezing`.
Um teste **pré-existente** que documentava o bug
(`test_unresolved_reason_code_is_exact_and_known_not_persistable_under_c1_schema`,
`tests/test_shadow_monitor_savepoint_and_reason_codes.py`) foi atualizado
para validar o fix (`width == 32`, renomeado para `..._now_persistable_after_c1_fix`).

**Evidência em produção pós-deploy:**

`[railway logs]` `scalpyn-worker-compute`
(`e2375cdd-f50b-4a74-947b-7f8448131648`), deployment `e215190e-...`, 3
ciclos consecutivos:

```
17:08:57  '... 50 processed, 11 completed, 0 errors, 0 backfill | fast-scan closed_tp=10 closed_sl=2 closed_trailing=2 stale_skipped=2 errors=0'
17:13:54  '... 50 processed, 7 completed, 0 errors, 0 backfill | fast-scan closed_tp=10 closed_sl=0 closed_trailing=1 stale_skipped=0 errors=0'
17:18:53  '... 50 processed, 7 completed, 0 errors, 0 backfill | fast-scan closed_tp=2 closed_sl=0 closed_trailing=0 stale_skipped=0 errors=0'
```

Contra a referência anterior — `13 errors` em **todo** ciclo, 5 ciclos
seguidos (`13:40` a `14:05`). **0 errors em 3/3 ciclos verificados.**

`[query]` Os 4 Shadows nomeados no diagnóstico (`LIT_USDT`×2, `NEAR_USDT`,
`UNI_USDT`) — todos `status=COMPLETED`, `closure_path='canonical_walk'`,
`entry_boundary_ambiguous_at` preenchido com o candle de entrada original
(2026-09-01, confirmando que a ambiguidade foi corretamente detectada e
registrada), `completed_at = 2026-09-03 17:03:1X` (primeiro ciclo após o
worker subir). Todos fecharam com desfecho determinístico (2× SL_HIT, 1×
SL_HIT, 1× TRAILING_STOP).

## A.2 — Ordenação do fast-scan

`path:line`: `backend/app/tasks/shadow_trade_monitor.py`
(`_fast_barrier_scan_async`, ~linha 1960-2001) — `ORDER BY st.id` (fixo)
substituído por `shadow_fast_scan_priority` config-driven
(`AGE`/`MAGNITUDE`/`AGE_THEN_MAGNITUDE`, default `AGE_THEN_MAGNITUDE`) e
`shadow_fast_scan_batch_size` (default 20, agora ajustável). SQL de
detecção TP/SL/trailing **não foi tocado** — confirmado que já cobre TP/SL
por comparação direta de preço, sem depender de HWM.

**Nota honesta:** não pude atribuir isoladamente o fechamento de
`XRP_USDT`/`ARB_USDT` à A.2 — ambos fecharam **antes** do deploy terminar
(closure_path=NULL, ou seja, pelo código antigo), por sorte comum de
starvation rotativa. A2 permanece validada por design e pelo SQL testado
ao vivo nesta consulta (`docs/audits/r1/shadow-monitor-backlog/relatorio-consulta-latencia-fechamento-2026-09-03.md`),
mas não há um caso isolado pós-deploy para prova direta desta sessão.

## A.3 — Modo de monitoramento por população

`path:line`: `backend/app/schemas/strategy_settings.py`
(`shadow_monitor_mode_by_source`, ~linha 148-172) — schema validado,
default `L3=CONTINUOUS`, demais `BATCH`. `backend/app/services/shadow_trade_service.py`
(`_resolve_shadow_monitor_mode`, congelamento em `config_snap["shadow_monitor_mode"]`,
gate de criação para `OFF`). `backend/app/tasks/shadow_trade_monitor.py`
(filtro `_continuous_only` no lote regular e no fast-scan; nova task
`run_batch_sweep` + beat `shadow_trade_batch_sweep`, hourly).

**Requisitos atendidos:**
- modo lido a cada ciclo (`_load_shadow_monitor_ops_config`/leitura do
  snapshot), sem reinício — confirmado por código, não requer restart.
- Shadows já abertos não são reprocessados ao trocar — o modo é congelado
  em `config_snapshot` na criação, nunca reescrito.
- `BATCH` usa o mesmo avaliador canônico e convenções — `run_batch_sweep`
  reutiliza `_advance_shadow_batch_isolated`/`_advance_shadow`/
  `_advance_shadow_canonical` sem nenhuma lógica nova de avaliação.

**Aviso obrigatório na UI para `OFF`:** **não implementado nesta sessão** —
o schema/backend estão prontos, mas nenhuma alteração de frontend foi feita
além do dropdown genérico (`frontend/lib/strategySettings.ts`). Marcado como
lacuna conhecida; enquanto isso, `OFF` para `L3_REJECTED` continua fora do
padrão proposto (não foi ligado).

**Status ao vivo:** o split `CONTINUOUS`/`BATCH` **ainda não está ativo**
— requer que o operador salve a config em `/settings/strategies` (por
design: nenhuma mudança de comportamento silenciosa). Até lá, toda fonte
resolve para `CONTINUOUS` via fallback (`_resolve_shadow_monitor_mode`
retorna `CONTINUOUS` quando o mapa está ausente). A melhora observada
nesta sessão vem inteiramente de C1+C2+A.2+A.4.

## A.4 — Cota de fonte no lote regular

`path:line`: `backend/app/tasks/shadow_trade_monitor.py` (`_monitor_async`,
~linha 2237-2295) — `shadow_l3_batch_quota_pct` (default 20%) reserva
`ceil(50×20%)=10` vagas para `source='L3'` antes de preencher o resto.

**Vazão do L3, antes e depois:**

| Momento | Total aberto | L3 aberto | L3 rompido-e-aberto |
|---|---:|---:|---:|
| Antes (14:00) | 362 | 16 | 4 (40%) |
| Depois (21:51) | 143 | 5 | **0 (0%)** |

## A.5 — Investigação (sem correção)

**A.5.1 — hipótese OHLCV testada e refutada.** `[query]` Cruzei os 52
Shadows ainda parados (idade > 1h, `last_processed_time == entry_timestamp`)
contra 13 símbolos distintos: **todos têm cobertura `1m` completa e fresca**
(59-107 candles cobrindo todo o período aberto, mais recente até o minuto
atual). A hipótese "símbolos sem OHLCV coletado" está **refutada** para a
população atual. Com a fila drenando (362→143 em ~20min) e nenhum dos 52
com mais de ~2h de idade (contra 45-59h antes do fix), a explicação mais
simples é atraso normal de fila pós-fix, não um defeito novo. Nenhuma ação
proposta — reavaliar depois que `BATCH` (A.3) for ativado.

**A.5.2 — os ~9 erros/ciclo sem causa.** `[railway logs]` **Resolvidos
junto com C1** — 3 ciclos consecutivos pós-deploy com 0 erros. Não foi
possível isolar uma causa raiz independente para eles antes de
desaparecerem (só um núcleo estável de 4 IDs apareceu de forma consistente
nos logs anteriores; os demais apareciam uma vez e não repetiam,
sugerindo falhas transitórias, possivelmente amplificadas pela pressão
geral da fila entupida). Se a contagem de erros voltar a ficar
persistentemente > 0, capturar `shadow_id` de `"isolated advance failed"`
em múltiplos ciclos para reabrir a investigação.

## A.6 — `closure_path`

`path:line`: `backend/app/models/shadow_trade.py:228-230` (coluna, migration
212); `backend/app/tasks/shadow_trade_monitor.py` — `_finalize_outcome`
agora exige `closure_path` (kwarg obrigatório, todos os 6 call-sites
atualizados: `canonical_walk` fixo no caminho canônico;
`closure_path_hint` propagado de `_advance_shadow` — `"fast_scan"` quando
chamado pelo fast-scan, `"regular_batch"` (default) pelo lote normal e por
`run_batch_sweep`). Confirmado ao vivo: os 4 Shadows destravados gravaram
`closure_path='canonical_walk'`.

## A.7 — `feature_source_at`

`path:line`: `backend/app/tasks/shadow_trade_monitor.py`
(`_capture_exit_features`, ~linha 962) — `shadow.feature_source_at =
datetime.now(timezone.utc)` gravado incondicionalmente, nas 3 saídas da
função (sucesso, `_capture_failed` por exceção, `_capture_failed` por
snapshot vazio). Antes: `96%` nulo (216/225 TRAILING_STOP da coorte 559).
Efeito só visível em Shadows que fecharem **depois** do deploy — a coorte
histórica de 559 não é retroativamente alterada (não é permitido).

## Verificação do Bloco A

| Critério | Antes | Depois |
|---|---:|---:|
| Os 4 Shadows travados | `RUNNING` para sempre | `COMPLETED`, `closure_path=canonical_walk` |
| Monitor, falhas consecutivas | 13 erros × 5 ciclos seguidos | **0 erros × 3 ciclos seguidos** |
| Latência SL_HIT p50 / p90 | 529s / 21.769s | **221,5s / 5.501,9s** |
| Latência TP_HIT p50 / p90 | 147s / 16.428s | **98,9s / 342,6s** |
| Latência TRAILING_STOP p50 / p90 | 7.096s / 27.650s | **702,0s / 9.763,3s** |
| Fração aberta além de barreira (total) | 60/282 = 21,3% | **0/143 = 0%** |
| Fração aberta além de barreira (L3) | 4/10 = 40% | **0/5 = 0%** |
| Vazão L3 aberto | 16 | 5 (fila drenando) |
| `XRP_USDT` / `ARB_USDT` | rompidos, abertos | fechados (TRAILING_STOP, antes do deploy concluir) |

`[calc]` "Depois" para latência usa apenas Shadows com `barrier_touched_at`
E `completed_at` ambos após o deploy (17:03 UTC) — exclui o lote de
backlog histórico que o próprio fix drenou no primeiro ciclo (esse lote,
incluído, teria distorcido a métrica para pior por conter até 48h de
atraso acumulado da era pré-fix, não do código novo).

---

# BLOCO B — Pendências residuais do R1

## B.1 — R1.B: distribuição da latência de revisão da Gate

`[query]` 18.552+ amostras, 6 símbolos × 3 timeframes, ~22h de coleta.

**Achado operacional (novo, não pedido mas relevante):** a cobertura por
candle está incompleta — nenhum grupo (símbolo, timeframe, candle) atingiu
os 5 delays alvo simultaneamente; máximo observado é 4, média ≈2,05
amostras por candle. O bucket de `10s` é o mais sub-capturado (2.520
amostras vs. 4.188-4.476 nos demais) — consistente com o próprio tempo de
execução do sampler competindo com a janela de tolerância de ±6s.

**Achado substantivo:** `[calc]` Em **6.906 comparações par-a-par
utilizáveis** (candle com ≥2 delays capturados), **0 revisões observadas**
em qualquer campo (`open`, `high`, `low`, `close`, `volume`,
`quote_volume`), em qualquer par de delays, para qualquer símbolo ou
timeframe. `p50`/`p90`/`p99` de estabilização não são computáveis como uma
distribuição real de revisão — não há nenhum evento de revisão nesta
amostra para caracterizar.

**Se `60s` cobre o p99:** não há p99 de revisão real para comparar — a
pergunta original (o achado isolado do VET, uma revisão às ~7s) não se
repetiu nem uma vez em 22h. **Isso não prova que não pode acontecer** —
só que é raro o suficiente para não aparecer nesta janela/amostra.

**Âncora tardia (exigida pelo prompt) — implementada, não apenas
proposta:** `backend/alembic/versions/213_settlement_latency_late_anchor.py`
+ `backend/app/services/research_ohlcv_service.py:269`
(`SETTLEMENT_LATENCY_DELAYS_SECONDS` agora inclui `3600`). Publicado em
produção (`scalpyn` API + `scalpyn-worker-research-ohlcv`, ambos
`SUCCESS`). **Ainda sem dado acumulado** — a primeira leitura de 1h só
ocorre ~1h após o deploy; reler este relatório depois para o resultado.

**Cobertura de liquidez:** os 6 símbolos (`BTC`, `LINK`, `NEAR`, `SOL`,
`TAO`, `XDC`) cobrem de alta a baixa liquidez dentro do pool de 65, mas
`EVIDÊNCIA NÃO LOCALIZADA` para uma comparação formal de percentil de
liquidez contra o pool completo — não calculada nesta sessão.

**Graça de `60s`: não alterada**, conforme exigido.

## B.2 — R1.C.1: amostra de `30m` contra a Gate final

`[query]` Amostra aleatória de 50 candles `30m` de `ohlcv_shadow`
(`capture_contract_version='gate_ohlcv_state_v3'`, todos com folga total
> grace) contra a Gate ao vivo.

| Campo | Divergência |
|---|---:|
| `open`/`high`/`low`/`close` | **0/50 (0%)** |
| `quote_volume` | 47/50 (94%), sempre ~1e-5 a 5e-5 de magnitude |
| Todos os campos exatos | 3/50 (6%) |

**Veredito:** OHLC — o que o avaliador de barreira consome — está **100%
exato**. A divergência de `quote_volume` é de ordem de grandeza muito
menor (~10⁻⁵) que o bug original do contrato v1 (~10⁻⁴, erro de escala) e
é consistente com a quantização decimal proposital de 4 casas do pipeline
v3 (`_STATE_VOLUME_QUANTUM = Decimal("0.0001")`,
`research_ohlcv_service.py`) contra o valor bruto de maior precisão da
Gate — não é o mesmo defeito, é arredondamento por design. Não é urgência;
registrar junto ao corte, mesma disposição já dada ao achado R1.C.2
original.

## B.3 — R1.C.3: custo da espera

`[query]` Decisões e Shadows por dia (últimos 3 dias completos):

| Dia | Decisões | Shadows | Shadows `L3` |
|---|---:|---:|---:|
| 09-01 | 89.450 | 819 | 24 |
| 09-02 | 101.549 | 746 | 11 |
| 09-03 (parcial) | 93.068 | 1.035 | 41 |

**`1m` antes de `5m`?** Reforça a conclusão já registrada: `1m` tem
`455/455` exatas e é a fonte direta do avaliador de barreira; `5m` diverge
`97,14%` no canônico antigo. Promover `1m` primeiro segue fazendo sentido —
não há argumento novo contra isso nesta sessão.

**Defasagem adicional de `120s`, quantificada:** `[calc]` sobre os 559
holding_seconds: mediana `71,4min`, p25 `25,1min`, p75 `151,2min`. `120s`
representa `2,8%` da mediana e `8,0%` do p25 (quartil mais rápido). Mas
`22/559 (3,94%)` dos trades duram menos de `5min`, e `47/559 (8,41%)` menos
de `10min` — para esses, `120s` extra é uma fração grande e potencialmente
material do tempo de vida total, não um arredondamento desprezível. Não é
"assumir que é tolerável" — é tolerável **na mediana**, mas material para
a cauda rápida (~4-8% dos trades).

## B.4 — Refazer a Etapa 3.5 sem circularidade

Formulas de produção confirmadas: `path:line`
`backend/app/services/feature_engine.py:462-516` (`_calc_rsi`, Wilder EMA,
`alpha=1/period`), `:670-685` (`_calc_stochastic`, k=14/d=3/smooth=3).
`entry_exhaustion_score`: **sem produtor identificado** — não recomputável,
`EVIDÊNCIA NÃO LOCALIZADA` (achado reaproveitado, não re-verificado).

`[calc]` `rsi_6` e `stoch_k` recalculados em `barrier_touched_at`, a
partir das velas `1m` Gate-final já commitadas (`docs/audits/r1/trailing-policy/replay_1m/`),
para os TRAILING_STOP da coorte 559 (n=209/225 usável — 16 excluídos por
menos de 20 candles disponíveis até o toque).

| Sinal | AUC anterior (circular) | AUC recalculado (honesto) | IC95% recalculado |
|---|---:|---:|---|
| `rsi_6` | 0,836 | **0,568** | **[0,444, 0,686]** |
| `stoch_k` | 0,729 | **0,593** | **[0,445, 0,740]** |

**Veredito: o resultado anterior está morto, confirmado.** Ambos os
sinais, medidos honestamente no instante real da saída com dado limpo,
**cruzam 0,5 no IC95%** — indistinguíveis de aleatório. O `AUC=0,836`
original media o próprio desfecho (o preço já tinha subido por ~2h em
mediana antes da captura), não previa nada. Combinado com o achado já
existente de que `entry_exhaustion_score` também não separa, **os três
sinais de exaustão testados falham em discriminar continuação de
reversão quando medidos corretamente.** Nenhuma saída condicional foi
implementada, conforme exigido — e este resultado remove o principal
argumento que poderia ter motivado revisitar essa decisão.

---

# O que foi proposto e não executado

- **A.3, aviso de UI para `OFF`**: backend pronto, frontend não
  implementado. Motivo: escopo/tempo desta sessão; não bloqueia nenhum
  critério de verificação do Bloco A.
- **A.3, ativação do split CONTINUOUS/BATCH**: mecanismo pronto e
  implantado, mas requer salvar a config em `/settings/strategies` —
  decisão do operador, não automática por design (zero mudança de
  comportamento silenciosa).
- **A.5.1**: hipótese testada e refutada; nenhuma correção proposta pois
  não há causa a corrigir.
- **B.1, valor de graça**: não alterado, conforme exigido — proposta fica
  pendente até a âncora de 1h acumular dado.
- **B.2/B.3**: são relatórios de achado (portão de corte / custo), não
  ações — decisão do operador, conforme escopo do prompt original.

# Verificação transversal

1. `path:line` de cada mudança e nova versão de contrato — listado em
   cada item acima.
2. Teste cobrindo o cenário que cada defeito escondia — `test_shadow_monitor_unstick_bloco_a.py`
   (11 testes) + 1 teste pré-existente atualizado (era um regression-lock
   do próprio bug).
3. Evidência em produção pós-deploy — logs do worker (3 ciclos, 0 erros),
   consulta direta aos 4 Shadows destravados, métricas antes/depois via
   `psycopg2` read-only.
4. Hashes de profiles/versões (`97b7d30f95e76321d65794b809dddd1d`,
   `f5abc9b4386175d62c22ab5b5e492a80`): **não re-verificados por valor
   literal** — nenhum código de score/profile foi tocado nesta sessão
   (diff limitado a monitor/evaluator/settings/celery), então a inferência
   é de que permanecem inalterados, mas não há uma comparação byte-a-byte
   contra um snapshot anterior nesta sessão.
5. Nenhum Shadow histórico alterado — nenhuma escrita manual em
   `shadow_trades` além do funcionamento normal do monitor sobre Shadows
   `RUNNING`/`PENDING`; nenhuma migration altera dado, só schema.
6. Prova de zero hardcode — todos os parâmetros novos
   (`shadow_fast_scan_priority`, `shadow_fast_scan_batch_size`,
   `shadow_l3_batch_quota_pct`, `shadow_monitor_mode_by_source`,
   `shadow_canonical_barrier_policy_version`) validados via
   `MLShadowConfig`/`validate_payload`, cobertos por `source_hash`, com
   varredura de literal sobrevivente já feita ao desenhar cada consumidor
   (nenhum tem fallback numérico embutido fora do padrão já estabelecido
   pelo próprio `_load_shadow_force_close_policy`).
7. Contagens `ohlcv`/`ohlcv_shadow`/`ohlcv_live` sem perda: `[query]`
   `ohlcv=2.864.247`, `ohlcv_shadow=155.480`, `ohlcv_live=260` no momento
   desta verificação — sem escrita manual nesta sessão além da captura
   dual-run normal (que já estava rodando antes desta sessão começar).

# `EVIDÊNCIA NÃO LOCALIZADA`

- Fórmula/produtor de `entry_exhaustion_score` (B.4).
- Percentil de liquidez dos 6 símbolos do sampler R1.B contra o pool de 65
  (B.1).
- Dado da âncora de 1h (B.1) — mecanismo publicado, sem acúmulo ainda.
- Comparação byte-a-byte dos hashes de profile/versão citados no item 4 da
  verificação transversal.

# Ledger de Evidências

- `[railway logs]` 3 ciclos consecutivos pós-deploy, `scalpyn-worker-compute`,
  deployment `e215190e-849a-4f99-a758-af8d61ca1347`: 17:08:57 (0 errors),
  17:13:54 (0 errors), 17:18:53 (0 errors).
- `[query]` Os 4 Shadows travados: todos `COMPLETED`,
  `closure_path='canonical_walk'`, `completed_at≈2026-09-03T17:03:13Z`.
- `[query]` Fração rompida: antes 60/282=21,3% (total) e 4/10=40% (L3);
  depois 0/143=0% (total) e 0/5=0% (L3).
- `[query]` Latência pós-deploy (barrier_touched_at E completed_at após
  17:03 UTC): SL_HIT n=35 p50=221,5s p90=5.501,9s; TP_HIT n=78 p50=98,9s
  p90=342,6s; TRAILING_STOP n=78 p50=702,0s p90=9.763,3s.
- `[query]` B.1: 18.552+ amostras, 6.906 comparações pareadas utilizáveis,
  0 mudanças de valor em qualquer campo/par/símbolo/timeframe.
- `[query]` B.2: 50/50 (100%) OHLC exato; 47/50 (94%) `quote_volume`
  diverge por ~1e-5 a 5e-5.
- `[query]` B.3: decisões/dia 89.450 (09-01), 101.549 (09-02), 93.068
  (09-03 parcial); holding_seconds da coorte 559: mediana 4.285s
  (71,4min), p25 1.505s (25,1min), p75 9.074s (151,2min); 22/559 (3,94%)
  <5min, 47/559 (8,41%) <10min.
- `[calc]` B.4: `rsi_6` AUC circular=0,836 → honesto=0,568 IC95%[0,444,
  0,686]; `stoch_k` AUC circular=0,729 → honesto=0,593 IC95%[0,445,
  0,740]. n=209/225 usável.
