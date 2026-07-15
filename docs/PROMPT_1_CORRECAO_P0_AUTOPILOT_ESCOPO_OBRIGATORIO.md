# PROMPT 1 — Correção P0 do Auto-Pilot Legado mantendo o Auto-Pilot ativo

## Objetivo

Você é um engenheiro sênior Python/FastAPI/PostgreSQL especializado em sistemas de trading, Auto-Pilot de regras, auditoria de mutações, segurança operacional e pipelines quantitativos.

Preciso que você corrija o achado crítico **C-01** da auditoria do Scalpyn, mantendo o **Auto-Pilot legado ativo**.

A correção deve impedir que o Auto-Pilot use performance global, agregada ou não segregada para alterar um profile específico.

---

## 1. Contexto da auditoria

A auditoria identificou que o Auto-Pilot legado está ativo e pode executar escrita real, mas a performance usada para orientar mutações pode ser calculada sem escopo correto.

Achado crítico:

```text
compute_performance_window(..., user_id=None)
```

Problemas associados:

- A função aceita `user_id` ausente.
- O filtro por `user_id` só existe se `user_id` for informado.
- A chamada principal do ciclo não garante `user_id`.
- A mutação pode ser atribuída a um profile/config específico.
- A evidência usada pode vir de performance global/agregada.
- O Auto-Pilot possui autoridade para alterar:
  - `scoring_rules`
  - `minimum_score`
  - `block_rules`
  - `entry_triggers`

Risco operacional:

```text
Performance global ou misturada
↓
Auto-Pilot interpreta como evidência
↓
Auto-Pilot gera ajuste
↓
Ajuste é aplicado em um profile específico
↓
Profile é contaminado por uma conclusão que não veio dele
```

---

## 2. Diretriz principal

**Não desligar o Auto-Pilot.**

Este prompt não deve:

- forçar `dry_run_mode=true` globalmente;
- definir `autopilot_enabled=false`;
- remover permissões atuais;
- ativar kill-switch global;
- desativar scheduler;
- remover a capacidade de mutação quando houver escopo válido.

O objetivo é manter o Auto-Pilot funcionando, mas impedir mutação quando o escopo estiver ausente, inconsistente ou sem amostra suficiente.

---

## 3. Escopo técnico obrigatório

Auditar e corrigir principalmente:

```text
backend/app/services/autopilot_engine.py
```

Buscar todas as ocorrências e fluxos relacionados a:

```text
compute_performance_window
run_autopilot_cycle
scope_profile_id
profile_id
user_id
autopilot_can_adjust
autopilot_full_authority
dry_run_mode
MUTATED
ROLLED_BACK
before_json
after_json
diff_json
performance_window
```

Também revisar qualquer endpoint, worker, scheduler ou job que invoque o Auto-Pilot legado.

---

## 4. Regra de negócio obrigatória

Se o Auto-Pilot pretende alterar qualquer configuração/profile, então os seguintes campos devem ser obrigatórios:

```text
user_id
profile_id
performance_window
evidence_count
before_json
after_json
diff_json
```

Regra central:

```text
mutation_allowed = user_id válido
                   AND profile_id válido
                   AND performance calculada apenas para esse user_id + profile_id
                   AND amostra mínima suficiente
                   AND audit payload completo
```

Se qualquer condição falhar:

```text
mutation_allowed = false
```

O Auto-Pilot deve continuar ativo, mas não deve aplicar alteração.

---

## 5. Comportamento esperado

### 5.1 Ciclo com escopo válido

Quando houver:

```text
user_id válido
profile_id válido
dados fechados suficientes
config alvo identificada
```

O Auto-Pilot deve:

1. Calcular performance apenas daquele `user_id + profile_id`.
2. Gerar insights apenas com dados daquele profile.
3. Avaliar mutação apenas para aquele profile/config.
4. Aplicar mutação se os guardrails permitirem.
5. Registrar audit log completo.

Audit log obrigatório para mutação:

```json
{
  "event_type": "MUTATED",
  "user_id": "...",
  "profile_id": "...",
  "target_config": "...",
  "target_section": "...",
  "before_json": {},
  "after_json": {},
  "diff_json": {},
  "performance_window": {
    "start": "...",
    "end": "...",
    "source": "...",
    "closed_trades": 0,
    "profile_id": "...",
    "user_id": "..."
  },
  "evidence_count": 0,
  "reason_code": "...",
  "confidence": 0.0,
  "mutation_applied": true
}
```

### 5.2 Ciclo sem escopo válido

Quando faltar `user_id` ou `profile_id`, o Auto-Pilot deve:

1. Continuar vivo.
2. Não derrubar worker.
3. Não desligar scheduler.
4. Não aplicar mutação.
5. Registrar evento auditável.
6. Retornar status seguro.

