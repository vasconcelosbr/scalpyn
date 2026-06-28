# RELATÓRIO — CORREÇÃO DE CONTRATOS DE MÉTRICAS: PROFILE INTELLIGENCE vs SHADOW PORTFOLIO

**Data:** 2026-06-28  
**Prompt base:** `PROMPT_FIX_PI_METRIC_CONTRACTS_EXPLICIT_SOURCES_2026-06-28.md`  
**Estágio inicial:** `PI_SCREENS_DIVERGE_BY_DESIGN_DIFFERENT_DATA_CONTRACTS`  
**Estágio final:** `PI_METRIC_CONTRACTS_EXPLICIT_AND_UI_NOT_MISLEADING`  
**Commit:** `2556973`

---

## 1. Resumo Executivo

As telas do Profile Intelligence exibiam métricas com rótulos parecidos mas calculadas sobre fontes, períodos e agregações completamente diferentes. A correção implementa uma camada de **contrato de métricas explícito** em backend e frontend:

1. **Novo bloco "Performance do Portfólio Shadow"** na Calibration Evolution — mostra Win Rate e P&L trade-level (shadow_trades L3+L3_LAB) para não ser confundido com os buckets de indicadores (-35.21%)
2. **Rótulos renomeados** — "Win Rate Média" → "Win Rate Buckets (48h)", "P&L Médio" → "P&L Buckets (48h)", "Sugestões Pendentes" → "Sugestões Registradas" + "Pendentes Validação"
3. **Snapshot indicator** no Overview — "Win Rate Base" agora mostra sub `trade-level · 7d · 28/06/26 14:45` e todos os cards de snapshot têm sub "snapshot do run"
4. **`metric_contracts`** em ambos os endpoints — cada métrica documenta `source_table`, `aggregation`, `window`, `not_comparable_with`, `warning`
5. **`SHADOW_SOURCE_CONTRACT`** — mapeamento canônico dos 5 sources para tab/view/purpose/sql_filter
6. **16 testes novos** — todos passando

**Zero mutações. Zero live trading. Zero profiles criados.**

---

## 2. Fase 0 — Safety Precheck

| Check | Valor | Status |
|---|---|---|
| live_enabled | 0 | ✓ PASS |
| live_orders | 0 | ✓ PASS |
| active_new_models_24h | 0 | ✓ PASS |
| production_mutations_24h | 0 | ✓ PASS |
| ML_GATE_ENABLED | false | ✓ PASS |

---

## 3. Root Causes corrigidas

### RC-1 — Overview é snapshot de run, mas UI parecia live

**Fix:**
- `profile_intelligence.py` overview retorna `last_run_lookback_days` (campo plano)
- Retorna `snapshot_info: {is_snapshot: true, source_table: "profile_intelligence_runs", fields_from_snapshot: [...], note: "..."}`
- Retorna `metric_contracts.win_rate_base` com `is_snapshot=True`, `snapshot_computed_at`
- Frontend: cards "Profiles Analisados" e "Trades Fechados" ganham sub `"snapshot do run"`
- Frontend: "Win Rate Base" ganha sub dinâmica: `trade-level · 7d · 28/06/26 14:45`

### RC-2 — Sugestões com rótulos divergentes

**Fix:**
- Overview → "Sugestões do Run" (era "Sugestões Pendentes") com sub `profile_suggestions · legada`
- Overview → "Alta Confiança (PI)" com sub `profile_suggestions` 
- Calibration → "Sugestões Registradas" (era "Sugestões Pendentes") com sub `all-time · total`
- Calibration → novo card "Pendentes Validação" mostrando `suggestions.pending_shadow_validation`
- Backend retorna `suggestions.pending_shadow_validation` e `suggestions.shadow_applied` no summary
- `metric_contracts.suggestions_registered.not_comparable_with = ["overview.run_suggestions_pending"]`
- `metric_contracts.suggestions_registered.warning` documenta a diferença entre as tabelas

