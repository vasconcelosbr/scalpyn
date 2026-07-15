# AUDITORIA: Profile Intelligence — Calibration Evolution — PENDING_VALIDATION
**Data:** 2026-06-28 (evidências coletadas até 2026-06-29 01:07 UTC)
**Escopo:** READ-ONLY. Nenhum dado foi modificado durante esta auditoria.
**Analista:** Claude Code (claude-sonnet-4-6)

---

## 1. CONTEXTO E PROBLEMA

A aba "Ajustes" em `/profile-intelligence` → "Calibration Evolution" exibe:
- **1784 ajustes** no total
- `shadow_validation_status: PENDING_VALIDATION`
- `Status versão: SHADOW_APPLIED`
- `Rollback: Disponível`
- `Aplicado em: —`
- `Valor atual: null`

A suspeita era de que as melhorias calculadas NÃO estavam sendo aplicadas aos perfis L3.

**Conclusão da auditoria: A suspeita está correta. Nenhum ajuste foi — ou será — aplicado ao config real dos perfis com o código atual.**

---

## 2. ARQUIVOS AUDITADOS

| Arquivo | Linhas lidas |
|---|---|
| `backend/app/api/calibration_evolution.py` | 952 (completo) |
| `backend/app/services/profile_intelligence_live_service.py` | 1031 (completo) |
| `backend/app/models/profile_intelligence.py` | 587 (completo) |
| `backend/app/tasks/profile_intelligence_job.py` | 411 (completo) |
| `backend/app/tasks/celery_app.py` | 649 (completo) |
| `backend/app/api/profile_intelligence.py` | 1863 (completo) |
| `frontend/app/profile-intelligence/page.tsx` | parcial (tipos, renderer, queries) |

---

## 3. EVIDÊNCIAS DE BANCO DE DADOS (Queries read-only)

Todas as queries abaixo foram executadas via `zephyr.proxy.rlwy.net:23422` (URL pública Railway) em modo read-only (SELECT).

### Q1 — Distribuição por status em `profile_adjustment_versions`

```sql
SELECT shadow_validation_status, version_status, COUNT(*) as total,
       MIN(created_at)::text, MAX(created_at)::text,
       COUNT(*) FILTER (WHERE mutation_applied=true) as mutations_applied
FROM profile_adjustment_versions
GROUP BY shadow_validation_status, version_status ORDER BY total DESC;
```

| shadow_validation_status | version_status | total | criado_em (min) | criado_em (max) | mutations_applied |
|---|---|---|---|---|---|
| PENDING_VALIDATION | SHADOW_APPLIED | **1784** | 2026-06-27 22:57:19 UTC | 2026-06-29 00:48:06 UTC | **0** |

**Conclusão:** `PENDING_VALIDATION` é o ÚNICO valor que já existiu em `shadow_validation_status`. Nunca houve `VALIDATED`, `REJECTED`, ou qualquer outro estado.

### Q2 — Distribuição em `profile_adjustment_suggestions`

```sql
SELECT status, COUNT(*) FROM profile_adjustment_suggestions GROUP BY status;
```

| status | total |
|---|---|
| SHADOW_APPLIED | 1784 |
| PENDING_SHADOW_VALIDATION | 0 |

**Conclusão:** Todas as 1784 sugestões foram processadas pelo `run_shadow_calibration_cycle`. O ciclo já NÃO tem mais nada a processar.

### Q3 — `current_value` nulo

```sql
SELECT COUNT(*) FROM profile_adjustment_suggestions WHERE current_value IS NULL;
```

Resultado: **1784** (100% das sugestões têm `current_value = NULL`).

### Q4 — `requires_human_approval`

```sql
SELECT requires_human_approval, COUNT(*) FROM profile_adjustment_suggestions GROUP BY requires_human_approval;
```

| requires_human_approval | total |
|---|---|
| false | 1784 |

### Q5 — Snapshot before/after (amostra de 2)

