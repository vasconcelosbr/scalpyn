# Pump Score v1 — radar de tendências estáveis (10–15 min)

Status: `HYPOTHESIS_NOT_VALIDATED`. Observação apenas. Roda em paralelo com o v0 a cada ciclo;
`engines.active` escolhe qual engine aparece na coluna **Pump Score** e conduz o sync REALTIME.

## Objetivo

Indicar ativos **subindo de fato**, em tendência limpa de 10–15 min, e excluir micro pumps.
Um ativo em queda, esticado, sem estrutura ou caro de executar **não pontua** (célula nula, nunca zero).

## Camadas (código: `backend/app/services/pump_score_v1.py`)

| Camada | O que faz | Config |
|---|---|---|
| 1. Estrutura | Candles **5m fechados** (Gate preferida): ATR(14) 5m, progresso 15 min, eficiência 30/60 min, % candles verdes, fundos ascendentes, concentração, pavio, RVOL 5m, pico de volume, compressão prévia | `score_v1.structure` |
| 2. Regime | Progresso 5m do BTC + amplitude (% do universo com progresso > 0) → favorável / neutro / desfavorável. Força relativa `rs = (ret − β·ret_btc) / ATR%`, β = 1 na v1 | `score_v1.regime` |
| 3. Portões (AND) | progresso 5m, fluxo 1m (janela 15 min não vendedora + CVD slope), acima do VWAP móvel de 60 min, < 6 ATR(5m) dele, sem pavio, eficiência, não concentrado, sem pico de volume, slippage, profundidade ask 1 %, regime/RS. Entrada ausente = reprova | `score_v1.gates` |
| 4. Força | Só para quem passou: média **geométrica** de fluxo, preço (progresso + RS), qualidade da tendência e participação; penalidades de extensão e custo. Fatores normalizados pelo percentil do próprio ativo (média/variância exponenciais) após aquecimento; antes disso, lo/hi absolutos | `score_v1.factors`, `normalization`, `blocks`, `penalties` |
| 5. Estabilidade | EMA do score, passo uma vez por minuto fechado. Entrada após `enter_cycles` acima de `enter_score`; saída lenta após `exit_cycles` abaixo de `stay_score` **e** `min_hold_minutes`; saída imediata por queda, esticamento, falta de dados ou queda rápida no 1m; cooldown | `score_v1.stability` |

Condições instantâneas: `subindo`, `neutro`, `absorcao` (compra absorvida sem avanço — saída lenta),
`caindo`, `esticado`, `sem_dados` (as três últimas encerram o sinal na hora).
Estados exibidos: `candidato`, `ativo`, `enfraquecendo`; `fora` mostra célula nula.

## O que não muda

- Layout da tela: a coluna Pump Score mostra o engine ativo; o tooltip mostra o ledger do v1.
- Contrato v0: `pump_monitor_score`, `score_components` e exaustão no topo da linha continuam v0
  (dataset de pesquisa, continuidade e ML). As células `pump_score_v0`, `pump_score_v1` e `v1_*`
  vão para o dataset de pesquisa automaticamente.
- Hash do produtor (`_meta.producer_config_hash`) ignora `engines` e `score_v1`: ativar o v1 não
  divide coortes do Pump ML.

## Ativar e reverter

```http
PUT /api/pump-monitor/config   {"engines": {"active": "v1"}}   # ativa
PUT /api/pump-monitor/config   {"engines": {"active": "v0"}}   # rollback, vale no próximo ciclo
```

O v1 aquece desde o deploy (estatísticas por ativo e estado no Redis), então a troca não entra "fria".

## Critério de rollback (definir antes de ativar)

Comparar v0 e v1 nos **mesmos instantes** (ambos gravados no dataset de pesquisa):
taxa de toque em +0,6 % em 10/15 min do topo de cada ranking vs. média do slot, rotatividade
(trocas/hora, tempo médio na lista) e sinais por dia. Inferência por bootstrap por dia.

## Lacunas conhecidas

- Limiares são hipóteses iniciais; nenhuma validação estatística foi feita.
- Pesos e β = 1 devem ser revistos com dados (ablação por portão e bloco).
- Regime depende de candles 5m do BTC; sem eles o portão de regime reprova (fail-closed).

## Calibração v1.1 (2026-10-05, após a primeira hora em produção)

Observado em produção com o v1 ativo: nenhum ativo listado, mesmo com regime favorável.

| Problema observado | Ajuste |
|---|---|
| Distância medida contra o VWAP **diário** em ATR(5m): ativos em recuperação ficavam "abaixo do VWAP" e tendências de horas ficavam "esticadas" | VWAP **móvel de 60 min** dos candles 5m fechados (`structure.vwap_candles`); limite 6 ATR(5m), penalidade de 3 a 6 |
| RVOL do último candle muito baixo no universo inteiro zerava o bloco de participação | Participação = média dos 3 últimos candles vs base de 20 (`rvol_recent_candles`), lo/hi 0,3–1,5; piso de bloco 0,05 (`block_floor`) |
| Fluxo de janela levemente negativo (ex.: −0,03) reprovava uma escada perfeita | Portão de fluxo exige "não vendedor": `window_delta_norm > −0,1`; CVD slope continua > 0 |
| EMA ancorada em 0 enquanto o ativo estava fora: entrar levava ~9+ min | Fora da lista e sem passar nos portões, a EMA zera a memória; a entrada parte do próprio score |
| Limiar de entrada alto para normalização absoluta | `enter_score` 50, `stay_score` 35 |

