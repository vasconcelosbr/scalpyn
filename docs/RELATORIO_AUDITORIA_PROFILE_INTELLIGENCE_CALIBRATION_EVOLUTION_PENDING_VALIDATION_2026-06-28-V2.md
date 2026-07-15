# RELATÓRIO DE AUDITORIA — Profile Intelligence / Calibration Evolution  
## Problema: 1.812 ajustes PENDING_VALIDATION nunca aplicados  
**Data:** 2026-06-28 | **Auditoria:** READ-ONLY | **Auditor:** Claude Code (automatizado)  
**Escopo:** 13 escopos, 40+ queries SQL, 4 arquivos de código-fonte, zero ALTER/INSERT/UPDATE

---

## 1. RESUMO EXECUTIVO

O pipeline de Calibration Evolution (PI Live Engine) possui **implementação incompleta**. Ele executa correctamente a Fase 1 (detectar perfis com win_rate < 35% e criar snapshots de versão), mas a **Fase 2 jamais foi implementada**: nenhuma função no codebase avança `shadow_validation_status` de `PENDING_VALIDATION` para qualquer outro estado, nenhuma função aplica a mutação ao config do profile, e nenhuma função fecha o ciclo do `autopilot_pending_actions`.

Resultado: 1.812 PAVs, 1.812 PASs e 1.812 APAs acumulam indefinidamente em estados intermediários. Os profiles L3 nunca têm seus thresholds ajustados.

**Evidência-chave:** O docstring da função `run_shadow_calibration_cycle` (linha 449, `profile_intelligence_live_service.py`) afirma explicitamente: `"Never sets mutation_applied=true."` e `"Never changes the live profile config."` — design intencional para uma fase transitória, mas a fase seguinte nunca foi construída.

---

## 2. VEREDICTO DA CAUSA RAIZ

**Causa primária (P0):** A segunda fase do pipeline de shadow calibration não foi implementada. A função `run_shadow_calibration_cycle` cria snapshots (PAV) e marca sugestões como `SHADOW_APPLIED`, mas não existe nenhuma função que:
1. Aplique a mudança de threshold ao config do profile (nem em shadow mode)
2. Colete shadow trades após a mudança para comparar performance antes/depois
3. Transite `shadow_validation_status` de `PENDING_VALIDATION` → `SHADOW_VALIDATED` / `REJECTED`
4. Feche `autopilot_pending_actions` de `PROCESSING` → `COMPLETED` / `FAILED`
5. Grave `mutation_applied=true` ou `applied_at` no PAV

**Causa contribuinte (P1):** Uma constraint CHECK no banco bloqueia fisicamente `mutation_applied=true` para qualquer PAS com `requires_human_approval=false`:

```sql
-- migration 113_pi_live_engine.py, linha 154-155 / 229-230
CONSTRAINT chk_adj_sugg_mutation
    CHECK (mutation_applied = false OR requires_human_approval = true)
```

Todos os 1.812 PAS têm `requires_human_approval=false`. Portanto, nenhum UPDATE poderia setar `mutation_applied=true` sem violar essa constraint — mesmo se o código existisse.

**Causa contribuinte (P2):** `profile_adjustment_suggestions.current_value` é sempre NULL (hardcoded como `null` na linha 371 do serviço). Isso invalida qualquer comparação antes/depois e a coluna perde sua função de auditoria.

---

## 3. RESPOSTAS DIRETAS ÀS 3 PERGUNTAS

### P1: Por que 1.812 ajustes ficam em PENDING_VALIDATION?

`PENDING_VALIDATION` é o estado inserido diretamente no `INSERT` do PAV (linha 513 do serviço). Nunca existe um `UPDATE ... SET shadow_validation_status = 'SHADOW_VALIDATED'` em nenhum arquivo Python do backend. O estado é terminal por omissão de implementação, não por erro.

### P2: Por que os ajustes nunca são aplicados aos profiles L3?

