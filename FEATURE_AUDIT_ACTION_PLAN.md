# PLANO DE AÇÃO — CORREÇÃO DO PIPELINE DE FEATURES
**Baseado em:** Auditoria completa de features para XGBoost (2026-05-21)
**Status:** Pendente de execução
**Regra:** Não fazer refactor além do escopo de cada item.

---

## PRIORIDADES

| Nível | Critério                                               |
|-------|--------------------------------------------------------|
| P0    | Dado errado em produção / leakage que invalida o modelo|
| P1    | Feature ausente frequentemente / redundância crítica   |
| P2    | Feature não estacionária / encoding incorreto          |
| P3    | Qualidade menor, investigação necessária               |

---

## P0 — CRÍTICO (corrigir antes de qualquer treino)

---

### P0-1 · `market_cap` sempre retorna 0
**Arquivo:** `backend/app/services/market_data_service.py`
**Problema:** `result["market_cap"] = 0` — hardcoded, Gate.io não fornece via ticker.
**Ação:**
- Remover `market_cap` do dict de resultado de `get_ticker()`.
- Remover `market_cap` do `FEATURE_COLUMNS` em `feature_extractor.py` se estiver presente.
- Adicionar comentário explicando que Gate.io não expõe market cap via ticker spot.
- Verificar se algum consumer downstream lê `market_cap` e espera um valor (substituir por `None` ou omitir).

**Teste:** Chamar `get_ticker("BTC_USDT")` e confirmar que `market_cap` está ausente ou `None`.

---

### P0-2 · `score` presente em `FEATURE_COLUMNS` do XGBoost
**Arquivo:** `backend/app/ml/feature_extractor.py` — linha 44
**Problema:** `score` é calculado a partir de `rsi`, `ema9_gt_ema21`, `volume_spike`, `atr_pct`, `macd_signal`, `price > vwap`. Usar score como feature junto com seus inputs cria dependência circular: o modelo treina primariamente no score e ignora as features individuais.
**Ação:**
- Remover `"score"` de `FEATURE_COLUMNS`.
- Avaliar se `score` deve ser usado como **target** ou como **feature de segunda ordem** em modelo separado (meta-feature isolada, sem os seus inputs no mesmo modelo).
- Atualizar `build_training_dataframe()` se necessário.
- Documentar decisão no arquivo.

**Teste:** Re-treinar e verificar feature importance — as features individuais devem ter importância distribuída, não concentrada em score.

---

### P0-3 · Zero-fill universal no fallback de `extract_features()`
**Arquivo:** `backend/app/ml/feature_extractor.py` — linha 57-58
**Problema:**
```python
if not metrics:
    return {f: 0.0 for f in FEATURE_COLUMNS}
```
`taker_ratio=0.0` significa "100% venda"; `ema9_gt_ema21=0.0` significa "bear trend"; `volume_spike=0.0` significa "sem volume". Zeros são sinais válidos, não indicadores de ausência. Rows com dados ausentes entram no treino como sinais de mercado extremos.
**Ação:**
- Substituir fallback por `float("nan")` para todas as features.
- Adicionar filtro no `build_training_dataframe()` para remover rows com excesso de NaN (ex.: >30% de features ausentes).
- Garantir que o pipeline de treino do XGBoost chame `df.dropna(subset=critical_features)` antes do fit.

**Teste:** Passar `metrics=None` e confirmar que o resultado contém `nan`, não `0.0`.

---

### P0-4 · `flow_strength` com zero artificial quando `volume_delta=None`
**Arquivo:** `backend/app/ml/feature_extractor.py` — linha 76
**Problema:**
```python
f["flow_strength"] = _float("taker_ratio") * _float("volume_delta")
```
`_float(None)` retorna `0.0`, portanto quando `volume_delta` não está disponível, `flow_strength = 0.0` indistinguível de "pressão neutra real".
**Ação:**
- Verificar se `volume_delta` existe em `metrics` antes de calcular.
- Se ausente: `f["flow_strength"] = float("nan")`.
- Código corrigido:
```python
if metrics.get("volume_delta") is not None and metrics.get("taker_ratio") is not None:
    f["flow_strength"] = _float("taker_ratio") * _float("volume_delta")
else:
    f["flow_strength"] = float("nan")
```

