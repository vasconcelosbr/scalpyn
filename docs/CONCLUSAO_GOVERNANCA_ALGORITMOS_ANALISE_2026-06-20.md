# Conclusão da Governança dos Algoritmos de Análise — Scalpyn

## Sumário Executivo

- Profile Intelligence continua ativo para análise, descoberta, validação
  temporal, sugestões rastreáveis e shadow forward.
- Auto-Pilot legado continua ativo com escopo obrigatório por usuário e profile.
- PI Auto-Pilot continua ativo sem promoção live automática.
- XGBoost foi preservado como implementação operacional existente.
- LightGBM e CatBoost continuam não implementados e não operacionais.
- Autonomia plena continua bloqueada. A policy permite no máximo nível
  controlado `3` `[migration: 098_forward_autonomy_policy]`.

Não devem ir para produção sem migrations aplicadas, validação no banco real e
monitoramento forward: registries novos, limited live e futuros challengers.

## Fases Preservadas

- Fase 1: escopo `user_id + profile_id`, auditoria de mutação e
  `AUTOPILOT_SCOPE_BLOCKED` preservados.
- Fase 2: `PENDING_HUMAN_APPROVAL`, aprovação/ativação separadas e rollback
  obrigatório preservados.
- Fase 3: LightGBM/CatBoost continuam normalizados para `false`; XGBoost não foi
  substituído.

## Correções Aplicadas

- Validação fora da amostra:
  - Dynamic Combinations usa discovery/validation temporal, bloqueia feature
    ausente, suporte baixo, lift/win-rate ruins e dependência concentrada.
  - Association Rules separa WIN/LOSS, classifica actionability e valida holdout.
  - Optuna usa validation real e persiste PnL esperado, precision, FPR, lift de
    win rate, redução de drawdown e trade count.
- Suggestion Registry:
  - origem, run, profile, diff, rollback, versions, risco, impacto, validation e
    lifecycle foram adicionados;
  - criação de profile é bloqueada se o contrato de rastreabilidade estiver
    incompleto;
  - profile criado permanece `SHADOW_ONLY`.
- `source_profiles`:
  - analyzers agora selecionam e persistem `profile_id` e `profile_name`;
  - API e UI expõem nomes e ids de origem.
- Champion/Challenger:
  - registry suporta `xgboost`, `lightgbm`, `catboost`;
  - índice parcial impede mais de um champion por escopo;
  - challenger não pode controlar produção;
  - decisões registram `ml_model_id` e `ml_model_type`.
- Forward Validation:
  - lifecycle formal: discovery → temporal validation → shadow forward → human
    approval → limited live → full live;
  - live exige validation, shadow forward, aprovação humana e rollback.
- Autonomia:
  - policy default em nível `2` `[API: safe_default]`;
  - migrations limitam configuração ao nível `3`
    `[migration: 098_forward_autonomy_policy]`;
  - níveis `4/5` permanecem desabilitados.

## Arquivos Alterados

| Arquivo | Alteração | Motivo |
|---|---|---|
| `backend/app/models/profile_intelligence.py` | Registries e campos de governança | Persistência e constraints |
| `backend/app/services/profile_validation_service.py` | Gates OOS | Bloquear evidência fraca |
| `backend/app/services/algorithm_governance_service.py` | Policy central | Sugestão, forward, autonomia e challenger |
| `backend/app/services/indicator_lift_service.py` | Source profiles | Rastreabilidade |
| `backend/app/services/counterfactual_combination_service.py` | Source profiles e validation | Dynamic governado |
| `backend/app/services/association_rules_service.py` | Holdout e actionability | WIN/LOSS seguros |
| `backend/app/services/optuna_profile_search_service.py` | Métricas validation | Avaliação OOS real |
| `backend/app/services/profile_suggestion_service.py` | Registry completo | Sugestão aplicável rastreável |
| `backend/app/services/profile_create_service.py` | Gate do registry e shadow forward | Impedir aplicação sem validation |
| `backend/app/api/profile_intelligence.py` | Serialização e endpoints | UI/auditoria dos registries |
| `backend/app/tasks/pipeline_scan.py` | Model identity | Decisão auditável |
| `frontend/app/profile-intelligence/page.tsx` | Status e evidências | Diferenciar exploratório/validado/bloqueado |
| `docs/ML_CHALLENGER_READINESS_PLAN.md` | Plano técnico | Preparação sem ativação |

