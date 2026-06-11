# Smoke Train — Checklist e Instruções
**Data:** 2026-06-11

> **Contrato:** métricas do smoke train são diagnóstico de encanamento.
> AUC entre 0.35–0.65 com n_test ≈ 20–40 é estatisticamente aleatório.
> Nenhuma decisão de produto sai dessas métricas. `ML_GATE_ENABLED` fica `false`.

---

## Pré-requisitos — deploys e migrations

1. Deploy do código deste commit (migration 074 + forward_scorer.py)
2. Rodar `alembic upgrade head` na API (já é automático via start.sh)
3. Verificar que `074_ml_predictions_table` aparece em `alembic_version`

---

## PARTE 0 — Preparação do config_profile type='ml'

Execute no banco (Railway → Postgres → Connect):

```sql
-- Adicionar campos novos ao config ML (idempotente via ||)
UPDATE config_profiles
SET config_json = config_json || '{
    "ml_win_fast_threshold_seconds": 10800,
    "ml_forward_scoring_enabled": false,
    "ml_feature_min_coverage_pct": 0.30
}'::jsonb
WHERE config_type = 'ml' AND is_active = true;

-- Verificar
SELECT config_json FROM config_profiles WHERE config_type = 'ml' AND is_active = true;
```

Se não existir config_profile do tipo 'ml', criar:
```sql
INSERT INTO config_profiles (config_type, is_active, config_json, user_id)
SELECT 'ml', true, '{
    "ml_dataset_valid_from": "2026-06-11T00:00:00+00:00",
    "ml_feature_min_coverage_pct": 0.30,
    "ml_fee_roundtrip_pct": 0.16,
    "ml_label_net_of_fees": false,
    "ml_win_fast_threshold_seconds": 10800,
    "ml_forward_scoring_enabled": false
}'::jsonb, user_id
FROM config_profiles WHERE config_type = 'spot_engine' AND is_active = true LIMIT 1;
```

---

## PARTE 0 — Gates de Pré-Voo (parar no primeiro FAIL)

### Gate 0.1 — Features vivas

```sql
SELECT
    COUNT(*) AS novos,
    COUNT(*) FILTER (WHERE features_snapshot = '{}'::jsonb
                       OR features_snapshot IS NULL) AS vazios,
    ROUND(AVG((features_snapshot->>'_features_coverage')::numeric), 2) AS cobertura_media
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND created_at >= (
      SELECT (config_json->>'ml_dataset_valid_from')::timestamptz
      FROM config_profiles WHERE config_type = 'ml' AND is_active = true
  );
```

**✅ PASS:** `vazios = 0` E `cobertura_media >= 0.80`
**⚠️ OK:** `cobertura_media` entre 0.50–0.80 — prosseguir reportando (filtro dinâmico cobre)
**❌ STOP:** `vazios > 0` — fix de features não pegou em algum caminho de criação

---

### Gate 0.2 — Pureza empírica (distribuição de scores)

```sql
-- Distribuição do score do modelo de scoring (se existir) ou do alpha_score
-- extraído dos features_snapshot dos shadows L1 válidos.
-- Objetivo: confirmar que existem shadows com score ABAIXO do threshold L3.
SELECT
    COUNT(*) AS total,
    ROUND(AVG((features_snapshot->>'rsi')::numeric), 1) AS avg_rsi,
    MIN((features_snapshot->>'rsi')::numeric) AS min_rsi,
    MAX((features_snapshot->>'rsi')::numeric) AS max_rsi,
    COUNT(*) FILTER (WHERE (features_snapshot->>'rsi')::numeric < 30) AS n_oversold,
    COUNT(*) FILTER (WHERE (features_snapshot->>'rsi')::numeric > 70) AS n_overbought
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND features_snapshot != '{}'::jsonb
  AND features_snapshot IS NOT NULL
  AND created_at >= (
      SELECT (config_json->>'ml_dataset_valid_from')::timestamptz
      FROM config_profiles WHERE config_type = 'ml' AND is_active = true
  );
```

**✅ PASS:** RSI cobre toda a faixa (não todos concentrados em RSI alto = aprovados L3)
**❌ STOP:** 100% com RSI > 50 e volume_delta alto → suspeita de censura (verificar `source`)

---

### Gate 0.3 — Volume mínimo

```sql
SELECT
    COUNT(*) AS fechados_validos,
    COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS tp_total,
    COUNT(*) FILTER (WHERE outcome = 'SL_HIT') AS sl_total,
    COUNT(*) FILTER (WHERE outcome = 'TIMEOUT') AS timeout_total,
    COUNT(*) FILTER (
        WHERE outcome = 'TP_HIT'
          AND holding_seconds <= (
              SELECT (config_json->>'ml_win_fast_threshold_seconds')::int
              FROM config_profiles WHERE config_type = 'ml' AND is_active = true
          )
    ) AS win_fast
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND outcome IS NOT NULL
  AND created_at >= (
      SELECT (config_json->>'ml_dataset_valid_from')::timestamptz
      FROM config_profiles WHERE config_type = 'ml' AND is_active = true
  );
```