Porque `run_shadow_calibration_cycle` foi projetada explicitamente para **não aplicar a mutação**. O docstring diz "Never changes the live profile config." Não existe nenhum `UPDATE profiles SET config = ...` derivado desse pipeline em produção.

### P3: Por que os logs não mostram execução/rejeição?

O `profile_intelligence_audit_log` registra apenas os eventos `AUTOPILOT_SHADOW_CALIBRATION_APPLIED` (criação do snapshot) e `AUTOPILOT_SHADOW_CALIBRATION_FAILED` (se errar). Os eventos de validação (`SHADOW_VALIDATED`, `MUTATION_REJECTED`, `APA_COMPLETED`) não existem no código.

---

## 4. EVIDÊNCIAS SQL COM RESULTADOS REAIS

### 4.1 Distribuição de PAVs

```
[query: SELECT shadow_validation_status, version_status, COUNT(*), ... FROM profile_adjustment_versions GROUP BY ...]

shadow_validation_status | version_status | total | with_applied_at | mutation_applied_true
-------------------------|----------------|-------|-----------------|----------------------
PENDING_VALIDATION       | SHADOW_APPLIED  | 1.812 | 0               | 0
```
[1 row — ÚNICO estado existente no banco inteiro]

### 4.2 Distribuição de PASs

```
[query: SELECT status, suggestion_type, COUNT(*), ... FROM profile_adjustment_suggestions GROUP BY ...]

status          | suggestion_type | total | null_current | mutation_applied_true | needs_human
----------------|-----------------|-------|--------------|----------------------|------------
SHADOW_APPLIED  | REDUCE_RISK     | 1.812 | 1.812        | 0                    | 0
```
[1 row — ÚNICO estado. current_value NULL em 100% dos registros]

### 4.3 Distribuição de APAs

```
[query: SELECT action_status, action_type, COUNT(*), MIN(created_at), MAX(updated_at) FROM autopilot_pending_actions GROUP BY ...]

action_status | action_type          | total | first                    | last_updated
--------------|----------------------|-------|--------------------------|---------------------------
PROCESSING    | ADJUST_MINIMUM_SCORE | 1.812 | 2026-06-27 13:37:30 UTC  | 2026-06-29 01:17:56 UTC
```
[1 row — TODOS em PROCESSING, nenhum COMPLETED ou FAILED jamais]

### 4.4 Diff uniforme — 100% idêntico

```
[query: SELECT diff::text, COUNT(*) FROM profile_adjustment_versions GROUP BY diff::text ORDER BY total DESC LIMIT 10]

diff                                                                          | total
-----------------------------------------------------------------------------|-------
{"scoring": {"thresholds": {"buy": {"after": 70, "before": 65}}}}            | 1.812
```
[1 row — 100% dos PAVs têm exatamente o mesmo diff: buy_threshold 65→70]

### 4.5 Volume por dia

```
[query: SELECT DATE(created_at), COUNT(*), COUNT(*) FILTER (WHERE shadow_validation_status='PENDING_VALIDATION'), COUNT(*) FILTER (WHERE mutation_applied=true) FROM profile_adjustment_versions WHERE created_at >= NOW()-INTERVAL '14 days' GROUP BY DATE(created_at) ORDER BY day DESC]

day         | created | pending | applied
------------|---------|---------|--------
2026-06-29  | 83      | 83      | 0
2026-06-28  | 1.469   | 1.469   | 0
2026-06-27  | 260     | 260     | 0
```
[Pipeline ativo: gerando novos registros diariamente, todos presos, nenhum aplicado]

### 4.6 Profile intelligence runs (PI Engine — sistema separado)

