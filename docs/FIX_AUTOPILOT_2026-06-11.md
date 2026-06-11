# FIX_AUTOPILOT — Correção Auto-Pilot: Fonte Única de Verdade
**Data:** 2026-06-11  
**Status:** Código aplicado — aguardando deploy + validação pós-deploy  
**Branch:** main (via commit direto)

---

## 1. Inventário de Pontos de Leitura/Escrita (Parte 0)

### Seção: scoring_rules

| Componente | Op | Fonte | Arquivo:loc |
|---|---|---|---|
| `apply_rule_adjustments` | W | `config_profiles(score).scoring_rules` | `autopilot_engine.py:1069` |
| `pipeline_scan` | R | `config_service.get_config("score")` → `score_config` | `pipeline_scan.py:2464` |
| `_apply_robust_authoritative_scoring` | R | `score_config` (param) | `pipeline_scan.py:2514` |

**STATUS: CONECTADO** ✓

### Seção: minimum_score

| Componente | Op | Fonte | Arquivo:loc |
|---|---|---|---|
| `_adjust_minimum_score` | W | `config_profiles(score).minimum_score` | `autopilot_engine.py:1203` |
| `pipeline_scan` (custom gate) | R | `score_config.get("minimum_score")` → `_autopilot_min` | `pipeline_scan.py:2588` |
| `pipeline_scan` (L3 gate) | R | `score_config.get("minimum_score")` → `_autopilot_min` | `pipeline_scan.py:2726` |

**STATUS: CONECTADO após esta fix** ✓ (era L-01: DISCONNECTED)

### Seção: block_rules

| Componente | Op | Fonte | Arquivo:loc |
|---|---|---|---|
| `_adjust_block_rules` | W | `config_profiles(block).block_rules.blocks` | `autopilot_engine.py:1322` |
| `pipeline_scan` | R | `config_service.get_config("block")` → `_block_cfg.block_rules` → `profile_config` | `pipeline_scan.py:2484` |
| `ProfileEngine` | R | `profile_config.get("block_rules")` | `profile_engine.py:163` |

**STATUS: CONECTADO após esta fix** ✓ (era L-02: DISCONNECTED)

### Seção: entry_triggers

| Componente | Op | Fonte | Arquivo:loc |
|---|---|---|---|
| `_adjust_entry_triggers` | W | `config_profiles(block).entry_triggers.conditions` | `autopilot_engine.py:1439` |
| `pipeline_scan` | R | `config_service.get_config("block")` → `_block_cfg.entry_triggers` → `profile_config` | `pipeline_scan.py:2485` |
| `ProfileEngine` | R | `profile_config.get("entry_triggers")` | `profile_engine.py:172` |

**STATUS: CONECTADO após esta fix** ✓ (era L-03: DISCONNECTED)

### Seção: filters

**STATUS: REMOVIDO do allowlist (L-07)** — stub sem leitura no pipeline. Reintroduzir com implementação real em prompt próprio.

---

## 2. Mapa de Paridade Pré-Corte (Parte 0.3)

**Estado do DB em 2026-06-11 (antes da fix):**

| Seção | profiles.config | config_profiles | Divergência |
|---|---|---|---|
| scoring_rules | N/A (não gerenciado aqui) | `[rsi_1(40), rsi_2(30), ema_trend_1(30)]` | N/A |
| minimum_score | None (ausente) | None (ausente) | Nenhuma |
| block_rules | `{"blocks": []}` | `{"blocks": []}` (flat) | Estrutura diferente |
| entry_triggers | `{"conditions": []}` | ausente | Dados ausentes |

**Ação tomada:** `backend/sql/sync_block_config_structure.sql` — normaliza flat `{"blocks": []}` para `{"block_rules": {"blocks": []}, "entry_triggers": {"logic": "AND", "conditions": []}}`.

**⚠ EXECUTAR ANTES DO DEPLOY:** rodar `sync_block_config_structure.sql` no DB de produção.

---

## 3. Lacunas Corrigidas

### L-01: minimum_score desconectado
- **Fix:** `pipeline_scan.py` agora lê `_autopilot_min = (score_config or {}).get("minimum_score")` como fonte primária.
- **Fallback deprecado:** `filters_json.min_alpha_score` ainda funciona mas loga `DEPRECATED` warning.
- **Arquivos:** `pipeline_scan.py` linhas 2586-2596, 2724-2734.

### L-02: block_rules desconectado
- **Fix:** `pipeline_scan.py` carrega `block_config` de `config_profiles(block)` via `config_service` e mergeia `block_rules` em `profile_config` antes de passar ao `ProfileEngine`.
- **Arquivos:** `pipeline_scan.py` linhas 2473-2492.
- **Pré-requisito:** `sync_block_config_structure.sql` rodado no DB.

### L-03: entry_triggers desconectado
- **Fix:** Mesmo mecanismo de L-02 — `entry_triggers` mergeiado junto de `block_rules`.
- **Arquivos:** `pipeline_scan.py` linhas 2473-2492.

### L-04: Clamp destrutivo (BLOQUEANTE)
- **Fix:** `adjust_rule_points` agora skippa regras fora de `[rule_points_min, rule_points_max]` com log `AUTOPILOT_OUT_OF_RANGE_SKIPPED` em vez de colapsar (`min(current+1, 10)`).
- **Comportamento:** pts=40 + edge positivo → **skip** (log warning), NÃO → 10.
- **RULE_POINTS_MIN/MAX/DELTA** vêm de `guardrails` (ZERO HARDCODE).
- **Arquivos:** `autopilot_engine.py` linhas 907-952.
- **Teste:** `backend/tests/test_autopilot_rule_clamp.py`

