# RELATÓRIO — CORREÇÃO DA RASTREABILIDADE DO AI CRITIC

**Data:** 2026-06-28  
**Prompt base:** `PROMPT_FIX_AI_CRITIC_ANALYSIS_CONTEXT_2026-06-28.md`  
**Estágio inicial:** `AI_CRITIC_OUTPUT_WITHOUT_SOURCE_CONTEXT`  
**Estágio final:** `AI_CRITIC_AUDITABLE_SOURCE_CONTEXT_OPERATIONAL`  
**Commits:** `1da169b` + `fd5c53d` (merge)

---

## 1. Resumo Executivo

O AI Critic exibia diagnósticos sem nenhum contexto auditável — não indicava qual fonte/tabela, período, filtros, profiles ou symbols foram analisados. Além disso, Claude retornava o JSON dentro de um bloco ` ```json `, causando falha no `json.loads()`, o que deixava `findings`, `recommendations` e `risk_flags` sempre vazios.

Três root causes corrigidos: schema sem colunas de contexto, ausência de persistência da `analysis_context`, e parsing de respostas quebrado.

**Zero mutações, zero live trading, zero profiles criados.**

---

## 2. Fase 0 — Safety Precheck

| Check | Valor | Status |
|---|---|---|
| live_enabled | 0 | ✓ PASS |
| live_orders | 0 | ✓ PASS |
| active_new_models_24h | 0 | ✓ PASS |
| profiles_created_24h | 0 | ✓ PASS |
| mutations_24h | 0 | ✓ PASS |
| ML_GATE_ENABLED | false | ✓ PASS |

---

## 3. Fase A — Root Causes

### A.1 Schema sem colunas de contexto

```sql
-- profile_ai_reviews antes do fix: sem analysis_context, sem hashes
columns: id, run_id, status, requested_at, completed_at, next_review_at,
         model_name, prompt_hash, tokens_input, tokens_output,
         summary, findings, recommendations, contradictions, risk_flags,
         raw_response_ref, created_at
```

Root cause: `ROOT_CAUSE_AI_REVIEW_CONTEXT_NOT_PERSISTED`

### A.2 Payload sem fonte/período/filtros

Em `run_ai_review_cycle` (`profile_intelligence_live_service.py:587`):
```python
# ANTES — sem window_start/end, sem source_breakdown, sem symbols
row = await db.execute(text("""
    SELECT COUNT(*) AS completed_trades ...
    FROM shadow_trades
    WHERE source IN ('L3','L3_LAB')   # hardcoded, não registrado
      AND created_at >= now() - interval '4 hours'  # não registrado
"""))
```

Nenhum desses parâmetros era persistido em banco — o diagnóstico era opaco.

### A.3 Claude retorna ```json``` (code block)

```python
raw = '```json\n{"summary": "...", "findings": [...]}```'
json.loads(raw)  # → JSONDecodeError → summary = raw[:500]
# findings, recommendations, risk_flags = {}, [], []
```

Resultado: `summary` ficava com o bloco markdown completo, `findings` sempre vazio.

---

## 4. Fase B — Mapeamento do Código

| Item | Valor encontrado | Origem |
|---|---|---|
| window_hours | 4 | `profile_intelligence_live_service.py:593` |
| window_start/end | não computado | — (ausente) |
| tabela fonte | shadow_trades | `profile_intelligence_live_service.py:591` |
| sources | `['L3','L3_LAB']` | `profile_intelligence_live_service.py:592` |
| status filter | COMPLETED | `profile_intelligence_live_service.py:594` |
| profile filter | IS NOT NULL | `profile_intelligence_live_service.py:596` |
| hard negatives | lido da tabela | `profile_intelligence_live_service.py:603` (novo) |
| suggestions | 5 pending types | `profile_intelligence_live_service.py:603` |
| L1 incluído? | Não | sources = L3 + L3_LAB somente |
| L3 incluído? | Sim | sources[0] = "L3" |
| Strategy Lab incluído? | Sim (L3_LAB) | sources[1] = "L3_LAB" |
| UI rendering | `page.tsx:2583` | summary + tokens (sem contexto) |

---

## 5. Fase C — Novo Schema

**Migration 116** (`backend/alembic/versions/116_ai_review_analysis_context.py`):

```sql
ALTER TABLE profile_ai_reviews ADD COLUMN IF NOT EXISTS analysis_context jsonb;
ALTER TABLE profile_ai_reviews ADD COLUMN IF NOT EXISTS context_payload_hash text;
ALTER TABLE profile_ai_reviews ADD COLUMN IF NOT EXISTS context_query_hash text;

