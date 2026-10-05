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