Os valores antigos estão gravados na config de produção (versão 28) e precisam ser atualizados por `PUT /api/pump-monitor/config` com o bloco `score_v1` após o deploy.

## Calibração v1.2 (2026-10-05, 15:35 BRT)

| Problema observado em produção | Ajuste |
|---|---|
| Concentração = maior movimento / movimento **líquido**: em caminhos ruidosos explodia (mediana 1,07; 46 de 51 reprovados) | Concentração = maior candle de alta / soma das altas nos últimos 30 min (limitada a 0–1) |
| Participação baixa no universo inteiro (RVOL médio de 3 candles ~0,3) ainda derrubava ativos em tendência limpa (ZEC: preço 0,98, qualidade 0,71, score 39,9) | Bloco de participação com peso 0,25 e lo 0; tendência estável com volume comum não é vetada |

## Calibração v1.3 (2026-10-05, 15:45 BRT)

| Problema observado em produção | Ajuste |
|---|---|
| BTC e XRP reprovados por "profundidade ask 1 % ausente": nos books mais profundos os 100 níveis não chegam a 1 % do mid e a banda fica nula | O portão de profundidade aceita o slippage de compra medido para o nocional de referência; sem book continua reprovando |
| Concentração 0,5 rígida para 6 candles com 3–4 altas | Limite 0,6 |

Após isto, sem novos ajustes de limiar até haver dados acumulados (comparação v0 × v1 nos mesmos instantes).

## v1.4 — Regime de capital (2026-10-07)

Maré de capital do universo inteiro, medida pelo fluxo taker em USDT dos buckets de 1m já coletados da Gate (sem fornecedor externo). Não pontua ativo nenhum: mexe na barreira de entrada e pode limitar o regime de preço.

| Item | Definição | Config (`score_v1.capital_flow`) |
|---|---|---|
| Janela | Últimos 15 min fechados, só pares `_USDT`, só buckets completos | `window_minutes`, `quote_suffix`, `exclude_symbols`, `min_coverage` (0,8) |
| Medida | `ratio = (compra − venda) / (compra + venda)` em USDT | — |
| Normalização | z-score do ratio contra média/variância exponenciais (meia-vida 1 dia) após 240 minutos; antes disso, limiares absolutos do ratio | `halflife_minutes`, `min_observations`, `z_levels`, `ratio_levels` |
| Níveis | forte_entrada · entrada · neutro · saida · forte_saida; `desconhecido` sem cobertura (sem efeito) | — |
| Efeito 1 | `enter_score` += delta (−5, −2, 0, +5, +10), limitado a [stay_score, 100] | `enter_score_delta` |
| Efeito 2 | Teto do regime: forte_saida → no máximo desfavorável; saida → no máximo neutro. Nunca melhora o regime | `regime_cap` |
| Histórico | Tabela `pump_capital_flow_1m` (um registro por minuto), retenção 30 dias; endpoint `GET /api/pump-monitor/capital-flow/history` com horas de maior entrada/saída e média por hora do dia | `history_retention_days` |

Tela: indicador "Capital 15m" no cabeçalho do monitor clássico, com o efeito no v1 (regime e barreira de entrada) e o painel de histórico.

Limiares e deltas são hipóteses iniciais. Validar com o histórico gravado: taxa de toque em +0,6 % em 10/15 min dos ativos listados, separada por nível de capital.

## v1.5 — Spot × perpétuo e seta de direção (2026-10-07)

Contexto do perpétuo USDT da Gate (`GET /futures/usdt/contract_stats`, 5m), consultado uma vez por intervalo fechado por ativo (cache em Redis). Cobertura no universo em 07/10: 46 de 50 ativos (sem perpétuo: HTX, LEO, RAIN, RLUSD).

| Métrica | Definição | Config (`score_v1.derivatives`) |
|---|---|---|
| Fluxo do perp | `(long_taker − short_taker) / soma` nos últimos 3 intervalos (15 min) | `window_intervals` |
| OI | variação % de `open_interest_usd` na janela | — |
| Funding | `last_funding_rate` | — |
| Squeeze | liquidação de vendidos na janela / OI, em bps | — |

Sinais de alta frágil (só avaliados com progresso > 0): `perp_led` (perp ≥ spot + 0,15 com spot ≤ 0,05), `funding_hot` (≥ 0,05 %), `short_squeeze` (≥ 10 bps com OI sem subir), `oi_unwinding` (OI ≤ −1 %). Com `block_flags_min` (1) ou mais sinais, o portão `derivatives_healthy` reprova: o ativo não entra e, se listado, enfraquece pela histerese. Sem perpétuo, dado velho ou falha de coleta → o portão passa (`missing_policy: pass`).