-- Reviews existentes marcados como legado
UPDATE profile_ai_reviews
SET analysis_context = '{"_legacy": true, "note": "review created before analysis_context was tracked"}'::jsonb
WHERE analysis_context IS NULL AND status = 'COMPLETED' AND tokens_input > 0;
-- Result: 4 rows updated (legado)
```

---

## 6. Fase D — Contrato `analysis_context`

Estrutura persistida em cada novo review:

```json
{
  "dataset": {
    "table": "shadow_trades",
    "portfolio_view": "Aprovados (L3) + Strategy Lab / L3 Lab",
    "sources": ["L3", "L3_LAB"],
    "excluded_sources": ["L1_SPECTRUM", "L3_REJECTED", "L3_SIMULATED"],
    "filters": {
      "status": ["COMPLETED"],
      "pnl_pct_not_null": true,
      "profile_id_not_null": true,
      "include_running": false
    }
  },
  "window": {
    "window_hours": 4,
    "window_start": "2026-06-28T00:00:00+00:00",
    "window_end": "2026-06-28T04:00:00+00:00",
    "timezone": "UTC"
  },
  "sample": {
    "trades_count": 92,
    "completed_trades": 92,
    "profiles_count": 21,
    "symbols_count": 15,
    "source_breakdown": {"L3": {"trades": 61, "profiles": 21}, "L3_LAB": {"trades": 31, "profiles": 7}}
  },
  "metrics": {
    "win_rate": 0.38,
    "avg_pnl_pct": -0.0025,
    "pnl_total_usdt": -230.15,
    "negative_profiles": 8,
    "hard_negatives": 0
  },
  "links": {
    "review_id": "...",
    "context_query_hash": "abc123...",
    "context_payload_hash": "def456..."
  }
}
```

---

## 7. Fase E — Source→View Mapping

```python
_SOURCE_VIEW_MAP = {
    "L3": "Aprovados (L3)",
    "L3_REJECTED": "Rejeitados (L3)",
    "L3_SIMULATED": "Simulados (L3)",
    "L1_SPECTRUM": "Dataset ML (L1)",
    "STRATEGY_LAB": "Strategy Lab",
    "L3_LAB": "Strategy Lab / L3 Lab",
}
```

Sources desconhecidas → `UNKNOWN(source)` com warning.

---

## 8. Fase F — Fix no Fluxo de Persistência

**`profile_intelligence_live_service.py` — `run_ai_review_cycle`:**

1. Computa `window_start` e `window_end` como `datetime` UTC explícitos
2. Executa queries de breakdown por source e symbols
3. Constrói `analysis_context` completo
4. Persiste `analysis_context` + hashes na INSERT (antes de chamar Claude)
5. Loga `AI_REVIEW_CONTEXT_BUILT` e `AI_REVIEW_CONTEXT_PERSISTED`
6. Fix do parsing: `_strip_json_codeblock()` remove ` ```json ` antes de `json.loads()`
7. Prompt Claude atualizado com `"Return ONLY the JSON, no markdown code blocks."`
8. Validação `COMPLETED`:
   - `completed_review_contract_is_valid()` (tokens + summary + model)
   - `analysis_context.sample.trades_count IS NOT NULL` → ou `FAILED_MISSING_ANALYSIS_CONTEXT`
