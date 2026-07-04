# Encerramento da Fase ML — Pendências Finais e Decisão de Marco

Data: 2026-07-04
Executor: Claude (fechamento F1–F6 conforme `PROMPT_FECHAMENTO_FASE_ML.md`)
Status final: **AGUARDANDO MARCO** — retreino nº 2 (F5) NÃO executado; elegíveis `2349 < 2800` (marco).

---

## Sumário executivo

| Fase | Resultado |
|------|-----------|
| F1 | `shadow_max_open_age_hours` 18→48 aplicado; dano da janela 18h = **2 trades** (os 2 drains documentados, ambos entre 18h e 24h) |
| F2 | **COORTE INEXISTENTE** — a "drenagem de ~700 trades" foi artefato de contagem: 664 eram L3_LAB `CANCELLED` de 2026-06-17/18 contados pelo filtro `outcome IS NULL` sem filtro de status. Reconciliação exata: 664+25+174+1=864. Labels VÁLIDOS; nenhuma regra de exclusão necessária |
| F3 | Proposta `["trend_alignment","ema50_gt_ema200"]` + `ml_feature_exclusion_apply=true` gravados; consumo implementado no builder (lane L1); feature_count esperado = 31 |
| F4 | Checklist completo; marco `ml_retrain_min_eligible_rows=2800` gravado; elegíveis 2349 |
| F5 | **NÃO EXECUTADO** (2349 < 2800). Data projetada do marco: **2026-07-06 a 2026-07-08** |
| F6 | Este relatório. Veredito contra a tabela pré-registrada: **nenhuma linha aplicável** — nenhum treino ocorreu; tabela permanece imutável para o retreino nº 2 |

---

## F1 — `shadow_max_open_age_hours`: 18 → 48

### 1.1 Timeout natural confirmado (writer + produção)

Código — `backend/app/services/shadow_trade_service.py:75`:
```python
SHADOW_TIMEOUT_CANDLES = int(os.environ.get("SHADOW_TIMEOUT_CANDLES", "1440"))  # 24h de 1m
```
Consumo por trade: `shadow_trade_service.py:642` (`timeout_candles = int(user_config.get("timeout_candles") or SHADOW_TIMEOUT_CANDLES)`), avaliado no monitor em `shadow_trade_monitor.py:1079-1085` (timeout-elapsed) e `:1230` (scan candle).

Distribuição em produção (pós-fronteira) — **100% dos trades com 1440 candles = 24h**:
```
 timeout_candles | count
-----------------+-------
            1440 | 50867
```
Timeout nominal = 24h → 2× = **48h** confirmado como valor da rede de segurança. [query]

### 1.2 Config aplicada

```sql
UPDATE config_profiles SET config_json = config_json || '{"shadow_max_open_age_hours": 48, "shadow_max_open_age_rationale": "..."}'::jsonb
WHERE config_type='ml' AND is_active=true RETURNING config_json->>'shadow_max_open_age_hours';
-- novo_valor: 48 | UPDATE 1
```
Racional gravado na própria config (`shadow_max_open_age_rationale`): 2× timeout nominal; rede de segurança, não regra; 18h invertia a semântica (força-fechava antes da expiração natural).

### 1.3 Dano da janela 18h quantificado

Todos os force-closes existentes no banco (query em `exit_metrics_json->>'force_closed'='true'`):
```
                  id                  |   source    |  symbol   | outcome |  age_h  |         completed_at
--------------------------------------+-------------+-----------+---------+---------+-------------------------------
 7704ee7a-fcff-40b7-876d-18690d9b5041 | L1_SPECTRUM | RAIN_USDT | TIMEOUT | 23.4778 | 2026-07-03 20:57:11.718831+00
 8563eeb8-e7e7-448d-a94a-7b08549e1f17 | L3          | RAIN_USDT | TIMEOUT | 21.9440 | 2026-07-03 20:57:12.182512+00
```
**2 trades force-closed no banco inteiro**, ambos com idade entre 18h e 24h → ambos são fechamentos prematuros da janela 18h (são exatamente os 2 drains documentados no pre-retreino H3). Labels v2 permanecem válidos (holding > 4h ⇒ label 0 de qualquer forma); EV/win-rate desses 2 ids é impreciso e a exclusão de análises de EV já é coberta pelo marcador `force_closed`. Nenhum force-close adicional ocorreu desde a aplicação da config de 18h. [query]