```
before_snapshot: {'scoring': {'thresholds': {'buy': 65}}}
after_snapshot:  {'scoring': {'thresholds': {'buy': 70}}}
diff:            {'scoring': {'thresholds': {'buy': {'before': 65, 'after': 70}}}}
```

TODOS os 1784 diffs são idênticos: `buy 65 → 70`. Explicação: `_SCORE_BUMP=5` (hardcoded default), `thresholds.get("buy", 65)` (default 65 quando ausente).

### Q6 — `autopilot_pending_actions` — estado atual

```sql
SELECT action_type, target_scope, requires_human_approval, COUNT(*) 
FROM autopilot_pending_actions GROUP BY action_type, target_scope, requires_human_approval;

SELECT action_status, COUNT(*) FROM autopilot_pending_actions GROUP BY action_status;

SELECT MIN(created_at)::text, MAX(created_at)::text 
FROM autopilot_pending_actions WHERE action_status='PROCESSING';
```

| action_type | target_scope | requires_human_approval | count |
|---|---|---|---|
| ADJUST_MINIMUM_SCORE | SHADOW | false | 1784 |

| action_status | count |
|---|---|
| PROCESSING | 1784 |

Período em PROCESSING: 2026-06-27 13:37:30 UTC → 2026-06-29 00:42:51 UTC (stuck há mais de 35h).

### Q7 — Autopilot settings

```sql
SELECT enabled, settings_json FROM profile_intelligence_autopilot_settings LIMIT 5;
```

| enabled | settings_json (resumo) |
|---|---|
| **true** | cycle_hours: 24, review_min_trades: 50, max_shadow_candidates: 30, ... |

Autopilot está HABILITADO. O ciclo `run_shadow_calibration_cycle` NÃO é abortado por `_is_autopilot_enabled()`.

### Q8 — Perfis afetados

```sql
SELECT COUNT(DISTINCT profile_id) FROM profile_adjustment_versions 
WHERE shadow_validation_status='PENDING_VALIDATION';
```

Resultado: **32 perfis distintos**.

Tipo dos perfis (amostra de 5): todos `profile_type='GENERATED'`, `is_active=true`, `is_shadow_only=true`, `live_trading_enabled=false`.

### Q9 — Valor atual do threshold nos perfis (vs diff)

```sql
SELECT p.name, p.config->'scoring'->'thresholds'->>'buy' as current_buy,
       v.diff->'scoring'->'thresholds'->'buy'->>'before' as diff_before,
       v.diff->'scoring'->'thresholds'->'buy'->>'after' as diff_after
FROM profile_adjustment_versions v JOIN profiles p ON p.id = v.profile_id
WHERE v.shadow_validation_status='PENDING_VALIDATION' ORDER BY v.created_at DESC LIMIT 10;
```

| perfil (truncado) | current_buy atual | diff before | diff after | mutation_applied |
|---|---|---|---|---|
| L3_MEAN_REVERSION_CONTROLADO_V3 | 65 | 65 | 70 | False |
| rsi_gte_72_AND_ema50... | null | 65 | 70 | False |
| vol_spike_gt_2_5_AND_adx... | null | 65 | 70 | False |
| macd_hist_lte_0_AND_bb... | 65 | 65 | 70 | False |

**Conclusão:** O threshold `buy` NÃO foi alterado. `mutation_applied=False` para 100% dos registros.

### Q10 — Constraint de banco que bloqueia `mutation_applied=true`

```sql
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint 
WHERE conrelid='profile_adjustment_suggestions'::regclass AND contype='c';
```

```
chk_adj_sugg_mutation: CHECK ((mutation_applied = false) OR (requires_human_approval = true))
```

Como `requires_human_approval=false` em TODOS os 1784 registros (Q4), a constraint do banco **bloqueia definitivamente** qualquer tentativa de UPDATE `mutation_applied=true`. Um UPDATE tentado resultaria em `CheckViolation`.

### Q11 — Log de atividade: eventos existentes vs esperados

