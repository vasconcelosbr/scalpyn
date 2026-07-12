# T4E — Reconstrução de baseline e separação do delta T4C

## 1. Resumo executivo

Decisão: `BLOCKED_BASELINE_NOT_FOUND`.

Não foi localizado um estado Git reproduzível que contenha governance 131 e o contrato original, mas não contenha T4C. Reconstruir esse estado removendo hunks do worktree atual exigiria inferir autoria de blocos sobrepostos, o que o prompt proíbe.

## 2. HEAD e branch

- Branch: `codex/ml-profile-intelligence-v2` [git].
- HEAD inicial T4E: `023d7866d9277c7c1185276c002dbf8bc5d44640` [git].

## 3. Fontes investigadas

- `git log --all`, buscas `-S` e `-G`;
- reflog completo disponível;
- branches locais/remotas;
- `stash@{0}`;
- worktrees registrados;
- commits locais/órfãos visíveis;
- cópias em `.codex_tmp`;
- arquivos governance atuais;
- relatórios T4B/T4C/T4D.

## 4. Baseline procurado

Critérios simultâneos:

```text
governance 131 presente
feature_contract_v2 original presente
T4C ausente
origem Git reproduzível
```

Nenhuma referência encontrada atende aos quatro critérios.

## 5. Evidências Git

- O reflog da branch salta de `feb8ca1` para os commits T0/T4; governance 131 nunca foi commitada [git].
- `git log -S capture_native_snapshot --all` não encontrou commit de aplicação T4C [git].
- Migration 131 e `feature_contract_v2.py` permanecem não rastreados [git].
- O único stash é `autostash` de 09/jul, anterior à criação de governance 131 [git].
- Worktrees encontrados apontam para baselines de junho/julho anteriores e não contêm governance 131 [git].
- Nenhuma cópia `feature_contract_v2.py` ou `131_ml_governance_v2.py` foi localizada em `.codex_tmp` [git/filesystem].

## 6. Matriz de autoria

| arquivo/bloco | origem demonstrável | baseline | T4C | decisão |
|---|---|---|---|---|
| migration 131 inteira | PREEXISTING_USER_CHANGE/GOVERNANCE_131 | sim | não | preservar, mas sem commit-base |
| migration 133 inteira | T4C | não | sim | isolável |
| teste native capture | T4C | não | sim | isolável |
| filtro oficial no challenger | T4C | não | sim | isolável |
| campos governance no modelo | PREEXISTING_USER_CHANGE | sim | não | sobreposto no arquivo |
| campos T4C no modelo | T4C | não | sim | semanticamente identificável |
| shadow service governance | PREEXISTING_USER_CHANGE | sim | não | sem snapshot pré-T4C |
| shadow service captura nativa | T4C | não | sim | sobreposto ao bloco governance |
| feature_contract original | UNKNOWN/PREEXISTING | sim | não | sem cópia pré-T4C |
| extensões native capture | T4C | não | sim | identificáveis, mas arquivo-base desconhecido |

Blocos `UNKNOWN` impedem commit automático.

## 7. Testes L1

Resultado observado no worktree sobreposto: `3` falhas por mock de `CeleryAsyncSessionLocal` ausente [test].

Classificação final: não determinada. Sem baseline governance reproduzível, não foi possível executar o teste “antes da T4C” exigido e distinguir formalmente `PREEXISTING_TEST_DEFECT` de regressão de integração.

Nenhum teste foi alterado, ignorado ou enfraquecido.

## 8. Alembic

- Grafo local: uma head, `133_native_feature_capture` [test].
- A integridade do grafo não resolve a ausência de baseline versionado da migration 131.
- Nenhuma migration foi aplicada.

## 9. Worktree original

Preservado. Não foram usados reset, restore, checkout destrutivo, stash, clean, rebase ou sobrescrita integral. Nenhum worktree isolado foi criado porque faltou um `<baseline_commit>` que atendesse ao gate.

## 10. Commits

Nenhum commit de baseline, teste ou T4C foi criado. Somente este relatório pode ser versionado separadamente.

## 11. Riscos

- Criar baseline “subtraindo T4C” do arquivo atual trataria memória operacional como prova de autoria.
- Commitar os arquivos atuais misturaria mudanças preexistentes e T4C.
- Aplicar migration 133 dependeria de migration 131 ainda não versionada.
- As três falhas L1 permanecem sem classificação formal.

## 12. Condição para retomar

É necessário fornecer uma das seguintes evidências:

1. cópia/patch/hash do worktree imediatamente anterior à T4C;
2. commit contendo governance 131 e `feature_contract_v2.py` originais;
3. autorização explícita para tratar o estado governance identificável dos relatórios T4B como baseline reconstruído, aceitando a separação sem commit prévio.

Sem isso, T4D não pode ser retomado.

## 13. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | COMANDO | VALOR LITERAL |
|---|---|---|---|
| stashes encontrados=1 | [git] | `git stash list` | `stash@{0}: autostash` |
| worktrees registrados=5 | [git] | `git worktree list --porcelain` | 5 entradas |
| baseline aceitável=0 | [git/calc] | busca Git/filesystem | 0 |
| falhas L1=3 | [test] | pytest selecionado | 3 failed |
| heads Alembic=1 | [test] | `alembic heads` | `133_native_feature_capture` |