---

## F2 — Prestação de contas da drenagem (~700 trades)

### Veredito: COORTE INEXISTENTE — artefato de contagem, não drenagem

**Reconciliação exata dos 864 "abertos" do E11.** O E11 contou `outcome IS NULL` para L1+L3+L3_LAB pós-fronteira **sem filtrar `status`**. Reconstrução do instante do snapshot (t = 2026-07-03 14:05):
```sql
SELECT status, source, COUNT(*) FROM shadow_trades
WHERE source IN ('L1_SPECTRUM','L3','L3_LAB') AND created_at >= '2026-06-14 21:33:00+00'
  AND created_at <= '2026-07-03 14:05:00+00'
  AND (outcome IS NULL OR completed_at > '2026-07-03 14:05:00+00')
GROUP BY 1,2;
--   status   |   source    | count
-- -----------+-------------+-------
--  CANCELLED | L3_LAB      |   664
--  COMPLETED | L1_SPECTRUM |    25
--  COMPLETED | L3          |   174
--  RUNNING   | L1_SPECTRUM |     1
```
**664 + 25 + 174 + 1 = 864** — bate exatamente com o E11. Os 664 são L3_LAB `CANCELLED` criados em 2026-06-17/18 (o "mais antigo 2026-06-17 17:00 / 381h" do E11 é o primeiro desse lote). Eles seguem `outcome IS NULL` **até hoje** — nunca drenaram, nunca produziram label:
```
   source    |  status   |  n  |          mais_antigo          |           mais_novo
 L3_LAB      | CANCELLED | 664 | 2026-06-17 17:00:17.527367+00 | 2026-06-18 00:05:18.830611+00
```
O monitor só seleciona `status IN ('RUNNING','PENDING')` — `shadow_trade_monitor.py:1291`, `:1350`, `:1485` — logo CANCELLED jamais entra no batch; e o dataset exige `outcome IN ('TP_HIT','SL_HIT','TIMEOUT')`, logo essas linhas estão fora do treino por construção. Os ~200 realmente abertos (25+174+1 COMPLETED/RUNNING no snapshot) resolveram por turnover normal de trades jovens (a resolução diária corrente é de 3.7k–9k trades/dia).

### 2.1 Coorte do recorte do prompt (resolvidos ≥ 07-03 com created_at < 07-02)

```
 outcome | bucket | count | avg_pnl
---------+--------+-------+---------
 TIMEOUT | >24h   |     1 | -0.5405
 TP_HIT  | >24h   |     1 |  1.0000
```
**2 trades** (não ~700): `c3514aa3-dcd2-49ee-b27b-818acafc6bcd` (L3, TRX_USDT, TP_HIT, idade 53.08h) e `e7c7ab31-c814-4cac-9f0f-d6b8a4a26e1e` (L3_REJECTED, KAS_USDT, TIMEOUT, idade 52.81h). [query]

### 2.2 Veredito de mecanismo (path:line)

