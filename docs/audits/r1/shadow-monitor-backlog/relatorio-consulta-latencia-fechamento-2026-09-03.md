# Relatório — Por que 33% do livro aberto está com barreira rompida

**Data:** 2026-09-03 (consultas entre ~13:58 e ~14:10 UTC)
**Escopo:** somente leitura. Nenhuma escrita, migration, deploy, correção ou
fechamento manual foi executada. Nenhum Shadow foi alterado.
**Disciplina de evidência:** todo número é `[query]`, `[code path:line]` ou
`[calc]` sobre um destes dois.

## Resumo executivo

**Nem H1 nem H2 como formuladas explicam sozinhas o sintoma. Há três causas
reais e distintas, coexistindo:**

1. **H1 confirmada, mas não como "BARRIER_PATH_UNRESOLVED bloqueando a
   cabeça da fila"** — a contagem desse estado é **0** agora. O mecanismo
   real é mais simples e mais grave: o lote regular processa só as `50`
   linhas mais antigas entre **todas** as fontes (não só `L3`), e havia
   `362` Shadows abertos no início desta consulta. `43/50` das mais antigas
   nunca avançaram nem uma vez desde a criação (`last_processed_time ==
   entry_timestamp`), a mais antiga com `56,4h` de idade. `ZEC_USDT` e
   `WLD_USDT` (dois dos cinco do sintoma) **fecharam durante esta própria
   investigação**, horas depois de romper a barreira — confirmando que o
   mecanismo é atraso de fila, não travamento permanente, para esses dois.
2. **H2 é real, mas o defeito não é o hipotetizado.** O fast-scan **cobre
   TP e SL por comparação direta de preço, sem depender de HWM** — não é
   "fast-scan não detecta TP/SL simples". O defeito é: `LIMIT 20` combinado
   com `ORDER BY st.id` (ordem arbitrária de UUID, não por urgência/idade).
   Confirmei ao vivo que `XRP_USDT` e `ARB_USDT` estão **com ticker fresco
   (100-132s) e com preço atualmente além da barreira**, e mesmo assim não
   fecharam — porque a cada tick há tipicamente 17-20 candidatos elegíveis
   (perto do teto de 20), e a ordenação não garante que os mais antigos ou
   mais urgentes sejam priorizados.
3. **H3 — achado novo, fora das duas hipóteses do prompt: bug de schema.**
   `shadow_trades.barrier_touched` é `VARCHAR(20)`, mas o literal que o
   avaliador grava para candle de entrada ambígua,
   `'BARRIER_PATH_UNRESOLVED'`, tem **23 caracteres** — sempre cabe, exceto
   que `20 < 23`. Toda tentativa de persistir esse valor lança
   `asyncpg.exceptions.StringDataRightTruncationError`, que aborta o
   savepoint da linha inteira (nenhum campo daquele Shadow é salvo, nem
   `updated_at`). Confirmei **4 Shadows presos nesse loop exato, em 5 ticks
   consecutivos ao longo de 25+ minutos, sem exceção** (`LIT_USDT` ×2,
   `NEAR_USDT`, `UNI_USDT`; fontes `L3_SIMULATED`/`L3_REJECTED`, não `L3`).
   Esses Shadows **nunca vão fechar sozinhos** — e como ocupam permanentemente
   até `4` das `50` vagas do lote a cada tick, para sempre, degradam a
   capacidade efetiva da fila para todo mundo, incluindo `L3`.

**Nenhum dos cinco Shadows nomeados no sintoma está preso pelo H3** — são
todos vítimas de H1 (fila) e, no caso de `XRP`/`ARB`, também de H2
(starvation do fast-scan).

---

## Q1 — Estado dos cinco Shadows

Os horários do sintoma estavam em hora local (`UTC-3`); localizei os cinco
por `source='L3'` e `entry_timestamp` em UTC = local+3h.