---

## P1 — ALTO (corrigir antes de ir para produção com novo modelo)

---

### P1-1 · Duplicatas exatas em `FEATURE_COLUMNS` e no pipeline
**Arquivos:**
- `backend/app/services/market_data_service.py`
- `backend/app/ml/feature_extractor.py`

**Duplicatas confirmadas:**

| Manter       | Remover             | Motivo                          |
|--------------|---------------------|---------------------------------|
| `taker_ratio`| `buy_pressure`      | Fórmulas idênticas              |
| `bid_ask_imbalance` | `orderbook_pressure` | Aliases — mesmo campo  |
| `close`      | `price`             | Idênticos em >99% dos casos     |
| `macd_histogram` | `macd_signal` (string) | signal = sign(histogram) |
| `score`      | `score_normalized`  | Aliases                         |

**Ação por par:**
- `buy_pressure`: remover do dict em `order_flow_service.py` e de qualquer consumer. Manter `taker_ratio`.
- `orderbook_pressure`: remover de `market_data_service.py`. Manter `bid_ask_imbalance`.
- `price`: manter somente se vier de fonte diferente (ticker live vs candle). Documentar a diferença; se idênticos, remover.
- `macd_signal` (string): substituir por `int(macd_histogram > 0)` onde necessário. Remover o campo string.
- `score_normalized`: manter apenas `score`. Remover `score_normalized` ou fazer alias transparente.

---

### P1-2 · EMAs absolutas no `FEATURE_COLUMNS` (não estacionárias)
**Arquivo:** `backend/app/ml/feature_extractor.py` — linhas 22-26
**Problema:** `ema5`, `ema9`, `ema21`, `ema50`, `ema200` são valores absolutos de preço. BTC EMA9 ≈ 100,000; altcoin EMA9 ≈ 0.001. O modelo aprende splits de preço absoluto que não generalizam entre ativos.
**Ação:**
- Remover `"ema5"`, `"ema9"`, `"ema21"`, `"ema50"`, `"ema200"` de `FEATURE_COLUMNS`.
- Já existem no set: `ema9_gt_ema21`, `ema50_gt_ema200`, `ema_distance_pct`. São suficientes.
- Se informação de "distância" das EMAs longas for desejada, adicionar:
  - `ema50_distance_pct = (close - ema50) / ema50 * 100`
  - `ema200_distance_pct = (close - ema200) / ema200 * 100`
  Ambas estacionárias e comparáveis cross-asset.

---

### P1-3 · `taker_buy_volume` e `taker_sell_volume` absolutos no `FEATURE_COLUMNS`
**Arquivo:** `backend/app/ml/feature_extractor.py` — linhas 33-34
**Problema:** Volumes absolutos não são comparáveis entre BTC (volume alto) e altcoins (volume baixo). O modelo aprende thresholds de volume que só funcionam para o ativo de maior volume no treino.
**Ação:**
- Remover `"taker_buy_volume"` e `"taker_sell_volume"` de `FEATURE_COLUMNS`.
- Já existem no set: `taker_ratio` (buy/total), `delta_normalized` (delta/volume_24h). São suficientes e escalam corretamente.
- Se intensidade absoluta de fluxo for desejada, adicionar `taker_volume_usdt_normalized = (buy+sell) / volume_24h_usdt`.

---

### P1-4 · `volume_24h_usdt` e `orderbook_depth_usdt` absolutos
**Arquivo:** `backend/app/ml/feature_extractor.py` — linhas 30-31
**Problema:** Idem — escalas incompatíveis cross-asset.
**Ação:**
- Aplicar `log1p()` antes de inserir no dataset: `log1p(volume_24h_usdt)` comprime a escala e mantém monotonia.
- Adicionar transformação dentro de `extract_features()`:
```python
import math
f["volume_24h_usdt"] = math.log1p(_float("volume_24h_usdt"))
f["orderbook_depth_usdt"] = math.log1p(_float("orderbook_depth_usdt"))
```
- Atualizar a transformação inversa no pipeline de inferência (se necessário).

---

### P1-5 · Inconsistência entre `REQUIRED_CORE_INDICATORS` e `CRITICAL_INDICATORS`
**Arquivos:**
- `backend/app/services/indicators_provider.py` — linha 59: `(adx, rsi, macd_histogram)`
- `backend/app/services/indicator_validator.py` — linha 44: `[volume_24h_usdt, rsi, adx]`

