# T4G — Normalização do worktree e baseline recuperado

## 1. Resumo executivo

Decisão: `BLOCKED_UNKNOWN_HIGH_RISK_CHANGE`.

A estratégia `RECOVERED_INTEGRATED_BASELINE` foi adotada, a fotografia externa
foi criada e as falhas L1 originalmente apontadas foram corrigidas em commit
isolado. O commit do baseline integrado não foi criado porque a suíte ampliada
revelou falhas semânticas ainda não explicadas no estado integrado. Commitar o
conjunto nessas condições violaria os gates da T4G.

## 2. Causa do bloqueio

O baseline histórico pré-T4C não é reproduzível. Na revisão do estado atual, a
suíte relacionada encontrou falha na identidade de conflito do INSERT de
`shadow_trades`, mocks adicionais defasados e uma consulta de lineage não
representada por teste existente. A origem e o impacto do comportamento de
`ON CONFLICT DO NOTHING` sem alvo ainda exigem reconciliação com os índices e a
identidade lógica por source.

## 3. Estratégia de baseline recuperado

O estado atual é reconhecido como candidato a `RECOVERED_INTEGRATED_BASELINE`:
uma fotografia sem atribuição falsa de autoria. Ele ainda não recebe o estado
`RECOVERED_INTEGRATED_BASELINE_CREATED`, pois os critérios de teste não fecharam.

## 4. HEAD e branch

- Branch inicial/final: `codex/ml-profile-intelligence-v2` [git].
- HEAD inicial: `67910a63b9f9f8275da3eced8f2fc29f8b59bfa4` [git].
- HEAD após correção L1: `a12c8512b08b9e850108ac02c3d3cf95ffd39669` [git].

## 5. Inventário do worktree

Foram preservados todos os arquivos rastreados e não rastreados. O conjunto
explicitamente fotografado contém migrations 131–133, contrato de features,
modelo e serviços da captura, política de dataset, testes relevantes e os
relatórios T4C/T4E/T4F. Mudanças alheias permaneceram fora do commit L1.

## 6. Hashes

- Arquivo ZIP: `t4g-recovered-snapshot-20260712T163857Z.zip` [git/filesystem].
- SHA-256: `A972371C7B2C95FC9D0BE6800A573C2AA46D4DA4B0E4B08BC184B4E59BB07218` [calc].
- Tamanho literal: `87937` bytes [filesystem].
- Manifesto individual: `manifest-sha256.csv` no diretório de evidências.

## 7. Snapshot externo

- Snapshot: `C:\Users\ricar\AppData\Local\Temp\t4g-recovered-snapshot-20260712T163857Z`.
- Arquivo: `C:\Users\ricar\AppData\Local\Temp\t4g-recovered-snapshot-20260712T163857Z.zip`.
- Evidências: `C:\Users\ricar\AppData\Local\Temp\t4g-evidence-20260712T163857Z`.
- Horário UTC: `2026-07-12T16:38:57Z` [filesystem].
- Arquivos fotografados: `14` [filesystem].

## 8. Matriz semântica

| arquivo/bloco | responsabilidade | origem | risco | decisão |
|---|---|---|---|---|
| `131_ml_governance_v2.py` | schema e defaults de governança | histórica parcial | DDL/DML de governança | `KEEP_AS_RECOVERED_BASELINE`, condicionado a banco descartável |
| `132_calibration_orchestration_v2.py` | orquestração/calibração | histórica parcial | dependência da cadeia | `KEEP_AS_RECOVERED_BASELINE` |
| `133_native_feature_capture.py` | colunas e imutabilidade T4C | T4C | DDL em `shadow_trades` | `KEEP_AS_RECOVERED_BASELINE`, condicionado a validação online |
| `feature_contract_v2.py` | captura, normalização e hash | integrada/T4C | contrato temporal | `KEEP_AS_RECOVERED_BASELINE` |
| `shadow_trade_service.py` | materialização e persistência | integrada | conflito/lineage ainda não reconciliados | `BLOCK_UNKNOWN_HIGH_RISK` |
| `ml_challenger_service.py` | dataset oficial | integrada/T4C | fronteira do dataset | `KEEP_AS_RECOVERED_BASELINE` |
| `test_l1_features.py` | contrato L1 | teste preexistente | mocks defasados | `FIX_CONFIRMED_DEFECT` |

## 9. Governance 131

A revisão confirmou a cadeia `130_pool_asset_exclusions -> 131_ml_governance_v2`.
A migration não faz backfill de `features_captured_at`. Ela contém DML explícito
de governança sobre `ml_models`, `config_profiles` e inserção idempotente em
`ml_label_contracts`; esse DML não foi executado e ainda precisa ser exercitado
em banco descartável antes de qualquer deploy.