```sql
SELECT event_type, COUNT(*) FROM profile_intelligence_activity_log 
GROUP BY event_type ORDER BY count DESC;
```

| event_type | count |
|---|---|
| AUTOPILOT_SHADOW_CALIBRATION_APPLIED | 1784 |
| SUGGESTION_CREATED | 1784 |
| HEARTBEAT | 1270 |
| RUN_COMPLETED | 625 |
| SCANNING_SHADOW | 539 |
| ANALYZING_PROFILES | 539 |
| AUTOPILOT_RUN_COMPLETED | 300 |
| AUTOPILOT_SHADOW_CALIBRATION_STARTED | 300 |
| GENERATING_ADJUSTMENT_SUGGESTIONS | 86 |
| MINING_INDICATORS | 86 |
| MINING_HARD_NEGATIVES | 86 |
| AI_REVIEW_* | 43 |

Eventos que **NUNCA existiram**:
- Nenhum `*_VALIDATED`, `*_APPLIED_LIVE`, `*_MUTATION*`, `*APPLY*`
- Consultas `WHERE event_type ILIKE '%VALID%'` → 0 resultados
- Consultas `WHERE event_type ILIKE '%APPLY%'` → 0 resultados
- Consultas `WHERE event_type ILIKE '%MUTAT%'` → 0 resultados

### Q12 — Ciclos recentes (payloads pós-esgotamento)

```
2026-06-29 01:07:42 UTC: processed=0, failed=0, mutation_applied=False
2026-06-29 01:02:40 UTC: processed=0, failed=0, mutation_applied=False
2026-06-29 00:57:40 UTC: processed=0, failed=0, mutation_applied=False
2026-06-29 00:52:47 UTC: processed=0, failed=0, mutation_applied=False
2026-06-29 00:48:06 UTC: processed=8, failed=0, mutation_applied=False  ← último com work
```

O ciclo shadow calibration roda a cada ~5 minutos (via `feedback_loop`), mas desde 2026-06-29 00:48 UTC, `processed=0` porque não há mais sugestões elegíveis.

### Q13 — Taxa de criação de sugestões vs versões por dia

| Dia | Sugestões criadas | Versões criadas |
|---|---|---|
| 2026-06-27 | 541 | 260 |
| 2026-06-28 | 1188 | 1469 |
| 2026-06-29 | 55 | 55 |
| **Total** | **1784** | **1784** |

---

## 4. ANÁLISE DE CÓDIGO — CAUSAS RAIZ

### Causa Raiz 1: `PENDING_VALIDATION` é estado terminal — nenhum código avança além dele

**Arquivo:** `backend/app/services/profile_intelligence_live_service.py` linha 513

O `run_shadow_calibration_cycle()` cria registros em `profile_adjustment_versions` com:
```python
'PENDING_VALIDATION', false, true, now()
# shadow_validation_status='PENDING_VALIDATION', mutation_applied=false, rollback_available=true
```

**Não existe em nenhum arquivo `.py` do backend** qualquer código que faça:
```sql
UPDATE profile_adjustment_versions SET shadow_validation_status='VALIDATED' ...
```

O único lugar onde `'VALIDATED'` aparece é na query do summary (linha 64 de `calibration_evolution.py`):
```sql
COUNT(*) FILTER (WHERE shadow_validation_status = 'VALIDATED') AS validated
```
— que retorna sempre 0.

O estado `PENDING_VALIDATION` foi projetado como pré-condição para uma fase de validação que **nunca foi implementada**.

### Causa Raiz 2: Nenhum código aplica o diff ao config do perfil

**Arquivo:** `backend/app/services/profile_intelligence_live_service.py` docstring linha 447-450:
```
- Creates profile_adjustment_versions records (before/after snapshots).
- Never sets mutation_applied=true.
- Never changes the live profile config.
```

O diff `{scoring: {thresholds: {buy: {before: 65, after: 70}}}}` é um documento informacional. Não existe código que execute:
```python
profile.config['scoring']['thresholds']['buy'] = new_buy
```