**Problema:** As duas listas divergem. `macd_histogram` é obrigatório no provider mas não no validator. `volume_24h_usdt` é obrigatório no validator mas não no provider. Cria inconsistência em qual conjunto de indicadores é "mínimo viável".
**Ação:**
- Definir uma única fonte de verdade: criar constante compartilhada em `backend/app/services/indicator_constants.py`:
```python
MINIMUM_VIABLE_INDICATORS = ("adx", "rsi", "macd_histogram")
CRITICAL_FOR_LIQUIDITY = ("volume_24h_usdt",)
```
- Fazer `indicators_provider.py` e `indicator_validator.py` importarem de lá.
- Não misturar indicadores de "presença mínima" com indicadores de "liquidez mínima".

---

## P2 — MÉDIO (antes do próximo ciclo de treino)

---

### P2-1 · `obv` não estacionário no dataset
**Arquivo:** `backend/app/services/feature_engine.py` + qualquer consumer que leia `obv`
**Problema:** OBV é cumulativo desde o início da série. Valor absoluto muda a cada reinício do DataFrame. Não comparável cross-asset ou cross-run.
**Ação:**
- No `extract_features()` ou no preprocessing do dataset: substituir `obv` por `obv_slope = (obv[-1] - obv[-N]) / N` (diferença normalizada), ou por sinal de `obv.diff()`.
- Se o FeatureEngine já exporta o valor absoluto, adicionar campo derivado `obv_change_pct` sem alterar o campo original.

---

### P2-2 · `vwap` com instabilidade matinal (reset diário)
**Arquivo:** `backend/app/services/feature_engine.py::_calc_vwap()`
**Problema:** Reset diário às 00:00 UTC faz VWAP ≈ current_price nos primeiros candles do dia. `vwap_distance_pct` ≈ 0 artificialmente durante 30-60 min após abertura.
**Ação:**
- Adicionar coluna de metadata `vwap_candle_count` = número de candles acumulados no dia atual.
- No `extract_features()`, marcar como NaN quando `vwap_candle_count < 12` (< 1 hora de dados em 5m candles).
- Não alterar a fórmula do VWAP — apenas sinalizar a janela de aquecimento.

---

### P2-3 · `macd_signal` (string) — encoding ausente
**Arquivo:** `backend/app/ml/feature_extractor.py` + `backend/app/services/feature_engine.py`
**Problema:** `macd_signal` é `"positive"` / `"negative"` (string). `_float()` retorna `0.0` para qualquer string não-numérica — silenciosamente aplaina tanto "positive" quanto "negative" para `0.0`.
**Ação (curto prazo enquanto P1-1 não é executado):**
- Em `extract_features()`, adicionar conversão explícita antes do loop:
```python
if "macd_signal" in metrics:
    metrics["macd_signal"] = 1.0 if metrics["macd_signal"] == "positive" else 0.0
```
- Longo prazo: remover `macd_signal` string do pipeline (P1-1).

---

### P2-4 · `psar_signal` e `psar_trend` — strings sem encoding
**Arquivo:** Qualquer consumer que leia esses campos para XGBoost
**Problema:** Campos string sem conversão explícita — `_float()` retorna `0.0` para ambos.
**Ação:**
- `psar_trend`: `1.0` se `"up"`, `0.0` se `"down"`, `nan` se ausente.
- `psar_signal`: `1.0` se `"BUY"`, `-1.0` se `"SELL"`, `0.0` se `"HOLD"`, `nan` se ausente.
- Adicionar conversão no `extract_features()` ou num step de preprocessing dedicado.

---

### P2-5 · `change_24h` com fallback silencioso para zero
**Arquivo:** `backend/app/services/market_data_service.py`
**Problema:** `float(ticker.get("change_percentage", 0) or 0)` — se o campo vier como `None` ou ausente, retorna `0.0` sem log. Change de 0% é um sinal legítimo, indistinguível de dado ausente.
**Ação:**
- Substituir por: `float(ticker["change_percentage"]) if ticker.get("change_percentage") is not None else None`
- No consumer downstream, tratar `None` como NaN explícito.
- Adicionar métrica de monitoramento: contagem de `change_24h=None` por símbolo por hora.