```
[query: SELECT run_at, status, engine_version, total_profiles, total_shadow_trades, suggestions_generated FROM profile_intelligence_runs ORDER BY run_at DESC LIMIT 10]

run_at                    | status    | engine_version | total_profiles | total_shadow_trades | suggestions_generated
--------------------------|-----------|----------------|----------------|---------------------|---------------------
2026-06-29 00:33:31 UTC   | completed | 2B.1           | 49             | 12.957              | 0
2026-06-28 14:23:52 UTC   | completed | 2B.1           | 49             | 11.591              | 0
2026-06-27 14:45:52 UTC   | completed | 2B.1           | 51             | 9.440               | 0
2026-06-26 23:55:44 UTC   | completed | 2B.1           | 52             | 9.451               | 0
2026-06-26 14:38:18 UTC   | completed | 2B.1           | 52             | 9.561               | 0
...10 runs consecutivos   | completed | 2B.1           | 49-56          | 4.823-12.957        | 0
```
[PI Engine (sistema separado do Live Engine) completa com sucesso mas gera 0 sugestões em todas as runs recentes]

### 4.7 profile_intelligence_autopilot_cycles (sistema PIAC)

```
[query: SELECT * FROM profile_intelligence_autopilot_cycles ORDER BY created_at DESC LIMIT 5]

idempotency_key                                         | status                  | metrics_json.created | errors_json
-------------------------------------------------------|-------------------------|----------------------|----------------------------------
...2026-06-20...:spot:l3                               | COMPLETED_WITH_ERRORS   | 62                   | "Reset by admin: stuck at REVIEW_SHADOW after worker restart"
...2026-06-19...:spot:l3                               | COMPLETED               | 29                   | []
```
[Apenas 2 ciclos PIAC existem. O último (2026-06-20) terminou com erro. Nenhum ciclo desde então.]

### 4.8 profile_intelligence_autopilot_settings

```
[query: SELECT * FROM profile_intelligence_autopilot_settings]

enabled | last_cycle_at            | enabled_at               | disabled_at
--------|--------------------------|--------------------------|---------------------------
true    | 2026-06-20 23:50:31 UTC  | 2026-06-28 02:36:58 UTC  | 2026-06-26 21:41:17 UTC
```
[PIAC reabilitado em 2026-06-28, mas last_cycle_at ainda é 2026-06-20 — nenhum ciclo rodou desde]

### 4.9 autopilot_audit_logs — razão dos bloqueios

```
[query: SELECT * FROM autopilot_audit_logs ORDER BY created_at DESC LIMIT 5]

action                    | reason_code                   | dry_run | mutation_applied
--------------------------|-------------------------------|---------|------------------
AUTOPILOT_SCOPE_BLOCKED   | no_closed_trades_for_scope    | true    | false
AUTOPILOT_SCOPE_BLOCKED   | no_closed_trades_for_scope    | true    | false
...
```
[Autopilot (sistema de L1_SPECTRUM) bloqueado consistentemente por 0 closed trades no escopo L1_SPECTRUM. Dry_run=true em todos.]

### 4.10 Constraint CHECK na migração

```python
# alembic/versions/113_pi_live_engine.py, linhas 154-155
CONSTRAINT chk_adj_sugg_mutation
    CHECK (mutation_applied = false OR requires_human_approval = true),
```

```python
# alembic/versions/113_pi_live_engine.py, linhas 229-230
CONSTRAINT chk_apa_mutation
    CHECK (mutation_applied = false OR requires_human_approval = true),
```

Todos os 1.812 PAS e APA têm `requires_human_approval=false`, tornando `mutation_applied=true` fisicamente impossível neles.

---

## 5. EVIDÊNCIAS DE CÓDIGO

### 5.1 `run_shadow_calibration_cycle` — Fase 1 (implementada)