### Causa Raiz 3: DB constraint bloqueia `mutation_applied=true` para estes registros

**Arquivo:** `backend/alembic/versions/113_pi_live_engine.py` linha 155

Constraint no banco:
```sql
CHECK (mutation_applied = false OR requires_human_approval = true)
```

Como `requires_human_approval=false` em TODOS os 1784 registros, qualquer UPDATE de `mutation_applied=false → true` seria rejeitado com `CheckViolation`. Isso foi uma decisão de design deliberada: a mutation só pode ocorrer após aprovação humana explícita, mas o fluxo de aprovação humana nunca foi construído.

### Causa Raiz 4: `current_value=null` é bug de código (INSERT hardcoded)

**Arquivo:** `backend/app/services/profile_intelligence_live_service.py` (função `run_medium_cycle`, aproximadamente linha 371)

O INSERT de sugestão usa `null` literal para `current_value`:
```sql
INSERT INTO profile_adjustment_suggestions
    (..., current_value, suggested_value, ...)
VALUES
    (..., null, CAST(:suggested AS jsonb), ...)
```

O valor atual DO perfil não é lido no momento da criação da sugestão. O `before_snapshot` no PAV captura o valor correto (via `p.config->'scoring'`), mas isso nunca é retroativamente gravado em `profile_adjustment_suggestions.current_value`.

### Causa Raiz 5: Diff uniforme — default hardcoded mascara a heterogeneidade real

Todos os 1784 diffs são idênticos (`65 → 70`) porque:
1. `current_buy = int(thresholds.get("buy", 65))` — fallback 65 para perfis sem threshold
2. `_SCORE_BUMP = int(os.environ.get("PI_SCORE_BUMP", "5"))` — default 5
3. `new_buy = min(current_buy + _SCORE_BUMP, _SCORE_CAP)` = min(65+5, 85) = 70

Para perfis que JÁ tinham `buy=65` explícito, o diff é matematicamente correto mas igualmente uninformativo (não diferencia "tinha 65" de "não tinha — usou default 65").

### Causa Raiz 5: `autopilot_pending_actions` stuck em PROCESSING (fuga de estado)

`run_shadow_calibration_cycle` move as actions de `PENDING → PROCESSING` (linha 529-532):
```sql
UPDATE autopilot_pending_actions SET action_status='PROCESSING', updated_at=now()
WHERE suggestion_id=:sid AND action_status='PENDING'
```

Não existe código que transite `PROCESSING → COMPLETED` ou `PROCESSING → APPLIED`. As 1784 ações em `PROCESSING` desde 2026-06-27 são uma fuga de estado permanente enquanto o código não mudar.

---

## 5. INTERPRETAÇÃO DO QUE A UI ESTÁ MOSTRANDO

| Campo UI | Valor exibido | O que realmente significa |
|---|---|---|
| `Status versão: SHADOW_APPLIED` | indica mudança aplicada | `version_status='SHADOW_APPLIED'` = "o documento de versão foi criado". NÃO significa que o config do perfil mudou |
| `shadow_validation_status: PENDING_VALIDATION` | aguardando validação | estado terminal. Nunca avançará com código atual |
| `Rollback: Disponível` | implica que algo foi aplicado e pode ser revertido | `rollback_available=true` é o default (linha 511 do service). Nada foi aplicado; não há o que reverter |
| `Aplicado em: —` | não foi aplicado ainda | `applied_at=null` confirmado para 100% dos 1784 registros |
| `Valor atual: null` | UI mostra "null" com JSON.stringify | `current_value=null` hardcoded no INSERT desde a criação da sugestão |
| Count "1784 Ajustes" | total de ajustes pendentes | count de `profile_adjustment_suggestions` ALL TIME (sem filtro de data) |

---

## 6. RESPOSTAS ÀS HIPÓTESES (H1-H15)