---

### P2-6 · `ema_distance_pct` redundante com `ema9_gt_ema21` + `ema9_distance_pct`
**Arquivo:** `backend/app/ml/feature_extractor.py` — linha 89
**Problema:** `ema_distance_pct = (ema9 - ema21) / ema21 * 100` é a distância contínua entre EMA9 e EMA21. O set já tem `ema9_gt_ema21` (direção) e `ema9_distance_pct` (distância close/ema9). Redundância elevada.
**Ação:**
- Manter `ema_distance_pct` (é a versão mais informativa dos três).
- Remover `ema9_gt_ema21` do `FEATURE_COLUMNS` se `ema_distance_pct` já estiver presente (sign(ema_distance_pct) = ema9_gt_ema21).
- Ou manter ambos e deixar o XGBoost decidir via feature importance.

---

## P3 — BAIXO (investigação / monitoramento)

---

### P3-1 · Confirmar existência de `rsi_6`, `rsi_12`, `rsi_24` nos dados históricos
**Arquivo:** `backend/app/services/seed_service.py` — `"periods": [6, 12, 24]`
**Ação:**
- Executar query direta na tabela `indicators`:
```sql
SELECT symbol, time, indicators_json->>'rsi_6' AS rsi_6
FROM indicators
WHERE scheduler_group = 'structural'
ORDER BY time DESC
LIMIT 20;
```
- Se `rsi_6` retornar `null` para a maioria dos registros, o FeatureEngine não está escrevendo multi-period.
- Se existir, adicionar ao `FEATURE_COLUMNS`.

---

### P3-2 · Confirmar persistência de `psar_af` e `psar_ep` na tabela
**Ação:** Mesma query acima para `psar_af` e `psar_ep`. Se não persistidos, são apenas estado interno do FeatureEngine e não disponíveis para o dataset.

---

### P3-3 · Medir frequência de `None` em `taker_ratio` e `volume_delta`
**Arquivo:** `backend/app/tasks/compute_indicators.py`
**Ação:**
- Adicionar métrica Prometheus ou log estruturado:
```python
if taker_ratio is None:
    logger.info("order_flow.none symbol=%s", symbol)
```
- Após 24h: calcular `% de ciclos com taker_ratio=None` por símbolo.
- Se > 40%: revisar estratégia de fallback (REST cap de 500 trades pode ser insuficiente para pares de alto volume).

---

### P3-4 · Auditar `volume_24h_usdt` nos dados de treino
**Problema potencial:** `volume_24h_usdt` pode ter staleness de até 10 min (ticker cache TTL). Em `indicator_validator.py` é listado como CRITICAL mas pode estar desatualizado.
**Ação:**
- Verificar timestamp do último `volume_24h_usdt` em cache vs. timestamp do candle.
- Se delta > 10 min em > 20% dos registros, aumentar frequência de refresh do ticker ou reduzir TTL para 3 min.

---

### P3-5 · Verificar `adx_acceleration` com dados históricos insuficientes
**Problema:** `adx_acceleration = adx[-1] - adx[-2]` requer pelo menos 2 rows consecutivas de ADX no buffer do FeatureEngine. Se o DataFrame for carregado com apenas 1 candle (edge case de símbolo novo), retorna `None` ou `NaN` sem aviso.
**Ação:**
- Adicionar guard no FeatureEngine:
```python
if len(adx_series.dropna()) < 2:
    result["adx_acceleration"] = None
```
- Verificar se esse guard já existe no código.

---

## SEQUÊNCIA DE EXECUÇÃO RECOMENDADA