### RC-3 — Win Rate 40.4% vs 28.6%: agregações incomparáveis

**Fix:**
- "Win Rate Média" → **"Win Rate Buckets (48h)"** com sub `média por bucket · não é WR do portfólio`
- Hint: "AVG(win_rate) sobre rows de profile_indicator_performance 48h. Média SIMPLES por bucket — inclui buckets com win_rate=0. NÃO COMPARAR com Win Rate trade-level acima."
- `metric_contracts.bucket_avg_win_rate.not_comparable_with = ["calibration.portfolio_win_rate", "overview.run_snapshot_win_rate", "shadow.trade_level_win_rate"]`

### RC-4 — P&L -35.21% é média de buckets, não P&L do portfólio

**Fix (maior risco de decisão errada):**
- "P&L Médio" → **"P&L Buckets (48h)"** com sub `média por bucket · não é P&L do portfólio`
- Hint: "AVG(avg_pnl_pct) sobre rows de profile_indicator_performance 48h. Inclui buckets com avg_pnl_pct=-1.0 (100% SL no bucket). NÃO COMPARAR com P&L Médio/Trade acima."
- Novo bloco "Performance do Portfólio Shadow" acima exibe o P&L REAL (-14.27%) ao lado para comparação
- `metric_contracts.bucket_avg_pnl_pct.warning` documenta explicitamente que não é P&L do portfólio

### RC-5 — Shadow Portfolio sem source contract explícito

**Fix:**
- Novo `SHADOW_SOURCE_CONTRACT` dict em `metric_contracts.py` mapeando os 5 sources
- Calibration Evolution agora retorna `portfolio_metrics.portfolio_views = ["Aprovados (L3)", "Strategy Lab"]`
- UI: sub de cada card do bloco portfolio mostra `"L3 + L3_LAB"`
- Shadow Portfolio tabs (tooltip/info) — contratos anotados nos hints de cada card

---

## 4. Arquivos alterados

| Arquivo | Mudanças | Linhas |
|---|---|---|
| `backend/app/services/metric_contracts.py` | CRIADO — `SHADOW_SOURCE_CONTRACT` + `build_metric_contract()` | +96 |
| `backend/app/api/calibration_evolution.py` | Add `portfolio_metrics`, `suggestions.pending_shadow_validation`, `suggestions.shadow_applied`, `metric_contracts` | +120 |
| `backend/app/api/profile_intelligence.py` | Add `last_run_lookback_days`, `snapshot_info`, `metric_contracts` | +48 |
| `frontend/app/profile-intelligence/page.tsx` | Overview cards renomeados + subs, Calibration bloco portfolio + labels renomeados, `PIOverview.last_run_lookback_days` | +100, -30 |
| `backend/tests/test_metric_contracts.py` | CRIADO — 16 testes | +155 |

---

## 5. Contrato de métricas implementado

### 5.1 Overview endpoint

```json
{
  "last_run_lookback_days": 7,
  "snapshot_info": {
    "is_snapshot": true,
    "source_table": "profile_intelligence_runs",
    "run_at": "2026-06-27T14:45:52",
    "lookback_days": 7,
    "fields_from_snapshot": ["total_profiles_analyzed", "total_closed_trades", "base_win_rate"],
    "note": "These fields are computed at run time and frozen."
  },
  "metric_contracts": {
    "win_rate_base": {
      "metric_id": "overview.run_snapshot_win_rate",
      "is_snapshot": true,
      "snapshot_computed_at": "2026-06-27T14:45:52",
      "aggregation": {"type": "trade_level", "formula": "TP_HIT/(TP+SL+TIMEOUT)"},
      "not_comparable_with": ["calibration.bucket_avg_win_rate"],
      "warning": "Snapshot congelado do último run."
    },
    "suggestions_pending": {
      "metric_id": "overview.run_suggestions_pending",
      "source_table": "profile_suggestions",
      "filters": {"status": "pending_user_approval"},
      "not_comparable_with": ["calibration.suggestions_registered"],
      "warning": "Tabela legada do PI Engine. DIFERENTE de profile_adjustment_suggestions."
    }
  }
}
```

