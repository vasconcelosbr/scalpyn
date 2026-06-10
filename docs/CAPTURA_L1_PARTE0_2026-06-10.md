# Parte 0 — Medição de Volume L1 (2026-06-10)

## Contexto

Decisão de arquitetura: treinar o ML exclusivamente na população L1 (não-filtrada) em vez do
stream L3-ALLOW (censurado). Este relatório mede o volume antes de qualquer código ser escrito.

## Dados coletados

| Métrica | Valor | Fonte |
|---------|-------|-------|
| Beat schedule — `pipeline_scan` | 300s = 12 ciclos/hora | `celery_app.py` linha 468-470 |
| Símbolos ativos no L1 | 26 | `pipeline_watchlist_assets` WHERE level_direction IS NULL |
| Símbolos ativos no L3 | 10 | idem |
| Decisões L3-ALLOW/hora | ~143 (pico 213) | `decisions_log` últimas 27h |
| Shadows L3 criados/hora | avg 17, pico 29 | `shadow_trades` WHERE source='L3' |
| Shadows L3/dia | ~408 | extrapolação 24h |
| Shadow monitor cadência | 300s = 12×/hora | `celery_app.py` linha 537-540 |
| DB pool ceiling | 22 conexões | `docs/db-pool-budget.md` |
| Railway Postgres max_connections | ~400 | PostgreSQL 18 padrão |

## Modelo de evento de promoção L1

`pipeline_watchlist_assets` é uma tabela upserted — uma linha por (watchlist_id, symbol).
Não tem histórico de ciclos. O volume de captura L1_SPECTRUM é calculado como:

```
eventos L1/hora = símbolos_ativos_L1 × ciclos/hora
               = 26 × 12 = 312 eventos/hora
```

Cada par (symbol, scan_cycle) é um evento elegível para amostragem determinística.

## Tabela de cenários

| Rate | Shadows/hora | Shadows/dia | vs L3 atual (~408/dia) |
|------|-------------|-------------|----------------------|
| 5%   | 15.6        | 374         | 0.9×                |
| 10%  | 31.2        | 749         | 1.8×                |
| 25%  | 78.0        | 1,872       | 4.6×                |
| 50%  | 156.0       | 3,744       | 9.2×                |

## Análise de capacidade

### DB pool (docs/db-pool-budget.md)

- Shadows L1 criados inline dentro de `pipeline_scan.scan` (structural worker, 1 slot NullPool)
- Nenhuma task nova por shadow → incremento de conexões: **0**
- Utilização atual: 22/400 = 5.5% → headroom 378 conexões → NO ISSUE

### Fila structural

- `pipeline_scan.scan`: time_limit=600s, soft_time_limit=540s
- Overhead a 10%: 26 × 10% = 2.6 shadows/ciclo × ~50ms = ~130ms por ciclo
- Scan atual: 5-30s estimado → headroom > 500s → NO ISSUE

### Shadow monitor (execution queue)

- Open shadows em steady-state com lifetime médio ~3h:
  - L3 stream atual: 17/h × 3h = ~51 abertos
  - L1_SPECTRUM 10%: +31/h × 3h = ~93 adicionais
  - Total: **~144 shadows abertos por ciclo de monitor**
- Monitor usa NullPool (1 conexão por execução, devolvida logo após)
- Processamento: 1 SELECT batch + checagem TP/SL + raras UPDATEs → dentro de 120s time_limit
- **OBSERVAÇÃO**: o monitor pode precisar de um aumento em `SHADOW_MONITOR_BATCH_SIZE` se o default for < 150. Verificar antes do ramp para 25%.

### Custo Railway

- Shadow row ~200 bytes × 749/dia = ~150KB/dia de storage → negligível
- CPU/RAM: overhead do pipeline_scan é ~130ms por ciclo de 5 min → <0.05% do tempo de compute

## Decisão: sample rate inicial = 10%

### Justificativa

1. **Volume de treino**: 749/dia → após 4 semanas = ~21K registros L1_SPECTRUM. Suficiente
   para XGBoost retraining com boa cobertura (atual dataset L3 tem ~8K registros).
2. **Carga incremental controlada**: 1.8× sobre stream L3 atual, 0 conexões adicionais de DB,
   ~130ms de overhead por ciclo de scan.
3. **Capacidade shadow monitor**: ~144 shadows abertos é manejável; não bloqueia o execution worker.
4. **Purity path**: Em ~30 dias, trainer pode migrar `ML_SOURCE_FILTER = 'L1_SPECTRUM'` e
   eliminar o selection bias do L3.
5. **Ramp path**: 30 dias → verificar qualidade → aumentar para 25% sem mudanças estruturais.

### Plano de ramp

| Semana | Rate | Ação |
|--------|------|------|
| 0-1    | 10%  | Deploy com enabled=false → validar logs de skip |
| 1      | 10%  | enabled=true → observar shadows criados, distribuição de symbols |
| 2-4    | 10%  | Acumular dados, verificar cobertura de símbolos |
| 4      | —    | Audit: todos os 26 símbolos com shadows? Distribuição uniforme? |
| 4-5    | 25%  | Ramp se dados limpos |
| 8+     | —    | Trainer migra source_filter para L1_SPECTRUM |

## Estado dos pré-requisitos para implementação (Parte 1+)

- [x] `config_type='ml'` config_profile no DB (seed aplicado 2026-06-10, id: 4e445c54-...)
- [x] `shadow_trades.source` coluna existente (migration 067)
- [x] `ux_shadow_running_user_symbol` constraint em (user_id, symbol) — precisa ser migrada para (user_id, symbol, source) na Parte 2.5
- [ ] Campos novos no ML config: `shadow_capture_l1_enabled`, `shadow_capture_l1_sample_rate`, etc.
- [ ] Ponto de captura em `pipeline_scan.py` (Parte 2.1)
- [ ] Migration `ux_shadow_running_user_symbol` → (user_id, symbol, source) (Parte 2.5)
- [ ] Tabela `shadow_capture_skips` (Parte 2.6)