Seta de direção (`score_v1.direction`), só exibição: ▲ quando progresso 5m ≥ 0,25 ATR com fluxo spot e CVD positivos; ▼ no espelho. "Forte" quando o perp confirma (alta: OI subindo, fluxo do perp ≥ 0, sem fragilidade; baixa: OI subindo com fluxo do perp vendedor). Não é ordem: o spot não vende a descoberto.

## v1.6 — Pump ML (XGBoost) ligado ao v1 (2026-10-07)

**Objetivo do modelo:** direção — P(preço termina acima da referência no horizonte), sem alvo fixo de %. Treinado por horizonte (`score_v1.ml.training.horizons_minutes`, padrão 10 e 15 min); o v1 usa o de `score_v1.ml.horizon_minutes` (15).

**Treino:** task Celery `app.tasks.pump_monitor.train_ml_daily` (fila `pump_monitor`, 05:40 UTC, limite 900 s). Mesmo ledger (`pump_ml_job_runs`), lock e regra de um treino por dia UTC do antigo serviço Railway `scalpyn-pump-ml`, que passa a registrar `daily_already_recorded` e pode ser desligado. Correções do diagnóstico de 07/10:

| Falha em produção | Causa | Correção |
|---|---|---|
| 06/10 `CheckViolationError` | artefatos em `pump_directional/…`; tabelas exigem `pump_ml/%` | namespace `pump_ml/directional/…` |
| 03/10 e 07/10 `QueryCanceledError` | cursores `ORDER BY decision_at` sem índice, `statement_timeout` 30 s | índice `ix_pump_opportunity_owner_decision` (migração 237) e timeout configurável (120 s) |
| Merge não atualizava o treino | serviço Railway de upload | treino no worker Celery, atualiza a cada deploy |

**Portão de qualidade (na inferência, sobre as métricas de teste fora da amostra do modelo mais recente):** AUC ≥ 0,55, Brier melhor que a taxa base e que o prior de calibração, IC 95 % do ganho de Brier por episódio acima de zero, ≥ 30 episódios no teste (`score_v1.ml.quality`). Modelo mais novo sempre substitui o anterior; reprovado ou ausente → efeito zero.

**Efeitos (só com modelo aprovado):**
- Score: × [0,85; 1,15], linear em (2p − 1) (`score_v1.ml.score.max_adjust`).
- Seta: p ≥ 0,60 confirma ▲ (forte), p ≤ 0,40 confirma ▼; o contrário rebaixa para normal; nunca inverte.
- Regime: média de p no universo (≥ 10 ativos) < 0,45 → no máximo neutro; < 0,40 → desfavorável. Só piora o regime.

Features: as 9 do contrato congelado (`pump_numeric_features_v1`). A versão com campos v1/perp/capital exige um novo contrato de features e coorte própria.

**Primeiro treino (07/10 14:58 UTC, manual):** 15 min reprovado — AUC 0,538; Brier 0,379 contra 0,232 da taxa base; IC95 do ganho [−0,196; −0,093]; 103 episódios de teste. O modelo ficou pior que a taxa base com significância, padrão de calibração confiante e errada.

## v1.7 — Contexto v1 no ML, calibração contida, métricas por horizonte (2026-10-07)

**Variáveis de contexto (opcionais):** dicionário separado `pump_context_features_v1` (`CONTEXT_FEATURE_SPEC`), para não alterar o hash do contrato congelado que todas as observações já gravadas carregam. São 22 colunas (`pump_opportunity.research.context_features`):
- estrutura v1 de 5 min (`v1_*`, 13 colunas);
- perp da Gate (`perp_*`, 4 colunas);
- regime de preço e fluxo de capital do universo (`ctx_breadth`, `ctx_ref_progress_atr`, `ctx_ref_ret_pct`, `ctx_capital_ratio`, `ctx_capital_z`).

Regras:
- Valor ausente entra como NaN, no treino e na inferência. Nunca é inventado e nunca elimina a linha. As 9 variáveis centrais continuam obrigatórias.
- Nenhuma variável de contexto depende do próprio ML: usa o regime de preço, nunca o regime limitado pelo ML, e nunca o score v1.
- Na inferência, o ciclo roda uma pré-avaliação do v1 sem ML, sobre uma cópia do estado, para que o modelo veja as mesmas células que as observações gravam. A função única `context_cells` serve às duas pontas.

Disponibilidade, sem preenchimento retroativo (snapshot imutável):

| Grupo | Gravado desde |
|---|---|
| `v1_*` | 05/10 (~18h UTC) |
| `perp_*` | 07/10 (~13h30 UTC) |
| `ctx_*` | deploy da v1.7 |

O bloco de treino (o mais antigo) recebe essas variáveis por último, então o efeito delas aparece com dias de acúmulo. A janela de histórico é configurável: `research.lookback_days`, padrão 30.