**✅ PASS:** `fechados_validos >= 100` E `win_fast >= 10`
**❌ STOP:** Abaixo desses valores — reportar projeção de quando o gate abre:

```sql
-- Taxa de fechamento (para projetar data)
SELECT
    COUNT(*) AS total_abertos,
    COUNT(*) FILTER (WHERE outcome IS NOT NULL) AS fechados,
    ROUND(COUNT(*) FILTER (WHERE outcome IS NOT NULL) * 100.0 / NULLIF(COUNT(*), 0), 1) AS pct_fechados,
    MIN(created_at) AS primeiro,
    MAX(created_at) AS ultimo
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND created_at >= (
      SELECT (config_json->>'ml_dataset_valid_from')::timestamptz
      FROM config_profiles WHERE config_type = 'ml' AND is_active = true
  );
```

---

### Gate 0.4 — Leakage check

Confirmar que o builder consome APENAS features T0-safe (do `features_snapshot`):

```sql
-- Colunas presentes no features_snapshot (amostra de 5 registros)
SELECT id, jsonb_object_keys(features_snapshot) AS feature_key
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND features_snapshot != '{}'::jsonb
  AND outcome IS NOT NULL
ORDER BY created_at DESC
LIMIT 5;
```

**✅ PASS (todos T0-safe):**
- `rsi`, `adx`, `macd_histogram`, `ema9`, `ema21`, etc. — capturado em T0
- `_features_coverage`, `_features_captured_at` — metadado de captura

**❌ SUSPEITO (nunca deve aparecer):**
- `pnl_pct`, `outcome`, `holding_seconds` — resultado pós-trade (leakage)
- `score`, `score_raw` — leakage circular (ML_EXCLUDED_FIELDS no extractor)

---

## PARTE 1 — Trigger do Smoke Train (somente após PASS em todos os gates)

### Env vars a configurar no serviço `scalpyn-ml-trainer` (Railway UI)

| Variável | Valor smoke train | Valor atual |
|---|---|---|
| `ML_SOURCE_FILTER` | `L1_SPECTRUM` | `L3` |
| `MIN_RECORDS` | `100` | `200` |
| `N_TRIALS` | `5` | `50` |
| `DAYS_LOOKBACK` | `30` | `30` |
| `ML_TARGET_TYPE` | `binary` | `binary` |

**Configurar e Deployar agora** (Railway UI → scalpyn-ml-trainer → Variables → Deploy)

### Verificar no log do trainer após o run

```
=== Scalpyn ML Trainer Job iniciado ===
Dataset valid_from filter active: created_at >= 2026-06-11T...
shadow_trades L3 finalizados: <N> | ...    ← deve ser L1_SPECTRUM, mas log diz "L3" (cosmético)
DataFrame: <N> rows ...
Base WIN rate: <X>%
...
Modelo v1 registrado e ativado.
=== Trainer Job concluído com sucesso ===
```

---

## PARTE 2 — Ativação do Scoring Forward (após PASS do smoke train)

### 1. Ativar o flag no banco

```sql
-- Ativar scoring forward
UPDATE config_profiles
SET config_json = config_json || '{"ml_forward_scoring_enabled": true}'::jsonb
WHERE config_type = 'ml' AND is_active = true;

-- Confirmar
SELECT config_json->>'ml_forward_scoring_enabled' FROM config_profiles WHERE config_type = 'ml';
```

### 2. Verificar após algumas horas

```sql
-- Predictions populando?
SELECT
    COUNT(*) AS n,
    MIN(scored_at) AS primeiro,
    MAX(scored_at) AS ultimo,
    ROUND(AVG(win_fast_probability)::numeric, 4) AS avg_prob,
    ROUND(STDDEV(win_fast_probability)::numeric, 4) AS stddev_prob
FROM ml_predictions
WHERE shadow_trade_id IS NOT NULL
  AND scored_at >= NOW() - INTERVAL '24 hours';
-- Esperado: n > 0; stddev_prob > 0 (distribuição não degenerada)

-- Distribuição dos scores (verificar se não está tudo ~mesmo valor)
SELECT width_bucket(win_fast_probability, 0, 1, 10) AS bucket,
       COUNT(*) AS n
FROM ml_predictions
WHERE shadow_trade_id IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

---

## Quadro de Progresso para o Treino Real

| Critério | Valor atual | Meta |
|---|---|---|
| Fechados válidos (L1_SPECTRUM) | — | ≥ 500 |
| Classe WIN_FAST | — | 15–85% |
| Leakage audit | — | limpo |
| Forward AUC IC | — | IC 95% excluindo 0.50 |
| `ML_GATE_ENABLED` | `false` | só após todos acima |

Projeção de data de 500 fechados: calcular a partir da taxa/hora do Gate 0.3.

---

## O que NÃO fazer

- NÃO ativar `ML_GATE_ENABLED` — independentemente do AUC do smoke
- NÃO tirar conclusões de edge das métricas (n_test ≈ 20–40)
- NÃO treinar se qualquer gate da Parte 0 falhar
- NÃO modificar captura, sample rate, ou configs do Auto-Pilot
- NÃO restaurar `ML_SOURCE_FILTER=L3` nos workers sem voltar as outras vars