Evento recomendado:

```text
AUTOPILOT_SCOPE_BLOCKED
```

Exemplo de retorno:

```json
{
  "status": "blocked",
  "reason": "missing_profile_id",
  "mutation_applied": false,
  "autopilot_still_active": true
}
```

Motivos possíveis:

```text
missing_user_id
missing_profile_id
missing_user_and_profile
insufficient_scoped_sample
missing_audit_payload
invalid_scope
profile_not_found
no_closed_trades_for_scope
```

---

## 6. Alterações técnicas obrigatórias

### 6.1 Criar erro específico de escopo

Criar uma exceção específica ou equivalente:

```python
class AutopilotScopeError(Exception):
    pass
```

Ou usar uma estrutura de resultado segura:

```python
@dataclass
class AutopilotScopeValidationResult:
    ok: bool
    reason: str | None
    user_id: str | None
    profile_id: str | None
```

O importante é impedir fallback silencioso para global.

---

### 6.2 Tornar `user_id` obrigatório para performance de mutação

Se a função atual aceita:

```python
compute_performance_window(..., user_id=None)
```

Alterar para impedir uso em modo de mutação sem `user_id`.

Comportamento desejado:

```python
if mutation_context and not user_id:
    raise AutopilotScopeError("user_id is required for mutation performance window")
```

Ou:

```python
if mutation_context and not user_id:
    return blocked_result("missing_user_id")
```

---

### 6.3 Tornar `profile_id` obrigatório para mutação

Antes de qualquer alteração de:

```text
scoring_rules
minimum_score
block_rules
entry_triggers
thresholds
weights
profile config
```

Validar:

```python
if not profile_id:
    return blocked_result("missing_profile_id")
```

---

### 6.4 Filtrar queries por `user_id + profile_id`

Toda query usada para performance, win rate, drawdown, TP/SL, expected impact, rule insight ou qualquer métrica que alimente mutação deve conter:

```sql
WHERE user_id = :user_id
  AND profile_id = :profile_id
```

ou equivalente ORM.

Validar tabelas possíveis:

- `shadow_trades`
- `config_profiles`
- `profile_intelligence_*`
- `autopilot_audit`
- qualquer tabela usada no cálculo de performance.

Se a tabela não possuir `profile_id`, ela não pode ser usada como evidência direta para mutação de profile.

---

### 6.5 Proibir fallback global para mutação

Proibir qualquer comportamento como:

```text
profile_id ausente → usa todos os profiles
user_id ausente → usa todos os usuários
sem trades no profile → usa global
sem amostra suficiente → usa média global
```

Fallback global pode existir apenas para relatório analítico, nunca para mutação.

Implementar uma separação clara:

```text
analysis_global_allowed = true
mutation_global_allowed = false
```

---

### 6.6 Criar amostra mínima escopada

Adicionar configuração ou constante:

```text
AUTOPILOT_MIN_SCOPED_CLOSED_TRADES
```

Sugestão inicial:

```text
30
```

Se a amostra do profile for inferior ao mínimo:

```json
{
  "status": "blocked",
  "reason": "insufficient_scoped_sample",
  "mutation_applied": false,
  "closed_trades": 17,
  "min_required": 30
}
```

Não usar global como substituto.

---

### 6.7 Garantir audit payload completo

Toda ação `MUTATED` deve conter:

```text
before_json
after_json
diff_json
user_id
profile_id
reason_code
performance_window
evidence_count
```

Se algum campo obrigatório estiver ausente:

```text
não aplicar mutação
registrar AUTOPILOT_MUTATION_BLOCKED_MISSING_AUDIT_PAYLOAD
```

---

## 7. Requisitos de auditoria pós-correção

Depois de aplicar a correção, provar com evidências:

1. Não existe mutação sem `user_id`.
2. Não existe mutação sem `profile_id`.
3. `compute_performance_window` não calcula performance global para mutação.
4. Toda query de performance usada para mutação filtra `user_id`.
5. Toda query de performance usada para mutação filtra `profile_id`.
6. Evento bloqueado é registrado quando falta escopo.
7. Evento mutado possui `before_json`, `after_json` e `diff_json`.
8. Nenhuma alteração foi feita em LightGBM/CatBoost neste prompt.
9. Nenhuma alteração foi feita no PI Auto-Pilot neste prompt.
10. Nenhuma migration destrutiva foi executada.

---

## 8. Testes obrigatórios

Criar ou corrigir testes automatizados.

### Teste 1 — Bloqueia mutação sem `user_id`

Cenário:

```text
run_autopilot_cycle(profile_id válido, user_id ausente)
```

Esperado:

```text
mutation_applied = false
status = blocked
reason = missing_user_id
audit event = AUTOPILOT_SCOPE_BLOCKED
```