**Calibração:**
- `research.calibration_pool = validation_and_calibration`: a validação nunca ajusta nem interrompe o modelo, então é fora da amostra também para a calibração. Isso dobra o bloco de calibração.
- `research.calibration_method = platt_bounded`: a inclinação fica limitada a [0; `calibration_max_slope`=1] e o intercepto é reajustado.
  - Inclinação acima de 1 amplificava a confiança do booster.
  - Inclinação negativa (modelo invertido) vira a taxa base do bloco, em vez de uma probabilidade confiante e errada.
- Cortes temporais configuráveis em `research.cohort_cuts = [0,5; 0,65; 0,8]`, antes fixos em (0,5; 0,7; 0,85): teste com 20 % do período, antes 15 %.
- Modelos antigos, sem esses campos, continuam com Platt livre (`platt`).

**Métricas novas por modelo:**
- frequência de alta em cada bloco (treino, validação, calibração, teste);
- probabilidade média no teste;
- parâmetros da calibração, incluindo a inclinação antes do limite;
- cobertura das variáveis de contexto por bloco;
- importância por ganho.

**Painel:** o chip "ML" agora abre a tabela de todos os horizontes treinados, servida por `GET /api/pump-monitor/ml/models`. Para cada um: status, AUC, Brier contra a base, alta no teste contra alta no treino, episódios, calibração e as variáveis mais usadas. O horizonte aplicado continua sendo `score_v1.ml.horizon_minutes`.

**Retreino com a v1.7 (07/10 16:22 UTC):**
- 10 min: AUC 0,500. A calibração zerou a inclinação, que tinha saído −0,068, ou seja, o modelo estava invertido.
- 15 min: AUC 0,470.
- Ambos reprovados.
- A frequência de alta por bloco no 15 min foi [0,60; 0,73; 0,74; 0,41]. A direção absoluta em 10 a 15 min é dominada pela maré do dia.

## v1.8 — Objetivo relativo ao mercado, ajustado por beta (2026-10-07)

**Pergunta do modelo:** "este ativo vai terminar o horizonte acima do mercado?". Continua sendo direção, sem alvo de %. Objetivo `pump_relative_direction_v1`; `score_v1.ml.objective = relative` é o padrão e `absolute` mantém o comportamento anterior. Os dois objetivos nunca se misturam na inferência.

**Rótulo:**
1. `r_i` = retorno do ativo até o fim do horizonte, referenciado no **mid** (mid = ask / (1 + spread/200)). O rótulo gravado usa o melhor ask, que embute cerca de meio spread de perda e cresce com a iliquidez. Sem essa correção, o modelo aprenderia o spread.
2. `β_i` = inclinação MQO dos retornos de 5 min do ativo contra a mediana do universo, nas 288 velas fechadas **antes** da decisão (`ohlcv`, Gate preferida; mínimo de 200 pontos).
3. `e_i = r_i − β_i · mediana(r)`. O rótulo é `e_i > mediana(e)` no mesmo minuto, considerando todos os ativos rotulados naquele minuto. Empate exato é excluído, e o minuto precisa de pelo menos 10 ativos (`research.relative_min_assets`).

**Por que o beta (velas de 5 min da Gate, universo atual de 50 ativos, 01/10 22:00 a 07/10):** nos minutos claramente direcionais (|mediana| ≥ 0,3 %), a mediana entre ativos de |P(supera | mercado cai) − P(supera | mercado sobe)| é:

| Rótulo | Diferença mediana |
|---|---|
| relativo simples | 0,553 |
| normalizado por volatilidade | 0,338 |
| resíduo com beta | 0,111 |

No rótulo simples, RLUSD e TRX superam o mercado em 100 % das quedas e em 0 % das altas.

**Efeitos no v1 com modelo aprovado:**
- score × [0,85; 1,15];
- confirma ou rebaixa a seta, sem nunca inverter;
- **sem efeito no regime**: a média das probabilidades relativas fica perto de 0,5 por construção. O regime continua com preço e capital.

Config:
- `research.target_mode` (`relative_universe_median` | `absolute`);
- `research.relative_min_assets`;
- `research.relative_beta` = `{enabled, timeframe, window_candles, min_points}`.

**Primeiro treino relativo (07/10 17:42 UTC):**
- AUC 0,499 em 10 min e 0,500 em 15 min, os dois reprovados.
- A frequência de "acima" por bloco ficou entre 0,43 e 0,57; antes oscilava entre 0,25 e 0,74.
- As cerca de 4,3 mil linhas vieram de só 168 e 162 minutos distintos.

## v1.9 — Amostragem por minuto e momento relativo (2026-10-07)

**Amostragem:** `research.max_rows_per_minute = 5`.
- Cada minuto de decisão contribui com no máximo 5 ativos, escolhidos por hash determinístico do `observation_id`, sem olhar o resultado.
- O corte é aplicado antes da projeção JSON, usando o índice `(user_id, decision_at, observation_id)`.
- O mesmo orçamento de linhas passa a cobrir cerca de 5× mais momentos de mercado.
- A mediana do mercado em cada minuto continua sendo calculada com todos os ativos rotulados daquele minuto.