**H1: `PENDING_VALIDATION` porque não há código que avance este estado**
CONFIRMADO. O único lugar em que `PENDING_VALIDATION` é escrito é no INSERT em `run_shadow_calibration_cycle` (linha 513). Nenhum UPDATE para qualquer outro valor existe no codebase.

**H2: Ajustes não executados porque a feature de aplicação não foi implementada**
CONFIRMADO. O docstring do `run_shadow_calibration_cycle` declara explicitamente "Never changes the live profile config." Não existe nenhuma função que leia o diff e aplique ao `profiles.config`.

**H3: `current_value=null` porque o INSERT hardcoda null**
CONFIRMADO. O INSERT em `run_medium_cycle` usa `null` literal. 1784/1784 registros têm `current_value IS NULL`.

**H4: `mutation_applied=false` em 100% dos registros**
CONFIRMADO. Query Q19: `PAV mutation_applied=true: 0`, `PAS mutation_applied=true: 0`.

**H5: Rollback enganoso porque nada foi mutado**
CONFIRMADO. `rollback_available=true` é o default do modelo (linha 517, `rollback_available, created_at`). Como `mutation_applied=false`, nenhum rollback é operacionalmente possível.

**H6: Autopilot ESTÁ habilitado (enabled=true)**
CONFIRMADO. `profile_intelligence_autopilot_settings: enabled=true` desde 2026-06-28 02:36:58 UTC.

**H7: O ciclo shadow calibration JÁ rodou e processou tudo**
CONFIRMADO. 300 execuções de `AUTOPILOT_SHADOW_CALIBRATION_STARTED`, 1784 `AUTOPILOT_SHADOW_CALIBRATION_APPLIED`. Desde 2026-06-29 00:48 UTC, `processed=0` em cada execução subsequente.

**H8: O ciclo não processa mais nada porque todas as sugestões são `SHADOW_APPLIED`**
CONFIRMADO. O query em `run_shadow_calibration_cycle` linha 474 filtra `WHERE s.status='PENDING_SHADOW_VALIDATION'`. Como todas são `SHADOW_APPLIED`, o resultado é sempre vazio.

**H9: Todos os perfis afetados são shadow-only**
CONFIRMADO. 32 perfis distintos, todos com `is_shadow_only=true`, `live_trading_enabled=false`.

**H10: O diff uniforme (65→70) é um artefato do default hardcoded**
CONFIRMADO. `thresholds.get("buy", 65)` com `_SCORE_BUMP=5` → todo perfil sem threshold explícito usa o mesmo valor base.

**H11: DB constraint impede `mutation_applied=true` para esses registros**
CONFIRMADO. `chk_adj_sugg_mutation: CHECK (mutation_applied=false OR requires_human_approval=true)`. Como `requires_human_approval=false` em 1784/1784, qualquer UPDATE seria rejeitado com CheckViolation.

**H12: `autopilot_pending_actions` em PROCESSING é fuga de estado permanente**
CONFIRMADO. 1784 ações `PROCESSING` desde 2026-06-27 13:37 UTC. Nenhum código transita `PROCESSING → COMPLETED`.

**H13: O `before_snapshot` captura o valor correto, mas não é linkado ao `current_value`**
CONFIRMADO. `before_snapshot: {'scoring': {'thresholds': {'buy': 65}}}` — valor lido corretamente do config do perfil durante `run_shadow_calibration_cycle`. Porém, `profile_adjustment_suggestions.current_value` nunca recebe este valor retroativamente.

**H14: Não existe evento de log para aplicação de mutação real**
CONFIRMADO. Busca `WHERE event_type ILIKE '%VALID%'` → 0. `WHERE event_type ILIKE '%APPLY%'` → 0. `WHERE event_type ILIKE '%MUTAT%'` → 0.

**H15: Nunca houve qualquer `VALIDATED` ou `APPLIED` no banco**
CONFIRMADO. `SELECT DISTINCT shadow_validation_status FROM profile_adjustment_versions` → retorna apenas `('PENDING_VALIDATION',)`.

---

## 7. EVIDÊNCIA DE LOGS DE WORKERS

