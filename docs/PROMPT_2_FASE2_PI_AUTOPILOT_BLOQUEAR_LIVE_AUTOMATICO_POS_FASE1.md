# PROMPT 2 — Correção P0 do PI Auto-Pilot: bloquear promoção live automática mantendo Shadow ativo

## Objetivo

Você é um engenheiro sênior Python/FastAPI/PostgreSQL especializado em sistemas de trading, Auto-Pilot, governança de produção, shadow testing, audit log, Alembic, rollback e segurança operacional.

Preciso que você execute a **Fase 2** do plano de correção do Scalpyn.

A correção deve manter o **Profile Intelligence Auto-Pilot ativo**, mantendo criação de candidatos, coleta shadow e comparação contra incumbent, mas deve impedir que qualquer candidato seja promovido automaticamente para live sem aprovação humana explícita.

---

## 0. Contexto atualizado após a Fase 1

A **Fase 1 — Correção P0 do Auto-Pilot Legado com Escopo Obrigatório** já foi implementada em código.

Resultado reportado:

- Auto-Pilot legado continua ativo.
- Mutação profile-scoped válida continua permitida.
- Mutação global/misturada foi bloqueada.
- `user_id` e `profile_id` agora são obrigatórios para mutações.
- Queries de performance, rejected trades, behavioral circuit breaker e rule insights foram filtradas por `user_id + profile_id`.
- Escritas auxiliares em `config_profiles` foram bloqueadas porque a tabela não possui `profile_id`.
- Auditoria `MUTATED` agora registra `before`, `after`, `diff`, janela, evidências, alvo e status real da mutação.
- Foram criados eventos seguros `AUTOPILOT_SCOPE_BLOCKED`.
- Testes: `38 passed`.
- Ruff, compileall e git diff passaram.
- PI Auto-Pilot não foi alterado na Fase 1.
- LightGBM/CatBoost não foram alterados.
- Nenhuma migration foi executada ainda.

Arquivos alterados na Fase 1:

```text
backend/app/services/autopilot_engine.py
backend/app/api/autopilot.py
backend/app/tasks/autopilot.py
backend/alembic/versions/094_autopilot_scope_audit.py
backend/tests/test_autopilot_scope_p0.py
```

Riscos remanescentes da Fase 1 que impactam esta Fase 2:

```text
A migration 094 precisa ser aplicada antes do deploy do novo código.
O repositório já possui cabeças Alembic paralelas.
A estratégia de deploy precisa selecionar/reconciliar os heads.
Trades históricos sem profile_id serão ignorados pelo Auto-Pilot legado.
```

---

## 0.1 Regra obrigatória sobre Alembic antes de criar qualquer migration

Antes de criar qualquer nova migration nesta Fase 2:

1. Verificar o estado atual do Alembic.
2. Identificar todas as heads existentes.
3. Confirmar se a migration `094_autopilot_scope_audit.py` da Fase 1 existe e está preservada.
4. Não criar uma nova head paralela sem explicar a estratégia.
5. Se existirem múltiplas heads, propor uma das opções:

```text
Opção A — criar migration de merge explícita
Opção B — basear a nova migration na head correta
Opção C — não criar migration nesta fase e usar somente campos existentes
```

6. Não executar migrations automaticamente.
7. Não apagar, renomear ou sobrescrever a migration 094.
8. Se nova migration for necessária, ela deve ser pequena, reversível e não destrutiva.

Entregar no relatório:

```text
alembic current
alembic heads
alembic history relevante
estratégia adotada
se nova migration foi criada ou não
se existe risco de múltiplas heads
```

---

# 1. Objetivo principal

Manter o **Profile Intelligence Auto-Pilot** funcionando em modo de análise, criação de candidatos e coleta shadow, mas bloquear a transição automática para produção/live.

O sistema deve continuar podendo:

- criar candidatos;
- colocar candidatos em shadow;
- coletar performance shadow;
- comparar candidato contra incumbent;
- gerar evidência;
- registrar auditoria;
- preparar recomendação de promoção;
- manter rollback disponível;
- manter jobs/workers ativos.