**Variáveis derivadas** (dicionário de contexto):
- `rel_prev15_resid`: resíduo relativo das 3 velas fechadas anteriores, `(r_prev − β·mediana(r_prev)) − mediana(...)`. É a mesma construção do rótulo, aplicada à janela anterior.
- `beta_24h`: o próprio beta.

Regras:
- As duas são calculadas a partir de `ohlcv` 5m fechado no momento da decisão.
- No treino, são recalculadas em memória, sem gravar nada no snapshot imutável.
- Na inferência, o ciclo usa a mesma função (`relative_features` + `residual_prev`) com os parâmetros congelados no manifesto do modelo (`spec.relative_beta`), com cache por vela fechada.
- Motivação (velas de 5 min da Gate, 02 a 07/10, 72.493 pares): P(próximo acima | anterior acima) = 0,4774 contra 0,5225 quando o anterior ficou abaixo. É uma reversão leve.

**Treino com amostragem e momento relativo (07/10 20:16 UTC):**
- Os dados vieram de 1.438 minutos distintos no horizonte de 10 min e 1.370 no de 15 min.
- 10 min: AUC 0,520, com `beta_24h` e `rel_prev15_resid` como as variáveis mais usadas. O intervalo de confiança cruza zero.
- 15 min: AUC 0,487.
- Os dois foram reprovados.

## v1.10 — Avaliação walk-forward por dia e métrica econômica (2026-10-07)

`research.walk_forward = {enabled, min_train_days: 2, calibration_fraction: 0.2, economic_quantile: 0.1}`

**Como funciona:**
- Cada dia UTC com pelo menos 2 dias anteriores vira teste uma vez.
- O modelo daquele dia treina só com o passado, até o início do dia menos o embargo, e descarta episódios que tocam o dia de teste.
- A calibração usa a última fração desse passado, com o mesmo Platt limitado.
- As previsões feitas fora do treino são agrupadas e geram:
  - AUC e Brier ponderados por episódio, comparados à taxa base do treino de cada dia, com IC por bootstrap de episódios;
  - AUC de cada dia (estabilidade);
  - a métrica econômica: retorno excedente médio (pp) dos 10% de maior probabilidade menos o dos 10% de menor.

**Portão de qualidade:** `score_v1.ml.quality.source = walk_forward`. Os mesmos critérios (AUC ≥ 0,55, Brier melhor que a taxa base, IC positivo, episódios mínimos) passam a ser aplicados ao resultado agrupado. Modelos sem walk-forward continuam avaliados pelo holdout.

**Cobertura de velas:** `GET /api/pump-monitor/ml/candle-coverage` é somente leitura. Mostra o histórico de velas de 1 e 5 min por ativo do universo e dimensiona o futuro dataset histórico montado a partir de velas.

## v1.11 — Família "velas": histórico a partir de `ohlcv` (2026-10-07)

**Por quê:** o dataset de observações só começa em 01/10, e o bloco de treino de cada modelo enxergava cerca de 2 dias. Tudo o que o objetivo relativo precisa sai de velas fechadas:
- beta;
- resíduos relativos;
- o movimento do BTC à frente das altcoins (lead);
- o retorno do mercado;
- o próprio rótulo.

Por isso um modelo só de preço pode aprender com todo o histórico de velas desde já. O fluxo de ordens (delta, CVD, persistência compradora) existe só ao vivo e não entra nesta família.

**Construção:** `pump_ml_candles.build_frame` é uma função única e vetorizada, usada tanto no treino (todas as decisões) quanto na inferência ao vivo (última vela fechada). Uma decisão no fechamento da vela t usa apenas velas até t.

Variáveis:
- `beta_24h`;
- `rel_resid_{1,3,6,12}`: resíduo relativo de 5, 15, 30 e 60 min;
- `vol_ratio`: volatilidade de 1h dividida pela de 24h;
- `btc_ret_{1,3}`;
- `lag_gap_{1,3}`: β × retorno do BTC menos o retorno do ativo, ou seja, o quanto ainda falta o ativo acompanhar;
- `mkt_ret_{1,3}`.

Rótulo: resíduo com beta acima da mediana do minuto. Há dois modos:
- `endpoint`: no fim do horizonte;
- `path_mean`: média do resíduo ao longo do horizonte, menos ruidosa e usada como padrão.

Os dois modos são avaliados com o mesmo walk-forward, em `metrics.label_variants`.

**Amostragem:** no máximo 5 ativos por instante (hash) e até 40 mil linhas, com instantes espaçados de forma uniforme.

**Execução:**
- família própria no ledger (`payload.family = candle`), com lock próprio, regra diária própria e orçamento de 840 s;
- treino agendado às 06:10 UTC;
- disparo manual em `POST /api/pump-monitor/ml/train?family=candle`;
- configuração em `research.candle`.

**Aplicação:** `score_v1.ml.objective = relative_candle` faz o v1 usar o modelo de velas, que recalcula as variáveis no ciclo com os parâmetros do manifesto. O padrão continua `relative` até a família de velas passar no portão de qualidade.