### 5.2 Calibration Evolution endpoint

```json
{
  "suggestions": {
    "total": 1219,
    "registered": 1219,
    "pending_shadow_validation": 0,
    "shadow_applied": 1219,
    ...
  },
  "portfolio_metrics": {
    "sources": ["L3", "L3_LAB"],
    "portfolio_views": ["Aprovados (L3)", "Strategy Lab"],
    "period": "all-time",
    "aggregation": "trade-level",
    "completed_trades": 12984,
    "wins": 5189,
    "win_rate": 0.3996,
    "avg_pnl_pct": -0.142690,
    "pnl_total_usdt": -18526.82,
    "profiles_count": 45
  },
  "metric_contracts": {
    "portfolio_win_rate": {
      "metric_id": "calibration.portfolio_win_rate",
      "source_table": "shadow_trades",
      "comparable_with": ["shadow.trade_level_win_rate", "overview.run_snapshot_win_rate"],
      "not_comparable_with": ["calibration.bucket_avg_win_rate"]
    },
    "bucket_avg_win_rate": {
      "metric_id": "calibration.bucket_avg_win_rate",
      "source_table": "profile_indicator_performance",
      "window": {"label": "48h", "window_hours": 48},
      "warning": "Média simples por bucket. NÃO representa a performance do portfólio."
    },
    "bucket_avg_pnl_pct": {
      "metric_id": "calibration.bucket_avg_pnl_pct",
      "warning": "NÃO é o P&L do portfólio. Inclui buckets com avg_pnl_pct=-1.0."
    },
    "suggestions_registered": {
      "metric_id": "calibration.suggestions_registered",
      "not_comparable_with": ["overview.run_suggestions_pending"],
      "warning": "Diferente de profile_suggestions (PI Engine legado)."
    }
  }
}
```

---

## 6. Source Map das abas do Shadow Portfolio

| Source | Tab Shadow Portfolio | Descrição | Propósito |
|---|---|---|---|
| `L3` | Aprovados (L3) | Decisões ALLOW do L3 — trades que passaram pelo filtro | Medir o que o L3 deixaria operar em produção |
| `L3_REJECTED` | Rejeitados (L3) | Decisões BLOCK/REJECT do L3 — trades bloqueados | Medir oportunidades descartadas pelo filtro |
| `L3_SIMULATED` | Simulados (L3) | Universo contrafactual sem filtro ALLOW/BLOCK | Comparar com e sem filtro L3 |
| `L1_SPECTRUM` | Dataset ML (L1) | Captura bruta do scanner L1 antes das regras L3 | Dataset de treino/validação do L1 |
| `L3_LAB` | Strategy Lab | Watchlists experimentais e combinações | Testar hipóteses antes de promover ao L3 |

---

## 7. Labels alterados

| Tela | Label antigo | Label novo | Fonte | Agregação | Comparável com |
|---|---|---|---|---|---|
| Overview | Win Rate Base | Win Rate Base (snapshot + sub dinâmica) | profile_intelligence_runs | trade-level | shadow.trade_level_win_rate |
| Overview | Sugestões Pendentes | Sugestões do Run | profile_suggestions | COUNT(pending) | — |
| Overview | Alta Confiança | Alta Confiança (PI) | profile_suggestions | COUNT(HIGH) | — |
| Overview | Profiles Analisados | Profiles Analisados (sub: snapshot do run) | profile_intelligence_runs | COUNT(DISTINCT) | — |
| Overview | Trades Fechados | Trades Fechados (sub: snapshot do run) | profile_intelligence_runs | COUNT | — |
| Calibration | Sugestões Pendentes | Sugestões Registradas | profile_adjustment_suggestions | COUNT(*) all-time | — |
| Calibration | — | Pendentes Validação (NOVO CARD) | profile_adjustment_suggestions | COUNT(PENDING_SHADOW_VALIDATION) | — |
| Calibration | Win Rate Média | Win Rate Buckets (48h) | profile_indicator_performance | AVG(win_rate) por bucket | — |
| Calibration | P&L Médio | P&L Buckets (48h) | profile_indicator_performance | AVG(avg_pnl_pct) por bucket | — |