---

### Teste 2 — Bloqueia mutação sem `profile_id`

Cenário:

```text
run_autopilot_cycle(user_id válido, profile_id ausente)
```

Esperado:

```text
mutation_applied = false
status = blocked
reason = missing_profile_id
audit event = AUTOPILOT_SCOPE_BLOCKED
```

---

### Teste 3 — Não usa fallback global

Cenário:

```text
profile A tem poucos trades
global tem muitos trades
Auto-Pilot roda para profile A
```

Esperado:

```text
não usar global
não mutar profile A
reason = insufficient_scoped_sample
```

---

### Teste 4 — Mutação válida com escopo correto

Cenário:

```text
user_id válido
profile_id válido
trades fechados suficientes
```

Esperado:

```text
performance usa apenas profile_id alvo
mutação permitida
audit log contém before_json, after_json e diff_json
```

---

### Teste 5 — Não contamina profile A com dados do profile B

Cenário:

```text
profile A tem performance ruim
profile B tem performance boa
Auto-Pilot roda para profile A
```

Esperado:

```text
somente dados do profile A entram no cálculo
profile B não influencia nenhuma decisão
```

---

### Teste 6 — Toda ação `MUTATED` possui auditoria completa

Validar:

```text
before_json != null
after_json != null
diff_json != null
user_id != null
profile_id != null
evidence_count != null
performance_window != null
```

---

## 9. Critérios de aceite

A correção só estará aceita se:

1. Auto-Pilot continuar ativo.
2. Auto-Pilot não for colocado em `dry_run` global.
3. Mutação real continuar possível quando houver escopo válido.
4. Mutação real for bloqueada quando escopo estiver ausente/inválido.
5. Nenhuma performance global puder orientar mutação de profile específico.
6. Não existir fallback silencioso para global.
7. Testes novos passarem.
8. Testes existentes relevantes passarem.
9. Audit log ficar mais completo.
10. Logs deixarem claro quando uma mutação foi bloqueada.
11. O relatório final listar arquivos alterados, testes rodados e riscos remanescentes.

---

## 10. Entrega esperada

Entregar um relatório final em Markdown com esta estrutura:

```markdown
# Correção P0 — Auto-Pilot Legado com Escopo Obrigatório

## 1. Resumo Executivo
- O que foi corrigido
- Por que o Auto-Pilot continua ativo
- Em quais condições ele pode mutar
- Em quais condições ele será bloqueado

## 2. Bug Original
- Arquivo
- Função
- Linha aproximada
- Comportamento anterior
- Risco

## 3. Correção Aplicada
- Validação de user_id
- Validação de profile_id
- Filtro nas queries
- Proteção contra fallback global
- Amostra mínima
- Audit log completo

## 4. Arquivos Alterados
| Arquivo | Alteração | Motivo |
|---|---|---|

## 5. Testes Criados/Alterados
| Teste | Resultado |
|---|---|

## 6. Evidências
- Trecho de código corrigido
- Query filtrada por user_id/profile_id
- Exemplo de evento bloqueado
- Exemplo de evento mutado com before/after/diff

## 7. Riscos Remanescentes
- Bloquear live promotion automática do PI Auto-Pilot
- Corrigir toggles órfãos LightGBM/CatBoost
- Validar Dynamic Combinations fora da amostra
- Criar Suggestion Registry
- Criar Champion Registry
- Corrigir source_profiles no Profile Intelligence

## 8. Veredito
- Auto-Pilot continua ativo?
- Mutação global foi bloqueada?
- Escopo obrigatório foi garantido?
- Testes passaram?
```

---

## 11. Restrições explícitas

Não fazer neste prompt:

- Não implementar LightGBM.
- Não implementar CatBoost.
- Não alterar Champion Registry.
- Não alterar PI Auto-Pilot.
- Não bloquear candidates shadow.
- Não mexer em Dynamic Combinations.
- Não criar migrations estruturais grandes.
- Não alterar regra estratégica de RSI/ADX/MACD.
- Não re-treinar ML.
- Não alterar XGBoost.
- Não alterar thresholds operacionais de trade.

Este prompt é exclusivamente para corrigir o risco de escopo do Auto-Pilot legado mantendo o Auto-Pilot ativo.

---

## 12. Veredito esperado

Ao final, o sistema deve ficar assim:

```text
Auto-Pilot legado: ativo
Mutação com user_id + profile_id válidos: permitida
Mutação sem escopo válido: bloqueada
Fallback global para mutação: proibido
Audit log de mutação: obrigatório
Auto-Pilot desligado: não
Dry-run global forçado: não
```

Objetivo final:

```text
Manter o Auto-Pilot operando, mas impedir que ele altere profiles com base em dados globais ou misturados.
```