### L-05: Auto-rollback assimétrico
- **Fix:** `rollback_last_adjustment` parseia `[source=score]`/`[source=block]` do `mutation_reason` para restaurar ao `config_type` correto.
- **Backward compat:** snapshots antigos (sem tag) = `source=score` por default.
- **Fix também:** snapshots de `_adjust_minimum_score` agora marcam `[source=score]`, `_adjust_block_rules` e `_adjust_entry_triggers` marcam `[source=block]`.
- **Arquivos:** `autopilot_engine.py` linhas 525-543, 1195-1199, 1317-1319, 1436-1438.

### L-06: Cache Redis sem invalidação
- **Fix:** `config_service.py` expõe `invalidate_cache(config_type, user_id)`. Chamado após cada escrita ORM direta em `autopilot_engine.py` (apply_rule_adjustments, _adjust_minimum_score, _adjust_block_rules, _adjust_entry_triggers, rollback_last_adjustment).
- **Arquivos:** `config_service.py` linhas 120-128, `autopilot_engine.py` 1073, 1207, 1330, 1449, 542.

### L-07: filters stub no allowlist
- **Fix:** `"filters"` removido de `_GUARDRAILS_DEFAULTS.autopilot_can_adjust`. A stub guard no `apply_full_adjustments` permanece como safety net (se alguém manualmente adicionar "filters" ao DB, retorna SKIPPED em vez de crash).
- **Arquivos:** `autopilot_engine.py` linhas 130-135.

### L-08: Seed guardrails ausente
- **Fix 1:** `_load_guardrails` agora loga `GUARDRAILS_ABSENT` com instrução explícita quando não há registro no DB.
- **Fix 2:** `seed_autopilot_guardrails.sql` atualizado com novos campos: `minimum_score_floor`, `minimum_score_ceiling`, `min_score_delta_per_cycle`, `autopilot_full_authority`, `autopilot_can_adjust` (sem "filters").
- **⚠ EXECUTAR:** `backend/sql/seed_autopilot_guardrails.sql` no DB de produção.
- **Arquivos:** `autopilot_engine.py` linhas 176-180, `backend/sql/seed_autopilot_guardrails.sql`.

---

## 4. Arquivos Novos/Modificados

| Arquivo | Tipo | Mudança |
|---|---|---|
| `backend/app/services/config_service.py` | MOD | + `invalidate_cache()` |
| `backend/app/services/autopilot_engine.py` | MOD | L-04/05/06/07/08 + guardrails thread |
| `backend/app/tasks/pipeline_scan.py` | MOD | L-01/02/03: block_config load + min_score source |
| `backend/sql/seed_autopilot_guardrails.sql` | MOD | Novos campos + instruções Railway |
| `backend/sql/sync_block_config_structure.sql` | NEW | Normaliza estrutura config_profiles(block) |
| `backend/tests/test_autopilot_rule_clamp.py` | NEW | Teste unitário L-04 |
| `backend/tests/test_autopilot_connectivity.py` | NEW | Teste regressão de conectividade |

---

## 5. Checklist de Deploy

```
[ ] 1. Rodar sync_block_config_structure.sql no DB de produção (Parte 2.1)
[ ] 2. Rodar seed_autopilot_guardrails.sql no DB de produção (L-08)
[ ] 3. Deploy do código (Railway → main branch)
[ ] 4. Confirmar no log que GUARDRAILS_ABSENT não aparece mais
[ ] 5. Confirmar no log que scoring_rules ainda funcionam (RULES_ANALYZED ou DRY_RUN_RULES_ADJUSTED)
[ ] 6. Testar pytest backend/tests/test_autopilot_rule_clamp.py
[ ] 7. Testar pytest backend/tests/test_autopilot_connectivity.py
```

---

## 6. Validação Pós-Deploy (ainda pendente)

**Requer acesso ao DB/produção:**

1. **Matriz de cobertura** — re-executar `audit_autopilot.py` verificando write→persist→read para as 5 dimensões.

2. **Teste end-to-end minimum_score** (agora conectado ao pipeline):
   - Baseline: contar ALLOWs/hora
   - Elevar `minimum_score` em +5 via escrita direta em `config_profiles(score)`
   - Confirmar queda de ALLOWs e que todos os aprovados têm score ≥ novo mínimo
   - Rollback → retorno ao baseline
   - ⚠ Executar em horário de menor volume, planejar reversão antes do início

3. **Regressão pipeline** — comparar taxa de ALLOWs/hora e distribuição de scores pré/pós deploy:
   - A migração de leitores NÃO deve mudar comportamento (fontes estão em paridade)
   - Qualquer mudança = bug de migração

4. **Isolamento L1_SPECTRUM** — confirmar que Auto-Pilot continua sem acesso a `ml_*`/`shadow_*`.

---

## 7. O que NÃO foi feito (conforme invariantes)

- ✅ dry_run=false NÃO ativado — permanece true
- ✅ autopilot_full_authority NÃO ativado — permanece false
- ✅ ml_*, shadow_*, captura L1, pool filters, decisions_log.metrics — não tocados
- ✅ Registros históricos de profile_versions/autopilot_audit_logs — não modificados
- ✅ ZERO HARDCODE — todos os clamps/floors/ceilings vêm de guardrails/config