De onde vêm os preços de resolução (`shadow_trade_monitor.py`):
- **Live-close a preço corrente** (`:982-1012`): `market_metadata`/último OHLCV; `exit_price = sl|tp` mas `exit_ts` = timestamp da fonte corrente → `holding ≈ idade`.
- **Force-close** (`:1014-1052`): preço corrente, marcado `force_closed`.
- **Timeout-elapsed** (`:1058-1108`): preço corrente quando `elapsed ≥ timeout_candles` e não há candles 1m.
- **Replay histórico** (scan candle-a-candle `:1132-1234` com `exit_ts = c["time"]` histórico) — **caminho MORTO em produção**: `_fetch_candles` (`:199-216`) busca `timeframe='1m'` e não existe candle 1m no banco:
```
 timeframe |   n    |         first          |          last
-----------+--------+------------------------+------------------------
 30m       |  28869 | 2026-06-20 20:00:00+00 | 2026-07-04 13:00:00+00
 5m        | 235174 | 2026-06-25 03:50:00+00 | 2026-07-04 14:00:00+00
```
Confirmação empírica — todas as 76 resoluções de 07-03 com idade >24h têm `holding ≈ idade` (fechamento a preço corrente, sem replay):
```
   source    | outcome | n  | avg_holding_h | avg_idade_h
-------------+---------+----+---------------+-------------
 L1_SPECTRUM | TIMEOUT |  1 |         24.21 |       24.14
 L1_SPECTRUM | TP_HIT  |  2 |         28.01 |       28.00
 L3          | TP_HIT  |  9 |         30.79 |       30.75
 L3_REJECTED | TIMEOUT | 27 |         25.62 |       25.53
 L3_REJECTED | TP_HIT  | 37 |         28.63 |       28.57
```
**Aplicando os critérios do prompt:** não houve replay retroativo, mas também não houve drenagem em massa de trades antigos com labels-artefato — a coorte antiga resolvida é marginal (idades 24–31h, detecção tardia de ~1–7h pelo tick do monitor). Todos têm `holding > 4h` ⇒ label v2 (`is_tp_4h_v2_sim_outcome`) = 0 correto por construção. **Labels VÁLIDOS; nenhuma regra `ml_exclude_stale_resolution` necessária** (a condição de disparo — outcome/holding artefatos em massa — não se materializou).

### 2.3 Impacto no dataset elegível e base rate v2

L1_SPECTRUM elegível (filtro do trainer: pós-fronteira + 30d + outcome resolvido):

| Recorte | Elegíveis | Positivos v2 (TP_HIT ≤4h) | Base rate v2 |
|---|---|---|---|
| ANTES (completed_at < 2026-07-03) | 2075 | 655 | **31.57%** |
| AGORA (2026-07-04 ~14:30 UTC) | 2349 | 741 | **31.55%** |

Base rate estável (Δ = −0.02 p.p.) — consistente com drenagem inexistente; crescimento é orgânico. Zero labels-1 adicionados por drenagem. [query]

---

## F3 — Lista de exclusão H4

### 3.1 Config gravada

```sql
-- RETURNING:
--                 proposta                | apply
-- ----------------------------------------+-------
--  ["trend_alignment", "ema50_gt_ema200"] | true
```
Chaves gravadas em `config_profiles (config_type='ml')`: `ml_feature_exclusion_candidates_proposed=["trend_alignment","ema50_gt_ema200"]`, `ml_feature_exclusion_apply=true`, `ml_feature_exclusion_rationale` (texto completo na config).

Critério atendido (H4, pre-retreino): AUC univariada cruzou 0,5 entre junho e julho — `trend_alignment` 0.5351→0.4237, `ema50_gt_ema200` 0.5277→0.4320 — inversão de sinal com mecanismo plausível (features de regime de tendência; junho trending → julho lateral). `bb_width` FORA: decaiu 0.6628→0.5597 sem inverter (permanece em observação).

### 3.2 Consumo no builder (código aditivo, zero literal de feature em código)

`backend/app/services/ml_challenger_service.py:1273-1289` — em `train_challengers`, após carregar `ml_config`: lê `ml_feature_exclusion_candidates_proposed` e, SOMENTE se `ml_feature_exclusion_apply=true`, filtra `lgbm_feature_columns` (lane L1_SPECTRUM/LightGBM — escopo da evidência H4; lane CatBoost/L3 permanece intocada e congelada). Consumido em `:1313` (`self._build_dataset(lgbm_records, lgbm_feature_columns, ...)`). Nomes de features vivem exclusivamente em config; decisão reversível com `ml_feature_exclusion_apply=false`.