**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`, linhas 444–579

```python
# Linha 448-452: docstring explícita
"""Shadow calibration executor: moves PENDING_SHADOW_VALIDATION → SHADOW_APPLIED.
- Never sets mutation_applied=true.
- Never changes the live profile config.
"""
```

```python
# Linhas 505-521: INSERT do PAV — estado inicial hardcoded como PENDING_VALIDATION
await db.execute(text("""
    INSERT INTO profile_adjustment_versions
        (id, suggestion_id, profile_id, version_status, before_snapshot,
         after_snapshot, diff, shadow_validation_status, mutation_applied,
         rollback_available, created_at)
    VALUES
        (:vid, :sid, :pid, 'SHADOW_APPLIED', ...,
         'PENDING_VALIDATION', false, true, now())
"""), {...})
```

```python
# Linhas 523-533: UPDATE PAS e APA — termina em SHADOW_APPLIED / PROCESSING
await db.execute(text("""
    UPDATE profile_adjustment_suggestions
    SET status='SHADOW_APPLIED', updated_at=now()
    WHERE id=:sid
"""), {...})

await db.execute(text("""
    UPDATE autopilot_pending_actions
    SET action_status='PROCESSING', updated_at=now()
    WHERE suggestion_id=:sid AND action_status='PENDING'
"""), {...})
```

**Fase 2 (não implementada):** Nenhum código existe para:
- UPDATE PAV.shadow_validation_status → 'SHADOW_VALIDATED' ou 'REJECTED'
- UPDATE autopilot_pending_actions.action_status → 'COMPLETED' ou 'FAILED'
- UPDATE profiles SET config = config || jsonb_update (aplicar threshold)
- Qualquer comparação de performance pré/pós ajuste

### 5.2 `run_medium_cycle` — Gerador de sugestões

**Arquivo:** `backend/app/services/profile_intelligence_live_service.py`, linhas 355–413

```python
# Linha 361: gate de win_rate
if win_rate < 0.35:
    ...
    # Linha 371: current_value hardcoded como null
    INSERT INTO profile_adjustment_suggestions
        (..., current_value, suggested_value, ..., requires_human_approval, ...)
    VALUES
        (..., null, ..., false, ...)  # current_value=null SEMPRE
```

**Problema:** `current_value=NULL` em todos os 1.812 PAS invalida qualquer diff auditável. O campo existe mas nunca é populado — o serviço não lê o threshold atual do profile antes de inserir.

### 5.3 Celery task `feedback_loop` — Orquestrador

**Arquivo:** `backend/app/tasks/profile_intelligence_job.py`, linhas 324–410

```python
async def _run_feedback_loop():
    # 1. Fast cycle — roda sempre
    result = await run_fast_cycle(db)
    
    # 2. Medium cycle — gated por PI_MEDIUM_INTERVAL_M (default 30 min)
    if needs_medium:
        result = await run_medium_cycle(db)    # Gera PAS + APA (PENDING)
    
    # 3. Shadow calibration — roda SEMPRE quando autopilot habilitado
    result = await run_shadow_calibration_cycle(db)  # PAS→SHADOW_APPLIED, APA→PROCESSING, cria PAV
    
    # 4. AI review — gated por PI_AI_REVIEW_INTERVAL_H (default 4h)
    if needs_ai:
        result = await run_ai_review_cycle(db)
    
    # FIM — sem fase de validação, sem aplicação de mutação