| Symbol | id | status agora | outcome | barrier_touched_at | last_processed_time == entry? |
|---|---|---|---|---|---|
| BNB_USDT | `3f8911b6-...` | **COMPLETED** | TRAILING_STOP | 2026-09-03 08:49:00 | — (já fechado) |
| ZEC_USDT | `77684235-...` | **COMPLETED** (fechou durante esta consulta) | TP_HIT | 2026-09-03 11:29:00 | — |
| WLD_USDT | `47ba9583-...` | **COMPLETED** (fechou durante esta consulta) | TP_HIT | 2026-09-03 08:07:00 | — |
| XRP_USDT | `cdc458f7-...` | RUNNING | null | **null** | **Sim** |
| ARB_USDT | `876c648d-...` | RUNNING | null | **null** | **Sim** |

**Veredito Q1:** para os 2 ainda abertos (XRP, ARB), `barrier_touched_at`
está **NULO** — o toque real (ticker cruzando TP/SL agora mesmo, confirmado
em Q4) **nunca foi detectado/persistido**, não é caso de "detectado mas não
fechado". Para BNB/ZEC/WLD, `barrier_touched_at` estava preenchido horas
antes de `completed_at` — a detecção aconteceu, e o que faltou foi
processamento a tempo (atraso de fila).

## Q2 — O monitor está alcançando esses Shadows?

`[query]` No início desta consulta: `362` Shadows abertos, por fonte —
`L1_SPECTRUM=21, L3=16, L3_LAB=178, L3_REJECTED=20, L3_SIMULATED=151` (caiu
para `282` no fim da investigação, ~12 min depois — a fila está drenando
ativamente, não parada).

Para `XRP_USDT` e `ARB_USDT`, `updated_at` está parado desde `07:40:45` e
`10:00:45` respectivamente (`6,3h` e `3,9h` atrás no momento da checagem) —
e `max_price_post_entry`/`min_price_post_entry`/`mfe_at`/`mae_at` estão
**todos nulos**, apesar do código do live-close atualizar esses campos
independentemente de fechar ou não (`shadow_trade_monitor.py:1411-1428`).
Isso é evidência, independente de `last_processed_time`, de que a linha
nunca foi tocada de novo desde a inicialização logo após a criação.

Posição na fila (Shadows abertos com `created_at` mais antigo, no momento
da checagem):

| Symbol | linhas à frente | dentro do lote de 50? |
|---|---:|---|
| ZEC_USDT | 41 | Sim (mas só fechou após rodar por horas) |
| WLD_USDT | 47 | Sim |
| XRP_USDT | 99 | **Não** |
| ARB_USDT | 188 | **Não** |

`[query]` Lote regular seleciona globalmente
(`ORDER BY created_at ASC, id ASC LIMIT 50`, `shadow_trade_monitor.py:2151-2156`)
— **sem filtro por fonte**. Fast-scan roda antes do lote a cada tick,
`LIMIT 20` (`shadow_trade_monitor.py:1904`, constante `SHADOW_FAST_SCAN_BATCH_SIZE`).

## Q3 — Peso de H1: os travados bloqueiam a cabeça da fila?

`[query]` Contagem atual de `barrier_touched='BARRIER_PATH_UNRESOLVED'`
entre abertos: **0** — não `7` como na referência do prompt. A população é
dinâmica; a foto de "7" já não existe.

`[query]` Dos 50 Shadows mais antigos abertos: **43/50 (86%) nunca
avançaram** (`last_processed_time == entry_timestamp`), incluindo
`UNI_USDT` criado `2026-09-01 05:42` (`56,2h` de idade). Isso é o achado
principal de H1: não são poucas linhas travadas — é a **maioria** das mais
antigas que nunca progride.

`[query]` Dentro desses 43, **4 confirmados presos permanentemente por H3**
(ver abaixo) — `~9,3%` do lote de 50, mas **100% permanentes** (nunca vão
sair sozinhos). Os outros `~39` parecem estar em atraso de fila comum (H1
puro) — `ZEC`/`WLD` (que estavam nessa faixa) se resolveram sozinhos durante
esta consulta, confirmando que ao menos parte desse grupo não está
travada, só lenta.

## Q4 — Peso de H2: o fast-scan funciona para TP e SL simples?

