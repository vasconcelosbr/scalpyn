# T4D — Deploy e validação da captura nativa point-in-time

## 1. Resumo executivo

Decisão: `BLOCKED_WORKTREE_CONFLICT`.

O deploy não foi iniciado. O inventário confirmou mudanças preexistentes e T4C sobrepostas sem baseline pré-T4C capaz de separar autoria linha a linha. O prompt determina parada nessa condição. Três testes L1 também permanecem falhando, logo o gate de testes não está fechado.

## 2. Commit inicial

- Branch: `codex/ml-profile-intelligence-v2` [git].
- HEAD: `c0d3c277e65c1adbe71794b2d4eabf690f9d90a3` [git].

## 3. Inventário do worktree relevante

```text
M  backend/app/models/shadow_trade.py
M  backend/app/services/ml_challenger_service.py
M  backend/app/services/shadow_trade_service.py
?? backend/alembic/versions/131_ml_governance_v2.py
?? backend/alembic/versions/133_native_feature_capture.py
?? backend/app/ml/feature_contract_v2.py
?? backend/tests/test_native_feature_capture.py
?? docs/RELATORIO_T4C_CAPTURA_NATIVA_POINT_IN_TIME.md
?? docs/RELATORIO_T4C_RECUPERACAO_FORENSE_DADOS_2026-07-01_A_2026-07-12.md
```

Diff rastreado relevante:

```text
shadow_trade.py: +24/-0
ml_challenger_service.py: +4/-0
shadow_trade_service.py: +191/-9
```

## 4. Classificação

- `shadow_trade_service.py`: `OVERLAPPING_CHANGE` — governance 131 preexistente mais integração T4C.
- `shadow_trade.py`: `OVERLAPPING_CHANGE`.
- `feature_contract_v2.py`: `UNTRACKED_USER_FILE` com extensão T4C sobreposta.
- migration 131: `UNTRACKED_USER_FILE`, pré-requisito da migration 133.
- migration 133/teste nativo/relatórios T4C: `T4C_CHANGE`.
- caches Graphify e demais arquivos fora da lista: `GENERATED_ARTIFACT` ou mudança alheia; preservados.

## 5. Estratégia de preservação

Nenhum reset, restore, checkout, stash, clean, rebase ou descarte foi executado. Nenhuma mudança preexistente foi commitada como T4C.

## 6. Revisão técnica T4C

O worktree preparado contém:

- timestamp produzido por `utcnow()` durante materialização;
- hash SHA-256 de JSON canônico com `allow_nan=False`;
- versões `feature-engine-v2`, `entry_features_v2`, `point-in-time-v1`;
- persistência no mesmo INSERT;
- remoção de fallback `decision.created_at` e `promotion_at`;
- filtro oficial exigindo contrato/versões/hash nativos.

Isso não foi commitado nem implantado por causa da sobreposição.

## 7. Testes

- Suite selecionada: `24` aprovados e `3` falhos [test].
- As três falhas ocorrem em `test_l1_features.py`, antes da captura, ao mockar `CeleryAsyncSessionLocal`, símbolo ausente no módulo [test].
- A origem preexistente ainda não foi isolada por um baseline limpo; portanto, o prompt proíbe seguir ao deploy.

## 8. Migration 133

- `alembic heads`: uma linha, `133_native_feature_capture (head)` [test].
- Cadeia: `132_calibration_orchestration_v2 -> 133_native_feature_capture` [test].
- Sem DML/backfill histórico; colunas nullable e trigger de imutabilidade [code].
- `alembic current` não concluiu por conexão encerrada (`ConnectionDoesNotExistError`) [test].
- Migration não aplicada em produção.

## 9. Deploy e canário

- Push: não executado.
- Deploy: não executado.
- Migration de produção: não executada.
- `native_capture_start_at`: `NÃO DISPONÍVEL`.
- Capturas nativas: `NÃO DISPONÍVEL`.
- Hash 50/50: não executável antes do deploy.
- Lineage/temporalidade do canário: não executável.

## 10. Dataset e modelo

O filtro local está preparado, mas não foi comprovado em produção. Histórico permanece research-only. Estado do modelo continua `MODEL_APPROVAL_NOT_YET_POSSIBLE`.

## 11. Rollback

Nenhum rollback é necessário porque não houve commit de código T4C, push, migration nem deploy. As mudanças locais foram preservadas integralmente.

## 12. Próxima condição

Para reabrir T4D é necessário fornecer uma separação confiável das mudanças preexistentes, por exemplo um commit/base que contenha governance 131 antes da T4C. Depois disso: reaplicar somente o delta T4C, corrigir ou provar as três falhas L1, validar migration e então fazer deploy controlado.

## 13. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | COMANDO | VALOR LITERAL |
|---|---|---|---|
| arquivos relevantes listados=8 | [git] | `git status --short -- <escopo>` | 8 entradas |
| diff shadow service=+191/-9 | [git] | `git diff --numstat` | `191 9` |
| testes aprovados=24 | [test] | pytest selecionado | `24 passed` |
| testes falhos=3 | [test] | pytest selecionado | `3 failed` |
| heads Alembic=1 | [test] | `alembic heads` | `133_native_feature_capture (head)` |
| deploys executados=0 | [deploy] | condição de parada | 0 |