O sistema **não deve**:

- trocar automaticamente o profile live;
- ativar live trading automaticamente;
- alterar `live_watchlist.profile_id` sem aprovação humana;
- definir `live_trading_enabled=true` sem aprovação humana;
- promover candidato para `LIVE_ACTIVATED` sem aprovação humana;
- usar apenas métricas/gates para produção automática.

---

# 2. Contexto do problema

A auditoria identificou que o PI Auto-Pilot possui um fluxo melhor que o Auto-Pilot legado em termos de shadow, candidatos, auditoria e rollback, mas ainda existe um risco crítico:

```text
O código permite promoção automática para live.
```

Fluxo de risco:

```text
Candidate criado em shadow
↓
Shadow coleta métricas
↓
Gates são considerados aprovados
↓
Sistema promove automaticamente para live
↓
Watchlist/profile live é alterado sem aprovação humana específica
```

Isso ainda é perigoso porque o Profile Intelligence possui pendências estruturais:

- Dynamic Combinations ainda precisam de validação temporal suficiente;
- suggestions ainda não possuem source registry completo;
- `source_profiles` ainda está incompleto;
- origem de modelo/técnica ainda não está normalizada;
- política de aprovação explícita ainda não está completa;
- governança de produção ainda não está finalizada;
- champion/challenger registry ainda não foi implementado.

---

# 3. Diretriz principal

**Não desligar o PI Auto-Pilot.**

Este prompt não deve:

- desativar o Profile Intelligence;
- desativar o PI Auto-Pilot;
- impedir criação de candidatos shadow;
- impedir coleta shadow;
- apagar candidatos existentes;
- apagar histórico de auditoria;
- remover rollback;
- desligar worker/scheduler;
- alterar Auto-Pilot legado;
- alterar a correção da Fase 1;
- alterar LightGBM;
- alterar CatBoost;
- alterar Dynamic Combinations;
- alterar Association Rules;
- alterar Optuna;
- implementar Champion Registry;
- implementar Suggestion Registry completo;
- re-treinar ML;
- alterar XGBoost;
- alterar thresholds operacionais de trading.

A correção é cirúrgica:

```text
bloquear somente a transição automática para live
```

---

# 4. Escopo técnico obrigatório

Auditar e corrigir principalmente:

```text
backend/app/services/profile_intelligence_autopilot_service.py
```

Buscar todas as ocorrências de:

```text
LIVE_ACTIVATED
live_trading_enabled
live_watchlist
profile_id
promote
promotion
activate
activation
rollback
SHADOW_COLLECTING
SHADOW_READY
APPROVED
candidate
incumbent
```

Também revisar:

```text
backend/app/api/profile_intelligence.py
backend/app/models/profile_intelligence.py
backend/app/schemas/profile_intelligence.py
frontend/app/profile-intelligence/page.tsx
backend/alembic/versions/
qualquer migration relacionada ao PI Auto-Pilot
qualquer worker/scheduler que execute PI Auto-Pilot
```

---

# 5. Regra de negócio obrigatória

Nenhum candidato pode virar live sem aprovação humana explícita.

Regra central:

```text
candidate_can_be_live =
    approval_status == APPROVED_FOR_LIVE
    AND approved_by IS NOT NULL
    AND approved_at IS NOT NULL
    AND approval_reason IS NOT NULL
    AND rollback_payload IS NOT NULL
```

Se qualquer condição falhar:

```text
candidate_can_be_live = false
```

O sistema deve continuar coletando shadow e gerando recomendação.

---

# 6. Estados recomendados

Adicionar ou normalizar estados do candidato:

```text
SHADOW_COLLECTING
SHADOW_READY
PENDING_HUMAN_APPROVAL
APPROVED_FOR_LIVE
LIVE_ACTIVATED
REJECTED
ROLLED_BACK
EXPIRED
BLOCKED
```

Fluxo desejado:

```text
CANDIDATE_CREATED
↓
SHADOW_COLLECTING
↓
SHADOW_READY
↓
PENDING_HUMAN_APPROVAL
↓
APPROVED_FOR_LIVE
↓
LIVE_ACTIVATED
```

Fluxo proibido:

```text
SHADOW_READY
↓
LIVE_ACTIVATED
```

Também é proibido:

```text
SHADOW_COLLECTING
↓
LIVE_ACTIVATED
```

---

# 7. Comportamento esperado

## 7.1 Quando candidato passar nos gates

Se um candidato tiver métricas suficientes e passar nos gates, o sistema deve:

1. Não promover para live automaticamente.
2. Não alterar `live_watchlist.profile_id`.
3. Não alterar `live_trading_enabled`.
4. Alterar status para:

```text
PENDING_HUMAN_APPROVAL
```

5. Registrar evento:

```text
LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL
```

6. Salvar evidências para aprovação:

```text
candidate_id
incumbent_profile_id
candidate_profile_id
shadow_metrics
comparison_metrics
risk_summary
expected_impact
rollback_payload
approval_required=true
mutation_applied=false
```

7. Exibir na UI como:

```text
Aguardando aprovação humana
```

---

## 7.2 Quando houver aprovação humana

A aprovação humana deve ser uma ação explícita.

A aprovação deve registrar:

```text
approved_by
approved_at
approval_reason
approval_source
approval_snapshot
confirm_risk=true
```

Depois da aprovação, o candidato pode passar para:

```text
APPROVED_FOR_LIVE
```

A aprovação **não deve necessariamente ativar live na mesma operação**, a menos que exista parâmetro explícito e seguro.

Modelo preferido:

```text
approve
↓
APPROVED_FOR_LIVE
↓
activate
↓
LIVE_ACTIVATED
```

---

## 7.3 Quando houver ativação live

A ativação live deve ser uma etapa separada.

A ativação só pode acontecer se:

```text
state == APPROVED_FOR_LIVE
approved_by IS NOT NULL
approved_at IS NOT NULL
approval_reason IS NOT NULL
rollback_payload IS NOT NULL
candidate não expirado
candidate não rejeitado
candidate não está live
```

A ativação live deve registrar:

```text
before_json
after_json
diff_json
rollback_payload
mutation_applied=true
```

---

## 7.4 Quando não houver rollback payload

Mesmo com aprovação humana, não ativar live se não houver rollback.

Registrar:

```text
LIVE_ACTIVATION_BLOCKED_MISSING_ROLLBACK
```

Resultado esperado:

```text
state não vira LIVE_ACTIVATED
live_watchlist.profile_id não muda
live_trading_enabled não muda
mutation_applied=false
```

---

# 8. Alterações técnicas esperadas

## 8.1 Bloquear promoção automática

Encontrar o trecho que faz a troca automática do profile live, especialmente operações semelhantes a:

```python
live_watchlist.profile_id = candidate_profile_id
live_trading_enabled = True
candidate.state = "LIVE_ACTIVATED"
```

Alterar para algo equivalente a:

```python
candidate.state = "PENDING_HUMAN_APPROVAL"
candidate.approval_required = True
log_event("LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL")
return safe_result(...)
```

Não executar a troca live nesse momento.

---

## 8.2 Criar função explícita de aprovação

Criar ou ajustar função/endpoint para aprovação humana:

```python
approve_candidate_for_live(candidate_id, approved_by, approval_reason, confirm_risk)
```

Regras:

- `approved_by` obrigatório;
- `approval_reason` obrigatório;
- `confirm_risk == true` obrigatório;
- candidato precisa estar em `PENDING_HUMAN_APPROVAL`;
- candidato precisa ter métricas shadow suficientes;
- candidato precisa ter rollback payload;
- candidato não pode estar expirado;
- candidato não pode estar rejeitado;
- candidato não pode já estar live.

Resultado esperado:

```text
state = APPROVED_FOR_LIVE
```

---

## 8.3 Criar função separada de ativação live

Criar ou ajustar função:

```python
activate_approved_candidate(candidate_id)
```