```

**Observação:** `_PI_CANDIDATE_CYCLE_ENABLED` = `false` por padrão (linha 26). O ciclo de candidatos do PIAC está **explicitamente desabilitado** por env var:
```python
_PI_CANDIDATE_CYCLE_ENABLED = os.environ.get("PI_AUTOPILOT_CANDIDATE_CYCLE_ENABLED", "false").lower() == "true"
```

### 5.4 Dois sistemas distintos confundindo o diagnóstico

| Sistema | Classe | Tabela de runs | Tabela de sugestões |
|---------|--------|----------------|---------------------|
| **PI Engine** | `ProfileIntelligenceService` | `profile_intelligence_runs` | `profile_suggestions` (legado) |
| **PI Live Engine** | funções em `profile_intelligence_live_service.py` | `profile_intelligence_activity_log` | `profile_adjustment_suggestions` (Calibration Evolution) |

`profile_intelligence_runs.suggestions_generated=0` refere-se ao **PI Engine**, não ao Live Engine. As 1.812 PAS foram criadas pelo **Live Engine** via `run_medium_cycle`.

---

## 6. TABELA DE HIPÓTESES H1–H15

| ID | Hipótese | Veredicto | Evidência |
|----|----------|-----------|-----------|
| **H1** | Status travado por job ausente (sem worker processando PENDING_VALIDATION) | **CONFIRMADA** | Nenhuma função no codebase faz `UPDATE ... SET shadow_validation_status = 'SHADOW_VALIDATED'`. O estado é terminal por ausência de código. |
| **H2** | Status travado por filtro incorreto no worker | **PARCIAL** | O filtro de `run_shadow_calibration_cycle` busca `status='PENDING_SHADOW_VALIDATION'` corretamente. Mas não há 2ª fase que processe `PENDING_VALIDATION`. |
| **H3** | SHADOW_APPLIED não altera profile ativo, apenas cria versão shadow | **CONFIRMADA** | Docstring: "Never changes the live profile config." Não existe UPDATE em `profiles.config` derivado deste pipeline. |
| **H4** | UI confunde version_status com shadow_validation_status | **PARCIAL** | A API `calibration_evolution.py` retorna ambos os campos corretamente. A confusão é do próprio pipeline: `version_status='SHADOW_APPLIED'` + `shadow_validation_status='PENDING_VALIDATION'` é estado paradoxal (dizem que foi aplicado E que ainda está pendente). |
| **H5** | current_value=null impede aplicação correta | **CONFIRMADA** | 100% dos PAS têm `current_value=NULL`. Código: `INSERT ... VALUES (..., null, ...)`. Invalida o diff auditável mas não é a causa primária do deadlock. |
| **H6** | Falta mutation service (só geração de recomendação, sem aplicação real) | **CONFIRMADA** | A função de "aplicação" (`run_shadow_calibration_cycle`) só cria snapshots. Nenhum mutation service aplica mudanças ao `profiles.config`. |
| **H7** | Audit log incompleto | **CONFIRMADA** | `profile_intelligence_audit_log` tem 15 event_types distintos, nenhum é `SHADOW_VALIDATED`, `MUTATION_APPLIED`, `APA_COMPLETED`, `THRESHOLD_CHANGED`. Eventos de Phase 2 simplesmente não existem. |
| **H8** | Indicadores são globais, não por profile | **REFUTADA** | `profile_indicator_performance` tem coluna `profile_id`. Indicadores são por profile. |
| **H9** | Performance ruim gera ajuste genérico | **CONFIRMADA** | 100% dos PAVs têm diff idêntico: `buy_threshold 65→70`. O ajuste é uniforme, sem análise das características individuais de cada profile. Todos recebem +5 independente de qual indicador é problemático. |
| **H10** | Divergência entre banco e código em produção | **REFUTADA** | Schema do banco consistente com o código. Os modelos ORM e as queries batem com as colunas reais. |
| **H11** | Gate de segurança bloqueia aplicação (constraint CHECK) | **CONFIRMADA** | `CHECK (mutation_applied = false OR requires_human_approval = true)` presente na migração 113. Como `requires_human_approval=false` em todos os 1.812 registros, `mutation_applied=true` violaria a constraint. Duplo bloqueio: código + banco. |
| **H12** | Logs existem mas não são exibidos | **REFUTADA** | Os eventos de Phase 2 realmente não existem no banco — não é problema de exibição. |
| **H13** | Rollback disponível falso (diff sem snapshot real) | **CONFIRMADA** | `rollback_available=true` em todos os 1.812 PAVs, mas `before_snapshot` e `after_snapshot` existem. Porém, o rollback só funciona para reverter o config do profile, e o config nunca foi mudado — portanto o rollback é semanticamente sem efeito. |
| **H14** | Dados insuficientes para validação (NO_TRADES após ajuste) | **CONFIRMADA** | A Fase 2 (comparação de trades antes/depois) nunca foi implementada, logo não há gate estatístico de validação. Além disso, o próprio ajuste (threshold do profile) nunca é aplicado, então não há trades "com nova config" para comparar. |
| **H15** | Erro silencioso em transação aborta gravação de status | **REFUTADA** | Audit log mostra `AUTOPILOT_SHADOW_CALIBRATION_APPLIED` sem erros. O pipeline de Fase 1 funciona corretamente — o problema é ausência de Fase 2, não falha silenciosa. |

---

## 7. ACHADOS POR SEVERIDADE

### P0 — Crítico (sistema parado)

**P0.1 — Fase 2 do shadow calibration pipeline não existe**  
Arquivos: `backend/app/services/profile_intelligence_live_service.py`, `backend/app/tasks/profile_intelligence_job.py`  
Impacto: 1.812 PAVs acumulam indefinidamente. Nenhum profile L3 jamais teve threshold ajustado pelo sistema.

**P0.2 — APA stuck em PROCESSING para sempre**  
A transição `PROCESSING → COMPLETED/FAILED` não existe em nenhum arquivo. 1.812 APAs ocupam espaço e distorcem métricas de pipeline.

### P1 — Alto (decisão de design bloqueante)

**P1.1 — Constraint CHECK bloqueia mutation_applied=true**  
`CHECK (mutation_applied = false OR requires_human_approval = true)` + `requires_human_approval=false` → deadlock físico. Mesmo que Fase 2 fosse implementada, o banco rejeitaria a mutation.

**P1.2 — PIAC (ciclo de candidatos) desabilitado por env var**  
`PI_AUTOPILOT_CANDIDATE_CYCLE_ENABLED=false` (default). O sistema alternativo de autopilot de candidatos está explicitamente desabilitado. Apenas 2 ciclos PIAC existem (2026-06-19 e 2026-06-20).

**P1.3 — PI Engine (profile_intelligence_runs) gera 0 sugestões há dias**  
10 runs consecutivas completam com `suggestions_generated=0`. Precisa investigação separada (possível issue com query/threshold no `ProfileIntelligenceService`).

### P2 — Médio (qualidade de dados)

**P2.1 — current_value NULL em 100% dos PAS**  
`INSERT ... current_value=null` hardcoded (linha 371). O campo de "valor antes" da sugestão nunca é populado, impossibilitando auditoria de mudança real.

**P2.2 — Ajuste genérico (buy_threshold 65→70) para todos os profiles**  
+5 no threshold de compra é aplicado uniformemente, sem análise de qual parâmetro causa o baixo win_rate. Um profile com problema de RSI recebe o mesmo ajuste que um com problema de volume.

### P3 — Baixo (observabilidade)

**P3.1 — Dois sistemas nomeados confusamente como "PI"**  
PI Engine (`ProfileIntelligenceService`, usa `profile_intelligence_runs`) vs. PI Live Engine (`profile_intelligence_live_service.py`, usa `profile_adjustment_suggestions`). Sem distinção clara no frontend ou logs.

**P3.2 — rollback_available=true semanticamente vazio**  
Rollback é marcado como disponível em todos os 1.812 PAVs, mas nunca houve mudança no config do profile — não há o que reverter.

---

## 8. VOLUME DE SHADOW TRADES POR PROFILE (Gate Estatístico)

```
[query: SELECT profile_id, source, total_trades, with_outcome, tp_hits, sl_hits, win_rate_pct FROM shadow_trades WHERE profile_id IS NOT NULL AND source IN ('L3','L3_LAB') HAVING with_outcome >= 30 ORDER BY total_trades DESC]