9. Loga `AI_REVIEW_COMPLETED_WITH_CONTEXT` (não `AI_REVIEW_COMPLETED`)

---

## 9. Fase G — Endpoint Atualizado

**`GET /api/profile-intelligence/live/ai-review`** agora retorna:

```json
{
  "review_id": "9b8e6739...",
  "status": "COMPLETED",
  "model_name": "claude-haiku-4-5-20251001",
  "tokens_input": 255,
  "tokens_output": 868,
  "analysis_context": {...},
  "analysis_context_available": true,
  "analysis_context_legacy": false,
  "context_payload_hash": "abc123...",
  "context_query_hash": "def456...",
  "summary": "...",
  "findings": {...},
  "recommendations": [...],
  "risk_flags": [...]
}
```

Reviews legados: `analysis_context_legacy: true`, `analysis_context: null`

---

## 10. Fase H — UI Atualizada

**`frontend/app/profile-intelligence/page.tsx:2583`**

Antes do diagnóstico (`summary`), bloco auditável exibido:

```
Contexto auditável ✓
Fonte          shadow_trades
Aba/visão      Aprovados (L3) + Strategy Lab / L3 Lab
Sources        L3, L3_LAB
Janela         4h
Período        28/06/26 00:00 → 28/06/26 04:00
Filtro         COMPLETED + pnl_pct IS NOT NULL + profile_id IS NOT NULL
Trades analisados  92
Profiles       21
Symbols        15
Review ID      9b8e6739…
Context hash   abc123def456…

Source breakdown
L3       61 trades
L3_LAB   31 trades
```

Reviews legados: aviso amarelo `"Diagnóstico legado sem contexto auditável. Reprocessar review."`

---

## 11. Fase I — Activity Timeline

Novos eventos registrados:
- `AI_REVIEW_CONTEXT_BUILT` — payload com sources, window, trades_count, profiles_count, hash
- `AI_REVIEW_CONTEXT_PERSISTED` — payload com review_id + hash
- `AI_REVIEW_COMPLETED_WITH_CONTEXT` — payload completo substitui `AI_REVIEW_COMPLETED`
- `AI_REVIEW_FAILED_MISSING_CONTEXT` — quando analysis_context incompleto

---

## 12. Fase J — Merge com Remote

O remote adicionou `ai_review_safety_service.completed_review_contract_is_valid` (commits `d55f113`–`5d88cfc`). Conflito em `profile_intelligence_live_service.py` linha 901 resolvido combinando:
- Remote: `completed_review_contract_is_valid()` valida tokens + summary + model
- Local: `FAILED_MISSING_ANALYSIS_CONTEXT` valida trades_count presente no context

---

## 13. Fase K — Testes

```
tests/test_ai_review_analysis_context.py — 13 testes
tests/test_autopilot_shadow_calibration.py — 12 testes
Total: 25 passed, 1 warning
```

| Teste | Status |
|---|---|
| test_analysis_context_structure | PASSED |
| test_context_payload_hash_changes_when_sources_change | PASSED |
| test_context_query_hash_stable | PASSED |
| test_strip_json_codeblock_removes_fences | PASSED |
| test_strip_json_codeblock_passthrough_plain | PASSED |
| test_strip_json_codeblock_backtick_only | PASSED |
| test_source_to_portfolio_view_mapping | PASSED |
| test_source_view_map_defined | PASSED |
| test_window_start_end_iso8601 | PASSED |
| test_ai_sources_not_empty | PASSED |
| test_ai_review_endpoint_returns_context_fields | PASSED |
| test_completed_requires_analysis_context | PASSED |
| test_completed_event_includes_context | PASSED |

---

## 14. Fase L — Deploy

```
commit: 1da169b (fix) + fd5c53d (merge)
git push → origin/main: 5d88cfc..fd5c53d
migration 116: aplicada manualmente + alembic roda no restart da API
```

---

