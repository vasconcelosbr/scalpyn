# ============================================================
# Scalpyn — Refactor Structural-30m
# Checklist de migração + estratégia de rollback
# ============================================================

## Resumo do que muda

| Arquivo                          | Tipo de mudança                          |
|----------------------------------|------------------------------------------|
| collect_structural_30m.py        | NOVO — coletor 30m (substitui OHLCV 1h) |
| compute_indicators.py            | PATCH — adiciona compute_30m + depreca compute 1h |
| collect_market_data.py           | PATCH — remove loop OHLCV + chain de collect_all |
| celery_app.py                    | PATCH — 4 pontos: include, routes, annotations, beat |
| docker-compose.yml               | FIX — corrige queue do celery_worker      |

---

## Ordem de aplicação (obrigatória)

### Passo 1 — Criar novo arquivo
```
backend/app/tasks/collect_structural_30m.py
```
Copiar integralmente o conteúdo de `collect_structural_30m.py` deste entregável.
Não depende de nenhuma outra mudança — pode ser criado antes de qualquer patch.

### Passo 2 — Patch compute_indicators.py
Aplicar os 4 sub-patches de `compute_indicators_PATCH.py`:
1. `_derive_min_candles`: `"5m"` → `("5m", "30m")`
2. Inserir `_compute_30m_async()` após `_compute_5m_async`
3. Inserir wrapper `compute_30m()` com `@celery_app.task`
4. Deprecar `compute()` (1h path): remover chain para score, manter como stub

### Passo 3 — Patch celery_app.py
Aplicar os 4 sub-patches de `celery_app_PATCH.py`:
1. `include[]`: adicionar `"app.tasks.collect_structural_30m"`
2. `TASK_ROUTES`: adicionar rotas das 2 novas tasks
3. `TASK_ANNOTATIONS`: adicionar guards para as 2 novas tasks
4. `beat_schedule`: adicionar `collect_structural_30m_candle_close`

### Passo 4 — Patch collect_market_data.py
Aplicar os 3 sub-patches de `collect_market_data_PATCH.py`:
1. Substituir `_inner()` em `_collect_all_async()` pela versão ticker-only
2. Remover chain `compute_indicators.compute` do wrapper `collect_all()`
3. Remover imports órfãos

### Passo 5 — Fix docker-compose.yml
```yaml
# celery_worker:
command: celery -A app.tasks.celery_app worker --loglevel=info --concurrency=4 -Q microstructure,structural,execution
```

### Passo 6 — Rodar teste de invariantes
```bash
cd backend
pytest tests/test_celery_routing_invariants.py -v
```
Deve passar sem erros. Se falhar, a task nova não está no TASK_ROUTES.

---

## Validações pós-deploy (em ordem)

### Imediatas (primeiros 5 minutos)

```bash
# 1. Confirmar que collect_all ainda roda (ticker path)
redis-cli get scalpyn:last_collect_all_start

# 2. Confirmar que collect_structural_30m foi agendado pelo beat
# Aguardar o próximo minuto :00 ou :30 UTC e verificar nos logs:
# [STRUCTURAL-30m] Starting 30m OHLCV collection...
```

### Após o primeiro ciclo completo (30-35 minutos)

```sql
-- 3. OHLCV 30m está sendo persistido
SELECT COUNT(*), MAX(time)
FROM ohlcv
WHERE timeframe = '30m'
  AND time > now() - interval '1 hour';
-- Espera: COUNT > 0, MAX(time) dentro dos últimos 35 minutos

-- 4. Indicadores 30m estão sendo computados
SELECT COUNT(*), MAX(time)
FROM indicators
WHERE timeframe = '30m'
  AND time > now() - interval '1 hour';
-- Espera: COUNT > 0

-- 5. Metadata ainda está fresca (collect_all @ 60s continua)
SELECT symbol, last_updated
FROM market_metadata
WHERE symbol = 'BTC_USDT';
-- Espera: last_updated < 90 segundos atrás

-- 6. OHLCV 1h NÃO está sendo inserido mais
SELECT MAX(time)
FROM ohlcv
WHERE timeframe = '1h';
-- Espera: timestamp ANTERIOR ao deploy (linha deve estar congelada)
```

### Monitoramento 7 dias (baseline de qualidade de sinal)

```sql
-- 7. Volume de ALLOW decisions (sinais aprovados) vs baseline
SELECT
    DATE_TRUNC('day', created_at) AS day,
    COUNT(*) AS allow_count
FROM decision_log
WHERE decision = 'ALLOW'
  AND created_at > now() - interval '14 days'
GROUP BY 1
ORDER BY 1;
-- Espera: aumento < 2x vs semana anterior.
-- Se > 2x: thresholds precisam de recalibração para 30m.
```

---

## Estratégia de rollback

### Rollback rápido (< 5 minutos, sem deploy)

Editar `celery_app.py` beat_schedule:

```python
# 1. Remover entrada collect_structural_30m_candle_close
# 2. Restaurar chain no collect_all (collect_market_data.py)
# 3. Restart celery_beat
```

O `compute_30m` e `collect_structural_30m` podem ficar no código — sem entrada no beat e sem enqueue, simplesmente não rodam.

### Rollback completo (se OHLCV 30m precisar ser limpo)

```sql
-- Remover candles 30m da janela de teste
DELETE FROM ohlcv WHERE timeframe = '30m' AND time > 'DATA_DO_DEPLOY';
DELETE FROM indicators WHERE timeframe = '30m' AND time > 'DATA_DO_DEPLOY';
```

---

## Riscos residuais

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Thresholds RSI/ADX calibrados em 1h geram mais falsos positivos em 30m | Média | Monitorar decision_log por 7 dias |
| collect_structural_30m timeout com pool > 150 símbolos | Baixa | time_limit=600s; reavaliar se pool crescer |
| indicators_provider lê timeframe errado após migração | Baixa | Validar query em get_merged_indicators antes do deploy |
| Beat clock drift faz collect_structural_30m rodar com candle parcial | Muito baixa | crontab UTC-aligned elimina drift; Gate entrega dado fechado em < 3s |

---

## Redução de carga esperada

| Métrica | Antes | Depois |
|---|---|---|
| Chamadas OHLCV Gate/hora | ~5.700 (95 sym × 60 ciclos) | ~190 (95 sym × 2 ciclos) |
| UPSERT market_metadata/hora | ~30.000 rows | ~2.000 rows (ticker) |
| Execuções collect_all/hora | 60 | 60 (inalterado — ticker only) |
| Execuções structural OHLCV/hora | 60 | 2 |
| Latência máxima estrutural | ~10min (dedup efetivo) | 30min (exato, previsível) |
| Sincronização com candle Gate | Não | Sim (crontab UTC) |
| Responsividade dashboard (metadata) | 60s | 60s (inalterado) |