**Limites conhecidos:**
- viés de sobrevivência: o universo usado é o das últimas 24h de observações;
- o histórico depende do que o coletor gravou em `ohlcv` (ver `GET /ml/candle-coverage`);
- a decisão ao vivo ocorre a cada minuto, mas as variáveis só mudam a cada vela fechada.

## v1.12 — Portão por dia, histórico de 90 dias e comparação de rótulos (2026-10-07)

**Motivo:** a AUC agregada de todas as linhas pode parecer boa mesmo quando o modelo erra na maioria dos dias. Na família de observações, 15 min, a AUC agregada foi 0,526 com 0 de 4 dias acima de 0,5. Por isso, o portão agora mede a consistência entre dias.

**Walk-forward (`pump_walk_forward_daily_v2`):**
- `pooled.auc` passa a ser a **mediana das AUCs diárias** (`auc_statistic = day_median`). A AUC agregada fica só como referência, em `pooled_auc_reference_only`.
- `sign_test_p` é um teste binomial unilateral sobre o número de dias com AUC > 0,5.
- O ganho de Brier é a média diária (`brier_improvement_day_mean`). O IC95 vem de bootstrap sobre **dias**, não sobre linhas (`ci_scope = bootstrap_over_days`), porque linhas do mesmo dia são correlacionadas.
- Métrica econômica: os cortes de decil usam todas as previsões fora da amostra. O IC95 do spread top − bottom vem de bootstrap sobre dias. Os campos `days_spread_positive` e `days_with_spread` mostram em quantos dias o spread foi positivo.
- `walk_forward.max_folds` (padrão 30) limita o número de dias avaliados, para que o treino caiba no orçamento de tempo.

**Portão de qualidade:** `score_v1.ml.quality.max_sign_test_p` (padrão 0,05). Se o p do teste de sinal passar desse valor, o modelo é reprovado com o motivo `days_not_consistently_above_half`, mesmo com bons números agregados.

**Histórico:** `research.candle.lookback_days` passou de 30 para 90.

**Painel:** cada modelo mostra:
- a AUC mediana diária, com o p do teste de sinal;
- o spread com IC95 por dias e o número de dias positivos;
- na família de velas, a linha "Rótulos comparados", com `endpoint` vs `path_mean`. A métrica econômica dos dois usa o mesmo retorno residual no ponto final, por isso é comparável.

## v1.13 — Modelo de velas aplicado ao v1, com ajuste máximo de ±5% (2026-10-08)

**Evidência** (treino de 2026-10-08 03:22 UTC, walk-forward de 30 dias, `GET /api/pump-monitor/ml/models`): o modelo de velas de 15 min foi o primeiro a passar no portão.
- AUC mediana diária: 0,558.
- Dias com AUC > 0,5: 26 de 30, com p de sinal igual a 3,0e-5.
- Ganho de Brier: +0,0022, com IC95 por dias de [0,0011; 0,0032].
- Diferença entre o top 10% e o bottom 10%: +0,064 pp, com IC95 por dias de [0,038; 0,097].

O modelo de 10 min continua reprovado (AUC 0,536). A família de observações também (AUC 0,526, com 0 de 4 dias acima de 0,5).

**Mudança nos padrões:**
- `score_v1.ml.objective` passa de `relative` para `relative_candle`.
- `score_v1.ml.score.max_adjust` passa de 0,15 para 0,05. O score fica entre ×0,95 e ×1,05.

**Por que ±5%:**
- o efeito é pequeno: 6,4 bps de excesso em 15 min entre os decis extremos;
- o teste de sinal supõe que os dias são independentes;
- ainda não houve validação ao vivo.

A seta de direção praticamente não muda, porque `direction.confirm_up` é 0,60 e `confirm_down` é 0,40. O teto de regime do ML continua desligado nos objetivos relativos.

**Proteções que continuam valendo:**
- Se um treino futuro reprovar o modelo, ou se ele passar de `max_model_age_days` (7), o v1 roda sem ML.
- O treino de velas é diário, às 06:10 UTC.

**Reversão:** voltar `objective` para `relative` e `max_adjust` para 0,15, por config ou por `git revert`.

**Próximo passo:** comparar, durante 1 a 2 semanas, a AUC ao vivo das previsões aplicadas com o 0,558 do walk-forward.

## v1.14 — Avaliação ao vivo do modelo aplicado (2026-10-08)

**Problema:** a probabilidade aplicada pelo ML não era gravada de forma durável:
- as observações de pesquisa excluem o ML de propósito, para não contaminar o dataset de treino;
- `pump_monitor_snapshots` guarda só amostras (1 a cada 10 ciclos) e só por 48h.

Sem esse registro, não havia como comparar o desempenho ao vivo com o 0,558 do walk-forward.

**Registro (`pump_ml_live_predictions`, migration `238_pump_ml_live_predictions`):**
- a tabela é só de inserção, com uma linha por experimento × horizonte × decisão × ativo, e repetições ignoradas;
- a cada ciclo em que o modelo de velas aplicado está ativo, o v1 grava a probabilidade de cada ativo do pool;
- `decision_at` é o fechamento da vela que alimentou as variáveis.