Feature_count esperado no retreino nº 2: **33 − 2 = 31**.

Verificação: `python -m py_compile backend/app/services/ml_challenger_service.py` → OK. Testes de config fail-closed: `pytest backend/tests/test_ml_dataset_config.py -q` → `3 passed`.

### 3.3 Aviso epistêmico (registrado também na config)

Com n=702 (julho) e IC ±0,04–0,05, a inversão é limítrofe. A exclusão é aposta conservadora de variância (2 features a menos em regime de dados escassos), **não** veredito definitivo sobre as features. Reavaliar com o dataset do marco.

---

## F4 — Checklist de prontidão

| Item | Estado | Prova |
|---|---|---|
| Fronteira fail-closed | ✅ | H1 provado (parser compartilhado `app/ml/dataset_config.py`; abort em config ausente). Re-teste hoje: `test_ml_dataset_config.py` → `3 passed`. Count atual elegíveis = **2349** [query] |
| Zero modelos `active` | ✅ | `SELECT status, COUNT(*) FROM ml_models GROUP BY 1` → `candidate 31, rejected 17, retired 17` — **zero active**. Loader `NoEligibleModelError` provado em H2 |
| F1 aplicado | ✅ | config=48 (RETURNING colado acima); dano janela 18h = 2 trades (ids em §1.3) |
| F2 veredito | ✅ | Coorte inexistente (reconciliação 664+25+174+1=864); labels válidos; nenhuma regra de exclusão necessária |
| F3 aplicado | ✅ | Proposta+apply=true gravados (RETURNING colado); consumo em `ml_challenger_service.py:1273-1289,1313`; feature_count esperado 31 |
| Contagem elegível final + base rate v2 | ✅ | **2349** elegíveis / base rate v2 **31.55%** (741 positivos) [query] |
| Marco de retreino | ✅ gravado / ❌ não atingido | `ml_retrain_min_eligible_rows=2800` (RETURNING `2800`). **2349 < 2800 → F5 NÃO executa** |

### Projeção do marco

Déficit: 2800 − 2349 = **451 elegíveis**. Entrada diária de elegíveis L1 (por `created_at`, últimos dias completos):
```
 2026-06-28 |  85 | 2026-06-29 | 224 | 2026-06-30 | 166
 2026-07-01 | 329 | 2026-07-02 | 255 | 2026-07-03 | 175
```
Média observada 6 dias = 1234/6 ≈ **206/dia** [calc] → marco em ~2,2 dias ≈ **2026-07-06**. Com a premissa conservadora do prompt (~109/dia) → ~4,1 dias ≈ **2026-07-08**.

**Data projetada do marco: entre 2026-07-06 e 2026-07-08.**

---

## F5 — Retreino nº 2: NÃO EXECUTADO (AGUARDANDO MARCO)

Conforme F4: elegíveis (2349) < marco (2800). Nenhum treino, nenhuma avaliação de test set, nenhum consumo do holdout. A tabela de veredito pré-registrada permanece **imutável e não consumida** — será julgada uma única vez, no retreino nº 2, quando o marco for atingido.

Regime do retreino nº 2 (inalterado, para a próxima sessão): retrato obrigatório do dataset antes do treino (funil de cortes, base rate por split, ranges temporais, coverage das 31 features, 10 amostras verbatim, dataset_hash), lane L1_SPECTRUM LightGBM, label v2, NaN nativo, threshold EV em validação, Optuna reduzido (`ml_optuna_max_trials=15` — gravado nesta sessão, RETURNING `15`; consumo pelo runner de retreino + ranges conservadores a registrar na sessão do treino), test intocado até avaliação única, gate fail-closed, promoção somente via `_transition_model_status`, `ml_forward_scoring_enabled` permanece `false`.

---

## Estado final dos componentes