## 10. Captura T4C

O código contém `capture_native_snapshot()`, `utcnow()` na materialização, hash
canônico, versões de extractor/schema/contrato e persistência dos campos. A
migration 133 adiciona as colunas e um trigger de imutabilidade. Os testes T4C
mínimos passaram, mas o conjunto integrado permanece bloqueado pelas falhas da
suíte ampliada.

## 11. Fallbacks

As seis buscas obrigatórias não encontraram atribuição de
`features_captured_at` a `decision.created_at`, `promotion_at`, COALESCE ou
timestamps de ranking/shadow. Uma ocorrência define `_ind_captured_at` com
`promotion_at`; trata-se de metadado de indicadores L1, não do campo persistido
`features_captured_at`, e foi preservada para revisão semântica posterior.

## 12. Classificação das falhas L1

As três falhas originais foram classificadas como `PREEXISTING_TEST_DEFECT`:
os mocks apontavam para símbolos importados localmente e inexistentes no
namespace do serviço. Após corrigir os namespaces, os fakes também precisaram
aceitar o argumento atual `lineage`.

## 13. Correções

Somente `backend/tests/test_l1_features.py` foi corrigido. Os mocks agora atuam
em `backend.app.database`, `backend.app.services.indicators_provider` e
`backend.app.schemas.spot_engine_config`; nenhum símbolo falso foi adicionado à
produção e nenhum assert foi removido.

## 14. Testes

- Suíte mínima final: `12 passed` [test].
- Suíte relacionada ampliada: `163 passed, 7 failed` [test].
- Das falhas ampliadas, `3` decorrem de testes que resolvem caminhos relativos
  à raiz quando executados dentro de `backend` [test].
- As demais `4` envolvem identidade de conflito, dois mocks L1 adicionais e
  consulta de atribuição/lineage ainda não simulada [test].

O gate global de zero regressões não fechou.

## 15. Alembic

- `alembic heads`: `133_native_feature_capture (head)` [test].
- A cadeia estática percorreu 131, 132 e 133 sem warning [test].
- `alembic current` falhou porque a conexão foi encerrada durante a operação;
  portanto a revisão atual do banco é `NÃO DISPONÍVEL` [test].
- Nenhuma migration foi aplicada ou revertida.

## 16. Commits

- `a12c851 test(ml): align L1 async session mocks with current pipeline` [git].
- Commit do baseline recuperado: não criado, por gate semântico/testes aberto.

## 17. Estado final do Git

O worktree preexistente permanece sujo e preservado. O único commit da T4G
contém exclusivamente a correção do teste L1. Snapshot e evidências temporárias
não foram adicionados ao repositório.

## 18. Riscos

- identidade lógica de conflito por `source` ainda não reconciliada;
- testes adicionais usam mocks defasados;
- consulta de lineage altera o contrato observado por teste legado;
- validação Alembic online indisponível;
- migration 131 contém DML de governança ainda não ensaiado em banco descartável;
- amplo conjunto de mudanças alheias impede commit agregado sem revisão adicional.

## 19. Decisão final

```text
BLOCKED_UNKNOWN_HIGH_RISK_CHANGE
```

## 20. Próxima fase

Resolver e classificar as quatro falhas funcionais da suíte ampliada, reexecutar
os testes relacionados a partir da raiz, validar migrations 131–133 em banco
descartável e somente então criar o commit explícito do baseline recuperado.
T4F, push, deploy e migration de produção continuam não autorizados.

## 21. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | COMANDO | VALOR LITERAL |
|---|---|---|---|
| arquivos no snapshot=14 | [calc] | `Get-ChildItem ... -Recurse -File` | `14` |
| tamanho do ZIP=87937 bytes | [calc] | `(Get-Item $archive).Length` | `87937` |
| testes mínimos aprovados=12 | [test] | pytest mínimo T4G | `12 passed` |
| testes relacionados aprovados=163 | [test] | pytest ampliado | `163 passed` |
| testes relacionados falhos=7 | [test] | pytest ampliado | `7 failed` |
| falhas de caminho relativo=3 | [test] | tracebacks `FileNotFoundError` | `3` |
| falhas funcionais/test-double restantes=4 | [calc] | `7 - 3` | `4` |
| heads Alembic=1 | [test] | `alembic heads` | `133_native_feature_capture (head)` |
| commits criados=1 | [git] | `git log` | `a12c851` |
| migrations de produção aplicadas=0 | [git] | execução T4G | nenhuma |
| deploys executados=0 | [git] | execução T4G | nenhum |