Logs do `scalpyn-worker-compute` e `scalpyn-beat` mostram atividade normal de market data e pipeline scan. O ciclo `feedback_loop` roda a cada ~5 minutos no queue `structural_compute`, mas os logs de mercado dominam — nenhuma mensagem `[PILive]` ou calibration estava visível na janela de 200 linhas capturada.

O activity log do banco confirma que o ciclo está rodando (300 execuções de `AUTOPILOT_SHADOW_CALIBRATION_STARTED`), apenas sem work a fazer desde 2026-06-29 00:48 UTC.

---

## 8. DIAGNÓSTICO ARQUITETURAL

O fluxo foi projetado em 3 fases, mas apenas 2 foram implementadas:

```
FASE 1 (IMPLEMENTADA): run_medium_cycle
  → Cria profile_adjustment_suggestions (status='PENDING_SHADOW_VALIDATION')
  → Trigger: win_rate < 35% nas últimas 24h com >= 10 trades
  → BUG: current_value = null (hardcoded)

FASE 2 (IMPLEMENTADA): run_shadow_calibration_cycle  
  → Lê sugestões PENDING_SHADOW_VALIDATION
  → Cria profile_adjustment_versions (shadow_validation_status='PENDING_VALIDATION')
  → Move sugestão para SHADOW_APPLIED
  → Move autopilot_pending_actions para PROCESSING
  → DESIGN EXPLÍCITO: mutation_applied=false, never changes live config

FASE 3 (NÃO IMPLEMENTADA): ??? run_validation_cycle / apply_cycle
  → Deveria: avaliar performance do perfil APÓS a shadow calibration
  → Deveria: decidir se o ajuste melhorou o resultado
  → Deveria: transitar PENDING_VALIDATION → VALIDATED ou REJECTED
  → Deveria: com VALIDATED + aprovação: aplicar diff ao profiles.config
  → Deveria: mover autopilot_pending_actions para COMPLETED ou APPLIED
```

A Fase 3 não existe em nenhum arquivo do backend. Os endpoints em `calibration_evolution.py` são todos declarados read-only. Não há endpoint POST/PATCH que execute a aplicação.

---

## 9. IMPACTO OPERACIONAL

- Os perfis L3 que foram identificados como tendo baixa taxa de vitória (win_rate < 35%) **continuam rodando com o mesmo threshold buy=65** que disparou o alerta.
- 32 perfis distintos têm 1784 registros de "ajustes" acumulados que nunca serão aplicados automaticamente.
- A UI mostra "Rollback: Disponível" para todos, criando a impressão de que mudanças foram feitas. Isso é semanticamente incorreto.
- O sistema gera novas sugestões a cada invocação do `run_medium_cycle` (a cada 30 minutos, quando `_needs_medium_cycle()` retorna True), potencialmente criando mais registros PENDING_VALIDATION indefinidamente.
- `autopilot_pending_actions` acumulará indefinidamente no estado `PROCESSING`.

---

## 10. RECOMENDAÇÕES

### P0 — Clareza imediata (sem código)
Nenhuma ação de emergência necessária. O sistema NÃO está aplicando mudanças incorretamente — ele não está aplicando nada. O risco é zero de mutação acidental.

### P1 — Implementar Fase 3 (aplicação controlada)
Criar `run_apply_cycle()` em `profile_intelligence_live_service.py` que:
1. Seleciona versões com `shadow_validation_status='PENDING_VALIDATION'` onde shadow performance melhorou após a data de criação
2. Compara win_rate antes/depois para validar o diff
3. Se VALIDATED: aplica `profiles.config['scoring']['thresholds']['buy'] = after_value`
4. Atualiza `mutation_applied=true` (requer `requires_human_approval=true` OR remoção da constraint)
5. Move `autopilot_pending_actions` para `COMPLETED`

Alternativa mais segura: UI de aprovação manual onde o usuário clica "Aplicar" em cada ajuste.