Configuração em `score_v1.ml.live_log`: `{enabled: true, retention_days: 120}`. A limpeza diária usa `retention_days`. Se a gravação falhar, o ciclo segue normalmente e só um aviso vai para o log.

**Avaliação (`GET /api/pump-monitor/ml/live-evaluation?days=14`, só leitura):**
- o rótulo realizado é recalculado a partir de `ohlcv` com o mesmo `build_frame` e o mesmo `label_mode` do manifesto do modelo, e com o mesmo mínimo de ativos por instante que o treino;
- uma previsão só entra na conta quando o horizonte inteiro já fechou; antes disso fica como pendente.

Métricas reportadas:
- AUC agregada e AUC mediana por dia;
- número de dias com AUC > 0,5 e o p do teste de sinal;
- Brier;
- spread entre o top e o bottom 10%, com o mesmo `economic_quantile` do walk-forward.

O painel do ML mostra uma linha "Ao vivo" no modelo aplicado.

**Limite conhecido:** se o coletor gravar a vela fechada com atraso, as variáveis daquela janela de 5 min ficam uma vela atrasadas, mas a decisão continua registrada no fechamento esperado. A avaliação mede exatamente o que o sistema fez, inclusive esse atraso.

## v1.15 — Rótulo relativo no tooltip e coluna "ML acima do mercado" (2026-10-08)

No detalhe do score, a linha do ML passa a se chamar `ml:acima_do_mercado` nos objetivos relativos. `ml:prob_alta` fica só para o objetivo absoluto. A probabilidade também vira coluna opcional, `ml_up_probability`, no grupo Scores, com cor por sinal em torno de 0,5.

## v1.16 — Mais linhas no treino de velas e histórico longo de fluxo e perpétuo (2026-10-08)

**Treino de velas** (`research.candle`):
- **Linhas:** de 40 mil linhas, com até 5 ativos por instante, para 100 mil linhas, com até 12. Antes, só ~3% da amostra de 90 dias era usada.
- **Comparação de rótulos:** removida (`compare_label_modes: ["path_mean"]`). `path_mean` venceu `endpoint` nos dois horizontes, e a comparação dobrava o tempo de treino.
- **Ordem dos horizontes:** `[15, 10]`. O horizonte aplicado treina primeiro e nunca é o cortado pelo orçamento de 840 s.
- **Benchmark local** (dados sintéticos, 90 dias, um horizonte): 40 mil linhas com comparação levaram 54 s; 120 mil linhas sem comparação, 116 s; 120 mil com comparação, 255 s.

**Histórico de 5 minutos** (migration `239_pump_flow_history`, config `flow_history`: `{enabled: true, step_seconds: 300, retention_days: 180}`):

O motivo é que `flow_buckets_1m` guarda só 2 dias e as estatísticas do perpétuo só existem no Redis. Assim, nenhum modelo conseguia aprender com fluxo ou derivativos. A partir desta versão, cada ciclo grava duas tabelas:

| Tabela | Conteúdo | Chave |
|---|---|---|
| `pump_flow_5m` | Compra e venda a mercado em USDT, número de trades, minutos encontrados e minutos parciais por ativo e janela de 5 min. Agregado dos buckets de 1 min das 2 últimas janelas fechadas. | `(symbol, bucket_start)` |
| `pump_perp_stats_5m` | Linhas de `contract_stats` do Gate exatamente como vieram, com `stat_time` = `time` do Gate. | `(symbol, stat_time, stat_interval)` |

Regras:
- As duas gravações são idempotentes. Minutos ausentes não são preenchidos.
- As fontes ficam separadas de propósito. Quando virarem variáveis do modelo, cada fonte será alinhada explicitamente ao instante da decisão, usando só dados com tempo ≤ decisão − intervalo.
- Falhas na gravação não afetam o ciclo.
- A limpeza diária usa `retention_days`.

**Cobertura:** `GET /api/pump-monitor/flow-history/coverage`.

**Próximo passo:** com 3 a 4 semanas de histórico, adicionar variáveis de fluxo e perpétuo à família de velas e comparar no walk-forward com o modelo atual.

## v1.17 — A config de oportunidades guarda só as alterações (2026-10-08)

**Defeito:**
- `put_config` gravava a config inteira já mesclada com os padrões.
- A tarefa `refresh_listing_contracts` chama `put_config` a cada 6 horas, só para atualizar a lista de ativos certificados.
- Cada execução congelava todos os padrões daquele momento, e mudanças posteriores nos padrões de `research.*` nunca chegavam ao usuário. Exemplo: a v1.16 (100 mil linhas, horizontes [15, 10]) não entrou em produção.

**Correção:**
- `put_config` passa a gravar `eng.overrides(c)`: só os valores que diferem do `DEFAULT_CONFIG`, mais `PERSIST_ALWAYS`. Esse segundo grupo é gravado sempre porque tarefas leem esses campos direto do JSON por SQL. Ele inclui:
  - as flags de topo, como `enabled` e `training_job_enabled`;
  - os mapas `listing_*`.