| Componente | Estado | Prova |
|---|---|---|
| Fronteira `ml_dataset_valid_from` | fail-closed, `2026-06-14 21:33:10.277143+00` | config colada; parser compartilhado; 3 testes passando |
| Label | v2 `is_tp_4h_v2_sim_outcome`, net of fees | config: `ml_label_version`, `ml_label_net_of_fees=true`, `ml_fee_roundtrip_pct=0.20` |
| Contrato de dados (E7) | ativo por lane | config `ml_feature_contract` (L1: 25 required/8 optional) |
| Split temporal | purge+embargo 14400s | config `ml_split_embargo_seconds=14400` |
| Gate de promoção | fail-closed: AUC≥0.60, gap≤0.05, FPR≤0.50, net EV>0, samples≥300 | config `ml_promotion_*`; E9 provou REJECTED com razões |
| Modelos | **0 active** / 31 candidate / 17 rejected / 17 retired | query colada em F4 |
| Forward scoring | **desligado** | config `ml_forward_scoring_enabled=false` |
| Force-close | 48h / TIMEOUT_LAST_KNOWN_PRICE | config colada (F1); marcador em `exit_metrics_json` |
| Exclusão de features | 2 propostas, apply=true, escopo lane L1 | config + `ml_challenger_service.py:1273-1289` |
| Marco de retreino | 2800 elegíveis (atual: 2349) | config `ml_retrain_min_eligible_rows` |

## Pendências herdadas pela próxima fase

1. **Executar retreino nº 2 ao atingir o marco (~2026-07-06 a 07-08)** e julgar contra a tabela pré-registrada — sem renegociação.
2. `ml_optuna_max_trials=15` já gravado; implementar o consumo no runner de retreino e registrar os ranges conservadores usados.
3. Go-live do scoring (`ml_forward_scoring_enabled=true`) — decisão humana, somente se APPROVED.
4. Pesquisa de features (~60 chaves ignoradas do snapshot + microestrutura) — se veredito for SINAL INSUFICIENTE.
5. Cadência walk-forward de retreino — desenho pendente.
6. Versionamento de profiles + avaliador de política (fase Auto-Pilot).
7. Upgrade B do backfill — evidência arquivada.
8. Descongelamento L3 — condições registradas no E9.
9. **Higiene**: 664 L3_LAB `CANCELLED` de jun/17-18 seguem `outcome IS NULL` (inofensivos — fora do monitor e do dataset — mas poluem contagens `outcome IS NULL`; qualquer auditoria futura de "abertos" DEVE filtrar `status IN ('PENDING','RUNNING')`).
10. **Testes desatualizados**: `test_ml_correction_plan_june30.py` tem 7 falhas pré-existentes (verificado no HEAD sem as mudanças desta sessão) — testes escritos para o plano de 30/06 não atualizados para label v2.

## Ledger de evidências

| NÚMERO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| timeout nominal = 24h | [código+query] | `SHADOW_TIMEOUT_CANDLES ... "1440"` (`shadow_trade_service.py:75`); `1440 | 50867` |
| config nova = 48 | [query UPDATE RETURNING] | `novo_valor: 48` |
| dano janela 18h = 2 | [query] | ids `7704ee7a...` (23.4778h), `8563eeb8...` (21.9440h) |
| 864 = 664+25+174+1 | [query] | tabela de reconstrução colada em F2 |
| coorte prompt = 2 | [query] | `c3514aa3...`, `e7c7ab31...` |
| sem candles 1m | [query] | timeframes: `30m | 28869`, `5m | 235174` |
| holding ≈ idade (76 tardios) | [query] | ex.: `28.01` vs `28.00` h |
| elegíveis = 2349; base rate 31.55% | [query] | `2349 | 741 | 31.55` |
| antes: 2075; 31.57% | [query] | `2075 | 655 | 31.57` |
| marco = 2800 | [query UPDATE RETURNING] | `marco: 2800` |
| zero active | [query] | `candidate 31, rejected 17, retired 17` |
| inflow ≈ 206/dia | [calc] | (85+224+166+329+255+175)/6 = 205.7 — insumos colados |
| feature_count esperado = 31 | [calc] | 33 (H1 `feature_count=33`) − 2 propostas |