`_fast_barrier_scan_async`, `backend/app/tasks/shadow_trade_monitor.py:1844`.
SQL cru (`:1877-1905`):

```sql
SELECT st.id
FROM shadow_trades st
JOIN market_metadata mm ON mm.symbol = st.symbol
WHERE st.status IN ('RUNNING', 'PENDING')
  AND st.tp_price IS NOT NULL
  AND st.sl_price IS NOT NULL
  AND (
    mm.price <= st.sl_price
    OR mm.price >= st.tp_price
    OR ( -- ramo trailing, único que depende de HWM
      st.config_snapshot #>> '{trailing,enabled}' = 'true'
      AND st.config_snapshot #>> '{trailing,contract_version}' = :trailing_contract_version
      AND st.max_price_post_entry IS NOT NULL
      AND jsonb_typeof(st.config_snapshot #> '{trailing,activation_profit_pct}') = 'number'
      AND jsonb_typeof(st.config_snapshot #> '{trailing,hwm_trail_pct}') = 'number'
      AND st.max_price_post_entry >= st.entry_price * (1 + (...)/100)
      AND mm.price <= st.max_price_post_entry * (1 - (...)/100)
    )
  )
  AND (mm.last_updated IS NULL OR mm.last_updated >= :stale_cutoff)
ORDER BY st.id
LIMIT :fast_scan_batch_size   -- 20 (SHADOW_FAST_SCAN_BATCH_SIZE)
```