## Migrations

Heads antes: `095_pi_human_live_approval`
`[command: alembic heads]`.

Heads depois: `098_forward_autonomy_policy`
`[command: alembic heads]`.

| Migration | Motivo | Reversível? | Head usada |
|---|---|---|---|
| `096_pi_suggestion_registry_source_profiles.py` | Suggestion Registry e source profiles | Sim | `095_pi_human_live_approval` |
| `097_ml_champion_challenger_registry.py` | Model/champion registry | Sim | `096_pi_suggestion_registry` |
| `098_forward_validation_autonomy_policy.py` | Forward lifecycle e autonomia controlada | Sim | `097_ml_champion_registry` |

As migrations não foram executadas. `alembic current` não pôde conectar ao banco
local: `ConnectionRefusedError [WinError 1225]` `[command: alembic current]`.

## Testes

| Teste | Resultado |
|---|---|
| Suíte crítica de governança | `88 passed` `[command: pytest]` |
| Frontend production build | `Compiled successfully` `[command: npm run build]` |
| TypeScript | sucesso, exit code `0` `[command: npx tsc --noEmit]` |
| Python compileall | sucesso, exit code `0` `[command: compileall]` |
| Ruff — artefatos novos | `All checks passed!` `[command: ruff check]` |
| Git diff check | sucesso, exit code `0` `[command: git diff --check]` |

O Ruff aplicado aos arquivos legados completos ainda encontra dívida preexistente
de estilo, principalmente `E701`; os novos artefatos de governança estão limpos.

## Auditoria Final

1. Dynamic sem validation gera sugestão aplicável? Não.
2. Association sem validation gera sugestão aplicável? Não.
3. Optuna sem validation gera sugestão aplicável? Não.
4. Feature ausente passa regra? Não.
5. LOSS vira positive signal? Não.
6. Suggestion exige `source_type`? Sim.
7. Suggestion exige `source_run_id`? Sim.
8. Suggestion exige `profile_id`? Sim.
9. Suggestion exige `validation_status=validated`? Sim.
10. `source_profiles` está preenchido por novas análises? Sim.
11. Dois champions podem existir no mesmo escopo? Não.
12. Challenger pode alterar produção? Não.
13. LightGBM/CatBoost continuam desativados? Sim.
14. XGBoost foi preservado? Sim.
15. Auto-Pilot legado foi preservado? Sim.
16. PI Auto-Pilot foi preservado? Sim.
17. Migrations 094/095 foram preservadas? Sim.
18. Testes críticos passaram? Sim: `88 passed` `[command: pytest]`.
19. Frontend build passou? Sim.
20. Ruff/compileall passaram? Novos artefatos Ruff: sim; compileall: sim.

## Riscos Remanescentes

- Migrations ainda não aplicadas nem verificadas no banco real.
- LightGBM/CatBoost ainda não têm dependência, trainer, predictor ou benchmark.
- Autonomia plena permanece bloqueada.
- Limited/full live exigem implementação operacional de monitoramento forward.
- A atualização incremental do graphify excedeu o timeout da ferramenta; o
  `graph.json` final não foi regenerado nesta execução.

## Veredito Final

- Profile Intelligence pronto para análise confiável: sim, após aplicar as
  migrations no ambiente autorizado.
- Pronto para sugestão validada: sim.
- Auto-Pilot pode continuar ativo: sim.
- Auto-Pilot pode aplicar live sozinho: não.
- Dynamic/Association/Optuna sem validation: bloqueados.
- Sugestões possuem rastreabilidade: sim.
- `source_profiles` corrigido: sim para novas execuções.
- Champion/Challenger seguro: sim em schema, policy e testes.
- LightGBM/CatBoost funcionam: não.
- LightGBM/CatBoost podem decidir produção: não.
- Sistema preparado para a implementação real futura: sim, sem ativação.

## Ledger de Evidências

| Número reportado | Origem | Valor literal |
|---|---|---|
| Testes críticos | `[command: pytest]` | `88 passed` |
| Head final | `[command: alembic heads]` | `098_forward_autonomy_policy (head)` |
| Build | `[command: npm run build]` | `Compiled successfully` |
| Máximo de autonomia | `[migration: 098]` | `CHECK (maximum_level BETWEEN 0 AND 3)` |