### P2 — Corrigir `current_value=null`
Em `run_medium_cycle`, antes do INSERT, ler:
```python
current_buy = (profile.config or {}).get('scoring', {}).get('thresholds', {}).get('buy')
```
e passar este valor para `current_value`.

### P3 — Corrigir semântica da UI
- "Status versão: SHADOW_APPLIED" → renomear para "Versão shadow criada"
- "Rollback: Disponível" → mostrar apenas quando `mutation_applied=true`
- "PENDING_VALIDATION" → exibir como "Aguardando validação futura (feature em desenvolvimento)"

### P4 — Limpar `autopilot_pending_actions` stuck
```sql
-- Mover PROCESSING → ABANDONED para os registros criados antes de hoje
UPDATE autopilot_pending_actions 
SET action_status='ABANDONED', updated_at=now()
WHERE action_status='PROCESSING' AND created_at < now() - interval '24 hours';
```
(executar apenas quando decidir o que fazer com estes registros)

### P5 — Avaliar se ciclos de sugestão devem continuar
Com 1784 sugestões acumuladas e sem consumidor da Fase 3, continuar gerando `PENDING_SHADOW_VALIDATION` só cresce a dívida técnica. Considerar:
- Pausar `run_medium_cycle` até Fase 3 estar implementada
- Ou implementar rate-limit por perfil (evitar duplicação de sugestões idênticas)

---

## 11. SUMÁRIO EXECUTIVO

```
AUDITORIA CONCLUÍDA — SEM ALTERAÇÕES APLICADAS

Causa raiz principal:
  A Fase 3 do pipeline de calibração (validação e aplicação do diff ao config do
  perfil) não foi implementada. PENDING_VALIDATION é um estado terminal sem
  consumidor. Nenhum ajuste foi ou será aplicado com o código atual.

Causas contribuintes:
  1. DB constraint chk_adj_sugg_mutation bloqueia mutation_applied=true enquanto
     requires_human_approval=false — proteção arquitetural intencional sem fluxo
     de aprovação implementado.
  2. current_value=null hardcoded no INSERT de run_medium_cycle — bug de código
     presente em 100% das 1784 sugestões.
  3. autopilot_pending_actions stuck em PROCESSING desde 2026-06-27 — fuga de
     estado sem código de transição para COMPLETED.

Respostas definitivas:
  1. PENDING_VALIDATION porque: é o único valor inserido pelo run_shadow_
     calibration_cycle (linha 513 de profile_intelligence_live_service.py).
     Nenhum código faz UPDATE para 'VALIDATED' em qualquer arquivo do backend.
  2. Ajustes não executados porque: a função que aplicaria o diff ao
     profiles.config não existe. O ciclo shadow_calibration CRIA documentos
     informativos (before/after/diff) mas possui docstring explícito:
     "Never changes the live profile config."
  3. current_value=null porque: INSERT em run_medium_cycle usa literal null
     (não lê o valor atual do perfil). Confirmado: 1784/1784 NULL.
  4. Rollback: Disponível enganoso porque: rollback_available=true é o
     default do ORM. Como mutation_applied=false em 100% dos casos,
     não há nada para reverter.

Evidência mais forte:
  SELECT DISTINCT shadow_validation_status FROM profile_adjustment_versions;
  Retorna: ('PENDING_VALIDATION',) — único valor em toda a história da tabela.
  Combinado com grep em todo o backend Python: zero ocorrências de UPDATE
  profile_adjustment_versions SET shadow_validation_status='VALIDATED'.

Relatório salvo em:
  docs/RELATORIO_AUDITORIA_PROFILE_INTELLIGENCE_CALIBRATION_EVOLUTION_PENDING_VALIDATION_2026-06-28.md
```

---

*Auditoria executada em modo read-only. Nenhuma modificação de dados ou código foi realizada.*
*Conexão ao banco via URL pública Railway: `zephyr.proxy.rlwy.net:23422`.*
*Evidências de banco coletadas em: 2026-06-29 01:07-01:15 UTC.*