**TP e SL são comparação direta de preço — não dependem de HWM.** Só o
terceiro ramo (trailing) depende de `max_price_post_entry`. Isso **refuta**
a premissa literal de H2 no prompt ("fast-scan deveria detectar TP/SL por
comparação simples e não está alcançando"): ele alcança e detecta
corretamente.

**Por que XRP/ARB não fecharam, então:**

`[query]` Verifiquei ao vivo (`14:07-14:09 UTC`): `market_metadata.price`
fresco (`100-132s`, bem dentro do `stale_cutoff` padrão de `300s`), e as
duas condições batem:

| Symbol | price | tp_price | sl_price | above_tp / below_sl | staleness |
|---|---:|---:|---:|---|---:|
| XRP_USDT | 1,39280 | 1,389738 | 1,348662 | **above_tp=True** | 100s |
| ARB_USDT | 0,13182 | 0,14101 | 0,13576 | **below_sl=True** | 132s |

Ambas as linhas **satisfazem o WHERE do fast-scan agora mesmo** e continuam
`RUNNING`. A causa: **`LIMIT 20` + `ORDER BY st.id`** (ordem arbitrária de
UUID, não por idade nem por urgência). Nos logs do worker (`scalpyn-worker-compute`,
últimas ~30min): fechamentos por fast-scan de `15-17` por tick, perto do
teto de `20` —
`"fast-scan closed_tp=9 closed_sl=1 closed_trailing=7"` (13:50),
`"closed_tp=9 closed_sl=2 closed_trailing=6"` (13:55),
`"closed_tp=10 closed_sl=2 closed_trailing=5"` (14:00). Quando o número de
candidatos elegíveis por tick se aproxima ou passa de 20, a ordenação por
`id` não garante cobertura — algumas linhas legitimamente rompidas ficam de
fora por várias rodadas seguidas, por azar de UUID, sem nenhum mecanismo de
rotação ou prioridade por idade/urgência.

**Logs do worker (últimas ~1h, `scalpyn-worker-compute`,
`91d4b8f1`→ na verdade `e2375cdd-f50b-4a74-947b-7f8448131648`):**
`shadow_trade_monitor.run` roda a cada `300s` exatamente como agendado — não
há falha de agendamento. Toda execução recente:

```
13:40:07 succeeded in 11.0s: 'Shadow monitor: 50 processed, 8 completed, 13 errors, 0 backfill | fast-scan closed_tp=0 closed_sl=0 closed_trailing=0 stale_skipped=125 errors=0'
13:45:08 succeeded in 12.1s: '... 50 processed, 12 completed, 13 errors ... closed_tp=6 closed_sl=2 closed_trailing=9 stale_skipped=0 errors=1'
13:50:08 succeeded in 12.0s: '... 50 processed, 12 completed, 13 errors ... closed_tp=9 closed_sl=1 closed_trailing=7 stale_skipped=0 errors=1'
13:55:11 succeeded in 15.1s: '... 50 processed, 30 completed, 13 errors ... closed_tp=9 closed_sl=2 closed_trailing=6 stale_skipped=0 errors=1'
14:00:15 succeeded in 19.3s: '... 50 processed, 30 completed, 13 errors ... closed_tp=10 closed_sl=2 closed_trailing=5 stale_skipped=0 errors=1'
```

**"13 errors" é constante em todo tick verificado.** Dentro desses 13, isolei
via `grep "isolated advance failed"` um núcleo **estável de 4 shadow_ids**
que aparecem em **todos** os 5 ticks consecutivos, sempre com a mesma
exceção:

```
sqlalchemy.exc.DBAPIError: (...) asyncpg.exceptions.StringDataRightTruncationError:
value too long for type character varying(20)
[SQL: UPDATE shadow_trades SET updated_at=now(), min_price_post_entry=$1, max_price_post_entry=$2,
 mae_at=$3, mfe_at=$4, barrier_touched=$5, barrier_touched_at=$6 WHERE shadow_trades.id = $7]
[parameters: (..., 'BARRIER_PATH_UNRESOLVED', ..., UUID('2f24d621-...'))]
```

`[query]` `information_schema.columns`: `shadow_trades.barrier_touched` é
`character varying(20)`. `len('BARRIER_PATH_UNRESOLVED') == 23`. **`20 < 23`
— confirmado.** Este literal é definido em
`app/services/shadow_barrier_evaluator.py` (`"barrier_touched":
"BARRIER_PATH_UNRESOLVED"`, ver a constante do reason_code homônimo) e nunca
cabe na coluna.

Os 4 confirmados: `LIT_USDT` (2×, `L3_SIMULATED`), `NEAR_USDT`
(`L3_SIMULATED`), `UNI_USDT` (`L3_REJECTED`) — idades `45,35h`, `45,37h`,
`45,35h`, `56,43h`. Os outros `~9` erros/tick restantes na contagem "13
errors" **não foram individualmente diagnosticados** — `EVIDÊNCIA NÃO
LOCALIZADA` para a causa exata deles (alguns apareceram uma única vez e não
repetiram, sugerindo causas transitórias diferentes, não o mesmo bug de
schema).

## Q5 — Latência por tipo de desfecho, refinada

`[query]` Não existe campo em `shadow_trades` que identifique explicitamente
o caminho de detecção (fast-scan vs. lote regular vs. live-close). O que
mais se aproxima —
`exit_price_semantics`/`intrabar_convention`/`barrier_contract_version` —
descreve a *semântica do preço*, não o *processo* que gravou. **`EVIDÊNCIA
NÃO LOCALIZADA`** para quebrar por caminho de detecção. Instrumentação
necessária: gravar, no momento do fechamento, um marcador tipo
`closure_path IN ('fast_scan','regular_batch','canonical_walk')` — nenhuma
das três funções (`_fast_barrier_scan_async`, `_advance_shadow`,
`_advance_shadow_canonical`) grava esse dado hoje.

| Caminho | Outcome | n | p50 | p90 | p99 | máx |
|---|---|---:|---:|---:|---:|---:|
| **não separável** | SL_HIT | 680 | 529s | 21.769s | — | 40.848s |
| **não separável** | TP_HIT | 280 | 147s | 16.428s | — | 44.989s |
| **não separável** | TRAILING_STOP | 638 | 7.096s | 27.650s | — | 49.069s |

(mesmos números da Etapa 5 do relatório de política de trailing — n=1.598,
últimas 48h, sem quebra por caminho por falta do campo.)

**`33%` é típico, não foi fotografia ruim.** `[query]` Verificação ao vivo:
`60/282` (`21,3%`) do total de Shadows abertos, **e `4/10` (`40%`)**
especificamente do livro `L3` (o que a UI mostra), estão com preço atual
além de uma barreira agora — pior proporção que o `33%` original, não
melhor. A fila caiu de `362→282` abertos durante a investigação (drenando),
mas a fração rompida-e-não-fechada permanece alta.

## Q6 — Impacto sobre a Etapa 3.5

`_capture_exit_features` é chamado em
`backend/app/tasks/shadow_trade_monitor.py:1126`, dentro de uma transação
**separada e posterior** (`TX1`) que só roda depois que o Shadow já foi
marcado `COMPLETED` na transação principal — ou seja, no ciclo do monitor
que **fecha** o trade, não no candle onde a barreira foi tocada. A função
delega para `exit_metrics.build_exit_snapshot(db, symbol)`
(`app/services/exit_metrics.py:167`), que lê o **estado de indicadores
corrente no momento da chamada** — não um snapshot histórico point-in-time
no instante do toque.

`[query]` `feature_source_at`: **216/225 (96%) nulo** entre os TRAILING_STOP
da coorte de 559 (não 100%, mas quase universal — 9 exceções existem e não
foram investigadas).

`[query]` Distribuição de `completed_at − barrier_touched_at` para os 225
TRAILING_STOP da coorte de 559 (proxy direto do atraso entre a saída real e
o instante de captura, já que a captura roda colada em `completed_at`):

| Métrica | Valor |
|---|---:|
| n | 225 |
| mínimo | 248s (4,1min) |
| p50 | **7.368s (122,8min ≈ 2h)** |
| p90 | 37.549s (10,4h) |
| p99 | 49.069s (13,6h) |
| máximo | 49.069s (13,6h) |
| média | 13.576s (3,77h) |
| n com atraso < 60s | **0 / 225** |
| n com atraso > 1h | 156 / 225 (69,3%) |

**Veredito Q6: circularidade confirmada, não é hipótese.** Nenhum dos 225
teve `rsi_6`/`entry_exhaustion_score`/`stoch_k` capturado a menos de 4
minutos da saída real; a mediana é ~2 horas depois, e 69% passam de 1 hora.
Como a variável de classificação da Etapa 3.5 ("preço continuou depois da
saída, medido dentro do horizonte de 1440min") e o `rsi_6` "na saída" **estão
sendo medidos, na prática, a partir de praticamente o mesmo instante tardio
em mais da metade dos casos**, o AUC de `0,836` relatado para `rsi_6`
**muito provavelmente mede o próprio desfecho** (se o preço já subiu por 2h,
o RSI medido 2h depois reflete essa subida), **não prediz nada a partir do
instante real da saída**. Isso não invalida o achado de que
`entry_exhaustion_score` não separa (esse é o resultado mais fraco, então
não seria inflado por circularidade na mesma direção), mas **derruba a
confiança no resultado de `rsi_6`/`stoch_k` como sinal preditivo utilizável**
tal como medido.

---

## Conclusão

**As três causas coexistem, com pesos diferentes:**

- **H1 (fila) é a causa dominante e estrutural**, explicando a maior parte
  do atraso observado, incluindo os casos que se resolveram sozinhos
  (`BNB`, `ZEC`, `WLD`) — 362 Shadows abertos, lote de 50 a cada 5min,
  43/50 das mais antigas paradas.
- **H2 é real, mas por um motivo diferente do hipotetizado**: fast-scan
  cobre TP/SL sem depender de HWM (a premissa do prompt de que "fast-scan
  deveria fechar TP/SL simples" está certa), mas seu teto de 20 com
  ordenação arbitrária por `id` causa starvation quando os candidatos
  elegíveis por tick se aproximam ou passam de 20 — confirmado ao vivo em
  `XRP`/`ARB`.
- **H3 é um achado novo, não coberto pelas duas hipóteses do prompt**: bug
  de schema (`VARCHAR(20)` vs. literal de 23 caracteres) prende
  permanentemente ao menos 4 Shadows (fontes lab/rejected, não o livro `L3`
  oficial), e essas linhas mortas **permanentemente** ocupam vagas no lote
  de 50, piorando H1 para todo mundo.

**Sobre C1/C2 do prompt original ("se for H1, resolvem os três problemas de
uma vez"):** não é uma decisão binária. C1/C2 devem mitigar boa parte de H1,
mas **não corrigem H2** (starvation do fast-scan por ordenação/teto) **nem
H3** (as 4 linhas travadas continuariam travadas para sempre, mesmo com fila
maior/mais rápida, porque o bug é na gravação, não na fila). Proposta sem
executar: (a) aumentar `VARCHAR(20)` para acomodar `BARRIER_PATH_UNRESOLVED`
(23 chars) — corrige H3 definitivamente; (b) mudar a ordenação do fast-scan
de `ORDER BY st.id` para algo que priorize idade ou magnitude do rompimento,
e/ou subir o teto de 20 — mitiga H2; (c) aumentar `SHADOW_MONITOR_BATCH_SIZE`
e/ou reduzir `SHADOW_MONITOR_INTERVAL_S` — mitiga H1. Nenhuma foi aplicada
nesta sessão.

## `EVIDÊNCIA NÃO LOCALIZADA`

- Campo que identifique caminho de detecção (fast-scan/lote/canonical) por
  Shadow fechado — não existe hoje; Q5 fica sem quebra por caminho.
- Causa exata dos ~9 erros/tick fora dos 4 confirmados por `StringDataRightTruncationError`.
- Por que exatamente 9/225 TRAILING_STOP têm `feature_source_at` não-nulo
  (não investigado — população pequena, possivelmente uma via de captura
  diferente).

## Ledger de Evidências

- `[query]` 362→282 Shadows abertos ao longo da investigação; por fonte no
  início: L1_SPECTRUM=21, L3=16, L3_LAB=178, L3_REJECTED=20, L3_SIMULATED=151.
- `[query]` Os 5 nomeados identificados por `source='L3'` + `entry_timestamp`
  em UTC = horário local do prompt + 3h; tabela de status na Q1.
- `[query]` 43/50 Shadows mais antigos nunca avançaram desde a criação
  (`last_processed_time == entry_timestamp`), mais antiga `UNI_USDT`
  criada `2026-09-01 05:42:58` (56,2h).
- `[query]` `BARRIER_PATH_UNRESOLVED` count atual entre abertos: 0.
- `[query]` `information_schema.columns`: `barrier_touched` = `varchar(20)`;
  `len('BARRIER_PATH_UNRESOLVED') == 23`.
- `[code]` `_fast_barrier_scan_async`:
  `backend/app/tasks/shadow_trade_monitor.py:1844-1905`; SQL citada
  verbatim acima.
- `[query]` XRP/ARB: preço fresco (100-132s), acima do TP / abaixo do SL
  agora, ainda `RUNNING` — consulta direta em `market_metadata` + `shadow_trades`.
- `[railway logs]` `scalpyn-worker-compute`
  (`e2375cdd-f50b-4a74-947b-7f8448131648`), deployment
  `e16f0efe-bbaf-42c1-97f6-d0f42c2cb8a2`: 5 execuções consecutivas de
  `shadow_trade_monitor.run` entre 13:39 e 14:05 UTC, todas com "13 errors",
  4 `shadow_id`s repetindo em todas com `StringDataRightTruncationError`.
- `[query]` Distribuição `completed_at - barrier_touched_at` para os 225
  TRAILING_STOP da coorte 559: p50=7.368s, p90=37.549s, p99=49.069s,
  máx=49.069s, min=248s, 0/225 abaixo de 60s.
- `[query]` `feature_source_at` não-nulo: 9/225.
- `[query]` Agora: 60/282 (21,3%) de todos os abertos, e 4/10 (40%) do
  livro `L3`, estão além de uma barreira.
- `[code]` `_capture_exit_features`:
  `backend/app/tasks/shadow_trade_monitor.py:926` (definição),
  `:1126` (chamada, dentro de TX1 pós-COMPLETED);
  `build_exit_snapshot`: `backend/app/services/exit_metrics.py:167`.