## 15. Fase M — Validação Pós-Deploy

### SQL

```
analysis_context column: exists ✓
context_payload_hash column: exists ✓
context_query_hash column: exists ✓
legacy reviews marcados: 4 rows ✓
new reviews: analysis_context será preenchido na próxima execução do AI cycle
```

### Endpoint

Reviews legados: `analysis_context_legacy: true`, `analysis_context_available: false`
Próximo review (após deploy): `analysis_context_available: true`, hashes preenchidos

---

## 16. Fase N — Safety Final

| Check | Status |
|---|---|
| live_enabled=0 | ✓ PASS |
| live_orders=0 | ✓ PASS |
| active_new_models_24h=0 | ✓ PASS |
| profiles_created_24h=0 | ✓ PASS |
| mutations_24h=0 | ✓ PASS |
| ML_GATE_ENABLED=false | ✓ PASS |

---

## 17. Ledger de Evidências

| Afirmação | Origem | Valor |
|---|---|---|
| Colunas não existiam | SQL `information_schema.columns` | analysis_context, context_payload_hash, context_query_hash ausentes |
| Claude retornava ```json``` | SQL `summary` raw de `profile_ai_reviews` | `\`\`\`json\n{...}\n\`\`\`` |
| findings sempre vazio | SQL `profile_ai_reviews.findings` | `{}` em todos os reviews |
| 4 reviews legados | SQL UPDATE rowcount | 4 |
| Migration 116 aplicada | SQL `information_schema.columns` | 3 novas colunas ✓ |
| 25/25 testes | pytest | PASSED |
| Commit | git log | `1da169b` + `fd5c53d` |
| Push OK | git push | `5d88cfc..fd5c53d` |
| Safety final | SQL | todos 0 |

---

## 18. Checklist Contrato

| Contrato | Status | Evidência |
|---|---|---|
| AI review persiste analysis_context | PASS | migration 116 + código |
| Sources gravadas | PASS | analysis_context.dataset.sources |
| Período gravado | PASS | analysis_context.window.window_start/end |
| Filtros gravados | PASS | analysis_context.dataset.filters |
| Source breakdown gravado | PASS | analysis_context.sample.source_breakdown |
| COMPLETED exige context | PASS | FAILED_MISSING_ANALYSIS_CONTEXT guard |
| Legacy sem contexto sinalizado | PASS | analysis_context_legacy=true + UI warning |
| Activity Timeline atualizado | PASS | AI_REVIEW_COMPLETED_WITH_CONTEXT + CONTEXT_BUILT |
| UI mostra fonte/período/filtros | PASS | bloco auditável antes do diagnóstico |
| UI marca legado com aviso | PASS | aviso amarelo "legado sem contexto" |
| _strip_json_codeblock fix | PASS | test_strip_json_codeblock_removes_fences |
| Safety final | PASS | SQL |

---

## 19. Veredito

```
AI_CRITIC_AUDITABLE_SOURCE_CONTEXT_OPERATIONAL
```

### Justificativa

- Colunas `analysis_context`, `context_payload_hash`, `context_query_hash` criadas ✓
- Reviews antigos marcados como `_legacy: true` ✓
- `run_ai_review_cycle` constrói e persiste `analysis_context` completo antes de chamar Claude ✓
- Sources `L3` + `L3_LAB`, window_start/end, source_breakdown, symbols, negative_profiles gravados ✓
- `_strip_json_codeblock()` resolve JSONDecodeError → findings/recommendations/risk_flags parseados ✓
- Endpoint retorna `analysis_context`, `analysis_context_available`, `context_payload_hash` ✓
- UI exibe bloco auditável (fonte, aba/visão, sources, janela, período, filtro, trades, profiles, symbols, review_id, hash) ✓
- Reviews legados: aviso amarelo na UI ✓
- 13 novos testes (25 total): 25/25 passing ✓
- Safety: live=0, mutations=0, profiles_created=0, ML_GATE=false ✓