- A leitura não muda: `config(stored)` continua mesclando os padrões com o que está gravado.
- **Efeito colateral aceito:** um valor escolhido de propósito igual ao padrão passa a acompanhar o padrão se o padrão mudar.

**Limpeza (migration `240_pump_unfreeze_candle`):**
- Remove de `research.candle` só as chaves que ainda têm exatamente os padrões antigos: `max_rows` 40000, `max_rows_per_time` 5, `horizons_minutes` [10, 15] e `compare_label_modes` ["endpoint", "path_mean"].
- Qualquer outro valor fica intacto.
- Cada linha alterada ganha um registro em `config_audit_log`, com o JSON anterior.
- Rodar de novo não altera nada.

## v1.18 — Grupos de variáveis, ablação e histórico do livro (2026-10-08)

**Grupos opcionais no modelo de velas** (`research.candle.feature_groups`, padrão `[]`, que mantém as 12 colunas originais na mesma ordem). Todos usam só velas fechadas até a decisão.

| Grupo | Colunas | Precisa de OHLC/volume |
|---|---|---|
| `btc_beta` | `beta_btc_24h`, `lag_gap_btc_{1,3}` | não |
| `candle_structure` | amplitude, corpo, pavios, posição do fechamento, distância à máxima e à mínima de 1 hora | sim |
| `volume` | volume em USDT da vela e das 3 últimas velas, relativo à mediana de 24h do próprio ativo; aceleração de 3 velas | sim |
| `pool_context` | dispersão dos retornos do pool (5 e 15 min), proporção de ativos subindo e posição do ativo no pool em 15 min | não |

Detalhes:
- O grupo `btc_beta` corrige um problema: o `lag_gap` original usa o beta contra a mediana do pool, mas multiplica pelo retorno do BTC. Com `btc_beta`, o `lag_gap` original sai e entram as versões calculadas com o beta contra o próprio BTC.
- Treino e inferência ao vivo usam o mesmo `build_frame`. O `ohlcv` é lido com preferência pela Gate, e `quote_volume` cai para `volume` quando vem vazio.

**Ablação (família `candle_ablation`)**:
- Disparo manual em `POST /ml/train?family=candle_ablation`; resultado em `GET /ml/ablation`.
- Usa as mesmas linhas e os mesmos dias de teste para a base e para cada variante: base + 1 grupo por vez, e no fim todos juntos.
- Para cada variante: walk-forward e comparação dia a dia contra a base. São reportados a diferença mediana de AUC, o número de dias em que a variante foi melhor e o teste de sinal.
- Não salva modelo e não aplica nada.
- Configuração em `research.candle.ablation`: horizonte 15 min, 20 dias de teste, 100 mil linhas.
- Benchmark local com dados sintéticos e 60 mil linhas por 15 dias: cerca de 16 s por variante.

**Histórico do livro de ofertas** (migration `241_pump_book_5m`):
- Guarda o último retrato de cada janela de 5 minutos: spread, profundidade ±1%, desequilíbrio e slippage estimado.
- Cada retrato tem `observed_at`, a hora do ciclo, e `computed_at`, a hora do recebimento. Um retrato mais antigo nunca sobrescreve um mais novo.
- A retenção segue `flow_history.retention_days`.
- Uso pretendido: filtro de execução e variáveis de liquidez, depois de semanas de histórico.

**Critério de adoção de um grupo:** só entra em `feature_groups` se melhorar a AUC diária contra a base de forma consistente entre os dias, com teste de sinal significativo, e sem piorar o spread econômico.

### v1.18.1 — Ablação que não perde resultado (2026-10-08)

**O que aconteceu:** a primeira ablação em produção (17:52 UTC) passou do orçamento e foi encerrada pelo limite rígido da tarefa (`deadline_exceeded`), sem gravar nada.

**Causas:**
- **Estimativa de tempo errada.** No perfil local em tamanho de produção (51 ativos, 100 mil linhas, 20 dias), cada variante levou cerca de 65 a 70 s. Com 6 variantes, e a produção rodando aproximadamente 1,7 a 2× mais devagar que o ambiente local, o tempo passa dos 840 s.
- **Resultado gravado só no fim.** Ao ser encerrada, a execução perdeu todas as variantes que já tinham terminado.
- **Trabalho pesado fora do controle de tempo.** A montagem das linhas e a conversão das velas rodavam no laço principal, onde a verificação de prazo não consegue interromper.

**Correção:**
- `progress`: o resultado parcial é gravado em `pump_ml_job_runs.payload` depois de cada variante.
- O tempo de cada fase fica registrado: carga, montagem do frame e montagem das linhas.
- A montagem das linhas e a conversão das velas passam a rodar fora do laço principal.
- Uma variante só começa se o tempo restante for maior que 1,3 × a variante mais lenta já concluída. As que não couberem ficam marcadas como `runtime_budget_exhausted`.
- O tamanho de triagem passa a ser 60 mil linhas × 15 dias.
- Uma família aprovada na triagem é confirmada numa segunda execução, só com ela (`ablation.groups = [família]`) e com mais dias.
- `GET /ml/ablation` mostra `deadline_exceeded` e o resultado parcial já gravado.