Regras:

- candidato precisa estar `APPROVED_FOR_LIVE`;
- precisa ter `approved_by`;
- precisa ter `approved_at`;
- precisa ter `approval_reason`;
- precisa ter `rollback_payload`;
- precisa gerar `before_json`;
- precisa gerar `after_json`;
- precisa gerar `diff_json`;
- precisa registrar audit log append-only;
- precisa permitir rollback.

Resultado esperado:

```text
LIVE_ACTIVATED
```

---

## 8.4 Garantir audit log completo

Toda tentativa de promoção deve registrar evento.

Eventos mínimos:

```text
CANDIDATE_CREATED
SHADOW_COLLECTING
SHADOW_READY
LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL
CANDIDATE_APPROVED_FOR_LIVE
LIVE_ACTIVATED
LIVE_ACTIVATION_BLOCKED_MISSING_ROLLBACK
CANDIDATE_REJECTED
CANDIDATE_ROLLED_BACK
```

Campos obrigatórios recomendados:

```text
event_type
candidate_id
user_id
incumbent_profile_id
candidate_profile_id
before_json
after_json
diff_json
shadow_metrics
comparison_metrics
reason_code
approval_required
approved_by
approved_at
approval_reason
rollback_payload
mutation_applied
created_at
```

Para eventos bloqueados, `before_json/after_json/diff_json` podem representar a tentativa planejada, mas deve ficar claro que:

```text
mutation_applied = false
```

---

# 9. Alterações de banco esperadas

Verificar se as colunas já existem. Se não existirem, propor migration segura.

Campos recomendados para candidatos:

```text
approval_status
approval_required
approved_by
approved_at
approval_reason
approval_source
approval_snapshot_json
promotion_blocked_reason
rollback_payload
live_activation_attempted_at
live_activated_at
```

Status possíveis para `approval_status`:

```text
not_required
pending
approved
rejected
expired
```

Regras:

- Não executar migration sem autorização.
- Não criar migration antes de validar heads Alembic.
- Não criar nova head paralela sem estratégia.
- Se migration for necessária, criar migration pequena, reversível e não destrutiva.
- Não apagar dados históricos.
- Preservar `094_autopilot_scope_audit.py`.

---

# 10. Alterações de API esperadas

Criar ou ajustar endpoints:

```text
POST /api/profile-intelligence/autopilot/candidates/{candidate_id}/approve
POST /api/profile-intelligence/autopilot/candidates/{candidate_id}/reject
POST /api/profile-intelligence/autopilot/candidates/{candidate_id}/activate
POST /api/profile-intelligence/autopilot/candidates/{candidate_id}/rollback
```

Regras:

- `approve` não deve ativar live automaticamente por padrão.
- `activate` só funciona se candidato já estiver aprovado.
- `rollback` deve continuar funcionando.
- `reject` deve impedir ativação futura.
- Todas as ações devem registrar audit log.
- Toda ação precisa validar ownership/user scope.

Payload mínimo de aprovação:

```json
{
  "approved_by": "user_or_admin_id",
  "approval_reason": "Motivo da aprovação",
  "confirm_risk": true
}
```

---

# 11. Alterações de UI esperadas

Na tela do Profile Intelligence / Auto-Pilot, exibir candidatos com status:

```text
Shadow collecting
Ready for review
Pending human approval
Approved for live
Live activated
Rejected
Rolled back
Expired
Blocked
```

Para candidato `PENDING_HUMAN_APPROVAL`, mostrar:

- profile candidato;
- profile incumbent;
- métricas shadow;
- comparação com incumbent;
- janela analisada;
- número de trades;
- expected impact;
- riscos;
- rollback disponível;
- botão Aprovar;
- botão Rejeitar;
- botão Ver Detalhes.

O botão Aprovar deve exigir confirmação explícita:

```text
Confirmo que revisei as métricas shadow e autorizo este candidato para ativação live.
```

A UI deve deixar claro:

```text
Aprovar não é a mesma coisa que ativar live, salvo se o fluxo implementado exigir confirmação dupla.
```