---

## 8. Cards novos/alterados

### Novo bloco "Performance do Portfólio Shadow" (Calibration Evolution)

5 cards com borda verde (`border-emerald-500/20 bg-emerald-500/5`), acima do bloco de indicadores:

| Card | Valor atual | Fonte | Agregação |
|---|---|---|---|
| Win Rate (trade-level) | 39.96% | shadow_trades L3+L3_LAB COMPLETED | TP_HIT / total |
| P&L Médio / Trade | -14.27% | shadow_trades L3+L3_LAB COMPLETED | AVG(pnl_pct) |
| P&L Total USDT | -$18527 | shadow_trades L3+L3_LAB COMPLETED | SUM(pnl_usdt) |
| Trades Fechados | 12984 | shadow_trades L3+L3_LAB COMPLETED | COUNT |
| Profiles | 45 | shadow_trades L3+L3_LAB COMPLETED | COUNT(DISTINCT profile_id) |

---

## 9. Endpoints alterados

| Endpoint | Campos novos |
|---|---|
| `GET /api/profile-intelligence/overview` | `last_run_lookback_days`, `snapshot_info`, `metric_contracts` |
| `GET /api/profile-intelligence/calibration-evolution/summary` | `portfolio_metrics`, `suggestions.pending_shadow_validation`, `suggestions.shadow_applied`, `suggestions.registered`, `metric_contracts` |

---

## 10. Testes

Arquivo: `backend/tests/test_metric_contracts.py`

| Teste | Status |
|---|---|
| test_shadow_source_contract_maps_all_five_tabs | PASSED |
| test_shadow_source_contract_has_required_fields | PASSED |
| test_shadow_source_contract_l3_is_approved_tab | PASSED |
| test_shadow_source_contract_l3_rejected_is_rejected_tab | PASSED |
| test_shadow_source_contract_l3_lab_is_strategy_lab | PASSED |
| test_shadow_source_contract_l1_spectrum_is_dataset_ml | PASSED |
| test_build_metric_contract_returns_required_fields | PASSED |
| test_build_metric_contract_snapshot_flag | PASSED |
| test_build_metric_contract_window_present_when_label_given | PASSED |
| test_build_metric_contract_warning_propagated | PASSED |
| test_build_metric_contract_no_window_when_no_label | PASSED |
| test_bucket_win_rate_not_comparable_with_trade_level | PASSED |
| test_bucket_pnl_not_comparable_with_portfolio_pnl | PASSED |
| test_suggestions_registered_not_comparable_with_overview_pending | PASSED |
| test_metric_contracts_module_has_no_write_sql | PASSED |
| test_shadow_source_contract_sql_filters_are_read_only | PASSED |
| **Total** | **16/16 PASSED** |

TypeScript: `tsc --noEmit` exit 0 (sem erros).

---

## 11. Validação SQL

### I.1 Trade-level (portfolio_metrics)

```
completed_trades=12984, wins=5189, win_rate=0.3996, avg_pnl_pct=-0.14269, pnl_total=-18526.82, profiles=45
```

### I.2 Bucket-level (indicadores)

```
bucket_rows=13109, profiles=40, indicators=4, bucket_avg_win_rate=0.2860, bucket_avg_pnl_pct=-0.351695
```

**Divergência confirmada:** 39.96% (trade-level) vs 28.60% (bucket-level) · -14.27% (trade-level) vs -35.17% (bucket-level)

### I.3 Suggestions

```
SHADOW_APPLIED: 1219, high_conf=955
```

---

## 12. Deploy

```
commit: 2556973
git push → origin/main: 9249be7..2556973
```

---

## 13. Safety Final