```
Semana 1 (antes de re-treinar o modelo):
  P0-1  market_cap = 0             → remover campo
  P0-2  score em FEATURE_COLUMNS   → remover do set
  P0-3  zero-fill para NaN         → corrigir extract_features()
  P0-4  flow_strength com None     → corrigir cálculo
  P1-1  remover duplicatas         → buy_pressure, orderbook_pressure, macd_signal string
  P1-2  remover EMAs absolutas     → ema5/9/21/50/200 do FEATURE_COLUMNS
  P1-3  remover volumes absolutos  → taker_buy/sell absolutos
  P2-3  macd_signal encoding       → encoding explícito enquanto P1-1 não executado

Semana 2 (qualidade do dataset):
  P1-4  log1p em volumes           → volume_24h_usdt, orderbook_depth_usdt
  P1-5  unificar CRITICAL_INDICATORS → criar indicator_constants.py
  P2-1  obv_change vs obv absoluto → adicionar campo derivado
  P2-2  vwap warm-up flag          → marcar NaN nos primeiros 12 candles do dia
  P2-4  psar encoding              → conversão explícita de strings

Semana 3 (investigação):
  P3-1  confirmar rsi_6/12/24 no DB
  P3-2  confirmar psar_af/ep no DB
  P3-3  medir frequência de None em taker_ratio
  P3-4  auditar staleness de volume_24h_usdt
  P3-5  guard em adx_acceleration
  P2-5  change_24h fallback silencioso
  P2-6  avaliar ema_distance_pct vs ema9_gt_ema21
```

---

## FEATURE_COLUMNS FINAL PROPOSTO

Após todas as correções P0+P1, o `FEATURE_COLUMNS` resultante:

```python
FEATURE_COLUMNS = [
    # Momentum (bounded, estacionário)
    "rsi",                    # [0,100]
    "macd_histogram_pct",     # macd_histogram / close * 100
    "adx",                    # [0,100]
    "atr_pct",                # (atr/close)*100
    "stoch_k",                # [0,100]
    # Microstructure
    "taker_ratio",            # [0,1] — remover buy_pressure
    "volume_spike",           # ratio, estacionário
    "spread_pct",             # %, estacionário
    "bid_ask_imbalance",      # [-1,1] — remover orderbook_pressure
    # Volume normalizado
    "volume_24h_usdt",        # log1p aplicado
    "orderbook_depth_usdt",   # log1p aplicado
    "delta_normalized",       # volume_delta / volume_24h_usdt
    # Trend (relativos, estacionários)
    "vwap_distance_pct",      # (close-vwap)/vwap*100
    "ema9_distance_pct",      # (close-ema9)/ema9*100
    "ema_distance_pct",       # (ema9-ema21)/ema21*100 — substituindo ema absolutas
    "ema50_distance_pct",     # (close-ema50)/ema50*100 — novo, P1-2
    "ema200_distance_pct",    # (close-ema200)/ema200*100 — novo, P1-2
    # Booleans de trend
    "ema9_gt_ema21",          # {0,1}
    "ema50_gt_ema200",        # {0,1}
    "ema_full_alignment",     # {0,1}
    # Volatility / regime
    "bb_width",               # (upper-lower)/sma
    # PSAR
    "psar_distance_pct",      # |close-psar|/close*100
    "psar_reversal",          # {0,1}
    # Engineered
    "trend_alignment",        # int(ema9_gt_ema21) + int(ema50_gt_ema200) ∈ {0,1,2}
    "momentum_strength",      # macd_histogram * adx
    "flow_strength",          # taker_ratio * delta_normalized (NaN quando volume_delta ausente)
]
# Total: 26 features — todas estacionárias, sem leakage, sem duplicatas
```

---

## CHECKLIST DE VALIDAÇÃO PÓS-CORREÇÃO

Antes de re-treinar o modelo, verificar:

- [ ] `market_cap` ausente do dict de indicadores
- [ ] `score` ausente de `FEATURE_COLUMNS`
- [ ] `extract_features(metrics=None)` retorna dict com `nan`, não `0.0`
- [ ] `flow_strength` = `nan` quando `volume_delta` ausente
- [ ] Nenhum valor de EMA absoluto em `FEATURE_COLUMNS`
- [ ] `buy_pressure` ausente do pipeline (apenas `taker_ratio`)
- [ ] `orderbook_pressure` ausente (apenas `bid_ask_imbalance`)
- [ ] `volume_24h_usdt` com `log1p` aplicado
- [ ] `macd_signal` string substituído por int/bool ou removido
- [ ] Dataset de treino sem rows com >30% de features NaN
- [ ] Feature importance do modelo treinado: nenhuma feature com >25% da importância total (sinal de duplicata ou leakage)
- [ ] Cross-validation com dados de ativos não vistos no treino (generalização cross-asset)