---

# 12. Testes obrigatórios

Criar ou corrigir testes automatizados.

## Teste 1 — Candidato aprovado nos gates não vira live automaticamente

Cenário:

```text
candidate passa métricas/gates
```

Esperado:

```text
state = PENDING_HUMAN_APPROVAL
live_watchlist.profile_id não muda
live_trading_enabled não muda
event = LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL
mutation_applied = false
```

---

## Teste 2 — Aprovação humana muda status para APPROVED_FOR_LIVE

Cenário:

```text
candidate em PENDING_HUMAN_APPROVAL
approve_candidate_for_live(...)
```

Esperado:

```text
state = APPROVED_FOR_LIVE
approved_by != null
approved_at != null
approval_reason != null
live ainda não muda
```

---

## Teste 3 — Ativação live exige aprovação

Cenário:

```text
activate_candidate(candidate sem aprovação)
```

Esperado:

```text
activation blocked
reason = missing_human_approval
live_watchlist.profile_id não muda
mutation_applied = false
```

---

## Teste 4 — Ativação live aprovada muda associação

Cenário:

```text
candidate APPROVED_FOR_LIVE
rollback_payload existe
activate_approved_candidate(...)
```

Esperado:

```text
state = LIVE_ACTIVATED
live_watchlist.profile_id = candidate_profile_id
audit log contém before/after/diff
rollback_payload salvo
mutation_applied = true
```

---

## Teste 5 — Ativação live sem rollback é bloqueada

Cenário:

```text
candidate aprovado
rollback_payload ausente
```

Esperado:

```text
activation blocked
reason = missing_rollback_payload
state não vira LIVE_ACTIVATED
mutation_applied = false
```

---

## Teste 6 — Rejeitado não pode virar live

Cenário:

```text
candidate REJECTED
activate_approved_candidate(...)
```

Esperado:

```text
activation blocked
reason = candidate_rejected
```

---

## Teste 7 — Rollback continua funcionando

Cenário:

```text
candidate LIVE_ACTIVATED
rollback_candidate(...)
```

Esperado:

```text
live_watchlist.profile_id volta para incumbent_profile_id
state = ROLLED_BACK
audit log registra rollback
```

---

## Teste 8 — Alembic não cria head paralela sem estratégia

Cenário:

```text
múltiplas heads existentes
nova migration necessária
```

Esperado:

```text
executor reporta heads
não cria migration paralela sem estratégia
preserva migration 094
```

---

# 13. Auditoria pós-correção

Depois de aplicar a correção, rodar auditoria read-only para provar:

1. Nenhum candidato pode ir de `SHADOW_READY` direto para `LIVE_ACTIVATED`.
2. Nenhum candidato pode ir de `SHADOW_COLLECTING` direto para `LIVE_ACTIVATED`.
3. Todo `LIVE_ACTIVATED` exige `approved_by`.
4. Todo `LIVE_ACTIVATED` exige `approved_at`.
5. Todo `LIVE_ACTIVATED` exige `approval_reason`.
6. Todo `LIVE_ACTIVATED` exige `rollback_payload`.
7. Eventos de bloqueio são registrados.
8. Shadow candidates continuam sendo criados.
9. Coleta shadow continua funcionando.
10. Rollback continua funcionando.
11. Nenhuma alteração foi feita no Auto-Pilot legado.
12. Nenhuma alteração foi feita em LightGBM/CatBoost.
13. Nenhum modelo ML foi re-treinado.
14. Migration 094 foi preservada.
15. Nenhuma nova head Alembic paralela foi criada sem explicação.

---

# 14. Critérios de aceite

A correção só estará aceita se:

1. PI Auto-Pilot continuar ativo.
2. Candidatos shadow continuarem sendo criados.
3. Candidatos shadow continuarem coletando métricas.
4. Candidato não virar live automaticamente.
5. Aprovação humana for obrigatória.
6. Ativação live for etapa separada da aprovação, ou exigir confirmação dupla inequívoca.
7. Rollback payload for obrigatório para live.
8. Audit log registrar bloqueios, aprovações, ativações e rollback.
9. Testes novos passarem.
10. Testes existentes relevantes passarem.
11. UI deixar claro que o candidato está aguardando aprovação.
12. Migration 094 da Fase 1 permanecer preservada.
13. Estratégia Alembic ficar documentada.
14. Relatório final listar arquivos alterados, testes rodados e riscos remanescentes.

---

# 15. Entrega esperada

Entregar um relatório final em Markdown com esta estrutura:

```markdown
# Correção P0 — PI Auto-Pilot sem Promoção Live Automática

## 1. Resumo Executivo
- O que foi corrigido
- Por que o PI Auto-Pilot continua ativo
- O que continua funcionando em shadow
- O que agora exige aprovação humana

## 2. Contexto da Fase 1
- Confirmação de que o Auto-Pilot legado não foi alterado
- Confirmação de que a migration 094 foi preservada
- Situação das heads Alembic

## 3. Bug Original
- Arquivo
- Função
- Linha aproximada
- Comportamento anterior
- Risco

## 4. Correção Aplicada
- Bloqueio de promoção automática
- Novo fluxo de aprovação
- Novo fluxo de ativação live
- Audit log
- Rollback

## 5. Arquivos Alterados
| Arquivo | Alteração | Motivo |
|---|---|---|

## 6. Migrations
| Migration | Necessária? | Motivo | Reversível? | Head usada |
|---|---|---|---|---|

## 7. Alembic
- heads antes
- heads depois
- migration 094 preservada?
- estratégia adotada

## 8. Testes Criados/Alterados
| Teste | Resultado |
|---|---|

## 9. Evidências
- Trecho de código que bloqueia auto-live
- Exemplo de candidato indo para PENDING_HUMAN_APPROVAL
- Exemplo de aprovação humana
- Exemplo de live activation aprovada
- Exemplo de rollback

## 10. Riscos Remanescentes
- Corrigir toggles órfãos LightGBM/CatBoost
- Validar Dynamic Combinations fora da amostra
- Criar Suggestion Registry
- Criar Champion Registry
- Corrigir source_profiles no Profile Intelligence

## 11. Veredito
- PI Auto-Pilot continua ativo?
- Candidatos shadow continuam?
- Live automático foi bloqueado?
- Aprovação humana é obrigatória?
- Rollback permanece funcional?
- Testes passaram?
- Alembic está seguro?
```

---

# 16. Restrições explícitas

Não fazer neste prompt:

- Não desligar PI Auto-Pilot.
- Não desligar Profile Intelligence.
- Não alterar Auto-Pilot legado.
- Não alterar a correção da Fase 1.
- Não apagar a migration 094.
- Não criar nova head Alembic paralela sem estratégia.
- Não mexer em LightGBM.
- Não mexer em CatBoost.
- Não implementar Champion Registry.
- Não implementar Suggestion Registry completo.
- Não alterar Dynamic Combinations.
- Não alterar Association Rules.
- Não alterar Optuna.
- Não re-treinar ML.
- Não alterar XGBoost.
- Não alterar thresholds de trading.
- Não apagar candidatos existentes.
- Não apagar histórico de auditoria.

Este prompt é exclusivamente para bloquear promoção live automática do PI Auto-Pilot, mantendo shadow, candidatos, auditoria e rollback.

---

# 17. Veredito esperado

Ao final, o sistema deve ficar assim:

```text
PI Auto-Pilot: ativo
Criação de candidatos shadow: ativa
Coleta shadow: ativa
Comparação candidato vs incumbent: ativa
Promoção live automática: bloqueada
Aprovação humana: obrigatória
Ativação live: separada da aprovação
Rollback: obrigatório e funcional
Migration 094 da Fase 1: preservada
Alembic: sem nova head paralela sem estratégia
```

Objetivo final:

```text
Manter o PI Auto-Pilot evoluindo candidatos, mas impedir que qualquer hipótese estatística vire produção sem aprovação explícita.
```