| Check | Valor | Status |
|---|---|---|
| live_enabled | 0 | ✓ PASS |
| live_orders | 0 | ✓ PASS |
| active_new_models_24h | 0 | ✓ PASS |
| production_mutations_24h | 0 | ✓ PASS |
| ML_GATE_ENABLED | false | ✓ PASS |

---

## 14. Checklist

| Contrato | Status | Evidência |
|---|---|---|
| Overview deixa claro que é snapshot | PASS | `snapshot_info.is_snapshot=true` + sub "snapshot do run" nos cards |
| Win Rate trade-level separado de bucket-level | PASS | Bloco "Performance do Portfólio Shadow" vs "Diagnóstico de Indicadores" |
| P&L de bucket não parece P&L do portfólio | PASS | Labels renomeados + `metric_contracts.bucket_avg_pnl_pct.warning` |
| Calibration mostra performance shadow separada | PASS | Novo bloco portfolio com 5 cards trade-level |
| Sugestões pendentes corrigidas semanticamente | PASS | "Sugestões Registradas" + card "Pendentes Validação" separado |
| Abas Shadow têm source contract | PASS | `SHADOW_SOURCE_CONTRACT` em `metric_contracts.py`, `portfolio_metrics.portfolio_views` |
| AI Critic mostra source/período/filtros | PASS | analysis_context já implementado (sessão anterior, commit 1da169b) |
| Endpoints retornam metric_contracts | PASS | calibration_evolution.py + profile_intelligence.py |
| Tooltips com hint detalhado | PASS | Todos os cards têm `title={card.hint}` com info de fonte/agregação/comparabilidade |
| Safety final | PASS | SQL |

---

## 15. Ledger de Evidências

| Afirmação | Origem | Valor |
|---|---|---|
| portfolio_metrics adicionado | calibration_evolution.py:90-109 | `port_row` query `shadow_trades WHERE source IN ('L3','L3_LAB')` |
| pending_shadow_validation = 0 agora | SQL `GROUP BY status` | `('SHADOW_APPLIED', 1219, 955)` — todos migrados |
| TypeScript sem erros | `tsc --noEmit` | exit 0 |
| 16/16 testes passando | `pytest tests/test_metric_contracts.py` | `16 passed in 0.09s` |
| snapshot_info implementado | profile_intelligence.py:204-218 | dict com `fields_from_snapshot` |
| SHADOW_SOURCE_CONTRACT tem 5 sources | metric_contracts.py | L3, L3_REJECTED, L3_SIMULATED, L1_SPECTRUM, L3_LAB |
| metric_contracts.bucket_avg_pnl_pct.warning | calibration_evolution.py | "NÃO é o P&L do portfólio..." |
| P&L trade-level vs bucket divergência | SQL I.1/I.2 | -14.27% (trade) vs -35.17% (bucket) |
| commit | git log | `2556973` |
| push | git push | `9249be7..2556973` |
| Safety final | SQL | todos 0 |

---

## 16. Veredito

```
PI_METRIC_CONTRACTS_EXPLICIT_AND_UI_NOT_MISLEADING
```

### Justificativa

- **RC-1 (snapshot):** Overview mostra sub "snapshot do run" + `snapshot_info` no endpoint ✓
- **RC-2 (sugestões):** "Sugestões do Run" (profile_suggestions legada) separado de "Sugestões Registradas" (profile_adjustment_suggestions) + card "Pendentes Validação" ✓
- **RC-3 (win rate):** "Win Rate Buckets (48h)" com not_comparable_with guards + bloco trade-level separado ✓
- **RC-4 (P&L):** "P&L Buckets (48h)" com warning explícito + P&L Médio/Trade real (-14.27%) visível no bloco acima ✓
- **RC-5 (abas shadow):** `SHADOW_SOURCE_CONTRACT` com 5 sources → view/tab/purpose/sql_filter ✓
- `metric_contracts` em ambos os endpoints com `not_comparable_with` e `warning` ✓
- 16/16 testes + tsc exit 0 ✓
- Safety final PASS ✓