profile_id                            | source  | total | with_outcome | tp   | sl   | win_rate%
--------------------------------------|---------|-------|--------------|------|------|----------
2b70dc42-...-0403cd1e2f54             | L3_LAB  | 1.116 | 952          | 449  | 492  | 47,2%
a565150d-...-d586a37cdf99             | L3_LAB  | 1.041 | 954          | 446  | 497  | 46,7%
2b70dc42-...-0403cd1e2f54             | L3      | 776   | 755          | 265  | 463  | 35,1%
a565150d-...-d586a37cdf99             | L3      | 774   | 759          | 279  | 461  | 36,8%
5bdbefc4-...-b9be1973b7e7             | L3      | 752   | 734          | 261  | 460  | 35,6%
5da37177-...-ff651025be37             | L3      | 548   | 535          | 194  | 335  | 36,3%
e44f3ad2-...-62c63686ee27             | L3_LAB  | 487   | 460          | 235  | 215  | 51,1%
7e2a14d7-...-ebaf39ac6578             | L3_LAB  | 423   | 328          | 124  | 196  | 37,8%
...
```

**Totais por source** [query: SELECT source, total, with_outcome, distinct_profiles]:
```
source       | total  | with_outcome | distinct_profiles
-------------|--------|--------------|------------------
L3           | 11.964 | 10.555       | 43
L3_LAB       | 4.512  | 3.796        | 10
L1_SPECTRUM  | 2.397  | 2.381        | 0
L3_SIMULATED | 1.533  | 1.511        | 0
L3_REJECTED  | 569    | 569          | 0
```

**Viabilidade do gate estatístico:** 43 profiles L3 e 10 profiles L3_LAB têm trades suficientes (≥30 fechados) para validação estatística. Win rates variam de 35% a 51%. O gate é viável para os profiles com maiores volumes, mas a função que executaria a comparação pré/pós não existe.

---

## 9. ESTADO ATUAL DO CICLO

**O ciclo está ativo e gerando novos registros corrompidos diariamente.**

```
2026-06-29: +83 PAVs criados (todos PENDING_VALIDATION, nenhum aplicado)
2026-06-28: +1.469 PAVs criados (todos PENDING_VALIDATION, nenhum aplicado)
2026-06-27: +260 PAVs criados (todos PENDING_VALIDATION, nenhum aplicado)
```

O `feedback_loop` Celery roda, chama `run_medium_cycle` (detecta perfis com win_rate<35%, cria PAS+APA), depois chama `run_shadow_calibration_cycle` (cria PAV e transita para SHADOW_APPLIED/PROCESSING), mas nada mais acontece. Os registros acumulam.

**O ciclo vai continuar gerando registros indefinidamente** enquanto existirem profiles com win_rate < 35% e a Fase 2 não for implementada.

---

## 10. CORREÇÕES RECOMENDADAS (SEM IMPLEMENTAR)

### Imediato (para parar a hemorragia)

**R1 — Desabilitar run_medium_cycle temporariamente**  
Definir `PI_MEDIUM_INTERVAL_M=99999` ou adicionar feature flag `PI_MEDIUM_CYCLE_ENABLED=false` enquanto Fase 2 não está pronta. Evita criar mais PAS/PAV/APA zombies.

**R2 — Limpar backlog acumulado (quando decidir como tratá-lo)**  
Definir uma política: cancelar todos os 1.812 APA com `action_status='CANCELLED'`, marcar PAS como `status='CANCELLED'`, PAV como `shadow_validation_status='CANCELLED'`. Isso requer aprovação de design.

### Correções de design necessárias para a Fase 2

**R3 — Implementar função `run_shadow_validation_cycle`**  
Esta função deve:
1. Query PAVs com `shadow_validation_status='PENDING_VALIDATION'` que tenham `created_at < now() - interval 'X days'` (janela de observação)
2. Comparar win_rate nos shadow_trades do profile ANTES e DEPOIS da criação do PAV
3. Se melhora estatisticamente significante (ex.: win_rate subiu ≥5pp com n≥30 trades): `UPDATE profile_adjustment_versions SET shadow_validation_status='SHADOW_VALIDATED'`
4. Caso contrário: `UPDATE ... SET shadow_validation_status='REJECTED'`
5. Fechar APA: `UPDATE autopilot_pending_actions SET action_status='COMPLETED'`

**R4 — Corrigir a constraint CHECK ou a lógica de requires_human_approval**  
Opção A: mudar constraint para `CHECK (mutation_applied = false OR requires_human_approval = false)` (permite autonomia quando não requer humano)  
Opção B: manter constraint e garantir que toda mutation passe por aprovação humana (`requires_human_approval=true`)  

**R5 — Popular current_value no INSERT de PAS**  
Ler `p.config->'scoring'->'thresholds'->'buy'` antes do INSERT e passar como `current_value`.

**R6 — Implementar aplicação real ao config do profile**  
Apenas após `shadow_validation_status='SHADOW_VALIDATED'` e `mutation_applied` permitido:
```sql
UPDATE profiles 
SET config = jsonb_set(config, '{scoring,thresholds,buy}', :new_value::jsonb),
    updated_at = now()
WHERE id = :profile_id
```

---

## LEDGER DE EVIDÊNCIAS (ANTI-FABRICAÇÃO)

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|-----------------|--------|------------------------|
| 1.812 PAVs em PENDING_VALIDATION | [query] `SELECT COUNT(*) FROM profile_adjustment_versions WHERE shadow_validation_status='PENDING_VALIDATION'` | `total: 1812` |
| 1.812 PASs em SHADOW_APPLIED | [query] `SELECT status, COUNT(*) FROM profile_adjustment_suggestions GROUP BY status` | `{'status': 'SHADOW_APPLIED', 'total': 1812}` |
| 1.812 APAs em PROCESSING | [query] `SELECT action_status, COUNT(*) FROM autopilot_pending_actions GROUP BY action_status` | `{'action_status': 'PROCESSING', 'total': 1812}` |
| mutation_applied=false em 100% | [query] `COUNT(*) FILTER (WHERE mutation_applied=true)` | `0` |
| current_value=null em 100% PAS | [query] `COUNT(*) FILTER (WHERE current_value IS NULL)` | `1812` |
| diff uniforme buy 65→70 | [query] `SELECT diff::text, COUNT(*) GROUP BY diff::text` | `1812 registros, 1 único valor` |
| 0 sugestões em 10 runs PI Engine | [query] `SELECT suggestions_generated FROM profile_intelligence_runs ORDER BY run_at DESC LIMIT 10` | todos retornam `0` |
| Constraint CHECK linha 154-155 | [código] `alembic/versions/113_pi_live_engine.py` | `CHECK (mutation_applied = false OR requires_human_approval = true)` |
| Never sets mutation_applied=true | [código] `profile_intelligence_live_service.py`, linha 449 | docstring literal |
| 43 profiles L3 com trades | [query] shadow_trades GROUP BY profile_id,source HAVING with_outcome>=30 | retornou 30 rows no LIMIT 30 |

---

```
AUDITORIA CONCLUÍDA — SEM ALTERAÇÕES APLICADAS

Causa raiz principal:
  run_shadow_calibration_cycle implementa apenas Fase 1 (criar snapshot e marcar SHADOW_APPLIED).
  A Fase 2 (validar shadow performance, transitar shadow_validation_status, fechar APA) 
  nunca foi implementada. Não existe nenhuma função em todo o backend que faça
  UPDATE profile_adjustment_versions SET shadow_validation_status = 'SHADOW_VALIDATED'.

Causas contribuintes:
  1. Constraint CHECK bloqueia mutation_applied=true para requires_human_approval=false
  2. current_value=NULL hardcoded em todos os PAS (linha 371 do serviço)
  3. PIAC desabilitado por env var (PI_AUTOPILOT_CANDIDATE_CYCLE_ENABLED=false)
  4. PI Engine (sistema separado) gera 0 sugestões há pelo menos 10 runs

A única coisa a fazer primeiro:
  Desabilitar run_medium_cycle (setar PI_MEDIUM_INTERVAL_M=99999 ou feature flag)
  para parar de criar novos PAV/PAS/APA zombies enquanto a Fase 2 não existe.
  O segundo passo é implementar run_shadow_validation_cycle com gate estatístico real.
```
