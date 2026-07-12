# T4H — Reconciliação de conflito, lineage, testes e migrations

## 1. Resumo executivo

Decisão: `BLOCKED_MIGRATION_131`.

As sete falhas da T4G foram reproduzidas pela raiz: três falhas de path
desapareceram e quatro falhas funcionais permaneceram. As quatro foram
reconciliadas e a suíte relacionada terminou verde. A criação do baseline
recuperado foi interrompida porque não existe PostgreSQL descartável disponível
para exercitar as migrations 131–133 e o DML da 131. Nenhum ambiente remoto foi
usado como substituto.

## 2. Decisão de entrada

`BLOCKED_UNKNOWN_HIGH_RISK_CHANGE` [prompt/documento].

## 3. Reprodução das sete falhas

Execução pela raiz, com Python `3.14.3` e pytest `9.0.3` [test]. O resultado foi
`166 passed, 4 failed`: as três falhas de caminho observadas na T4G não se
reproduziram; permaneceram identidade de conflito, dois mocks L1 e um test
double de consulta de lineage.

| teste/grupo | T4G | raiz | classificação |
|---|---:|---:|---|
| paths em `shadow_watchlist_lineage` | 3 falhas | 0 falhas | `PATH_EXECUTION_DEFECT` |
| alvo do ON CONFLICT | 1 falha | 1 falha | `CONTRACT_MISMATCH` |
| mocks de sessão L1 | 2 falhas | 2 falhas | `STALE_TEST_DOUBLE` |
| atribuição de profile/lineage | 1 falha | 1 falha | `STALE_TEST_DOUBLE` |

## 4. Falhas de path

Eliminadas ao executar da raiz do repositório. Nenhum código ou teste foi
alterado para mascarar caminhos relativos.

## 5. Quatro falhas funcionais

1. `_INSERT_SHADOW_SQL` ainda usava `ON CONFLICT DO NOTHING` genérico.
2. `test_disabled_flag_returns_zero` mockava símbolo inexistente no serviço.
3. `test_rate_limit_generates_skip` repetia o mesmo mock obsoleto.
4. `test_create_from_decision_copies_profile_attribution` não simulava a consulta
   determinística de `profile_versions` anterior ao INSERT.

## 6. Identidade por source

| source | identidade operacional ativa | campos obrigatórios | duplicidade |
|---|---|---|---|
| `L1_SPECTRUM` | user, symbol, source enquanto não concluído e sem profile | user_id, symbol, source | nova após conclusão |
| `L3` | mesma chave para baseline sem profile; decision_id preserva identidade canônica | user_id, symbol, source | nova após conclusão |
| `L3_REJECTED` | user, symbol, source enquanto ativo | user_id, symbol, source | nova após conclusão |
| `L3_SIMULATED` | user, symbol, source enquanto ativo | user_id, symbol, source | nova após conclusão |
| `L3_LAB` | profile, symbol, source enquanto RUNNING/PENDING | profile_id, symbol, source | nova após fechamento |

Essa matriz reflete os índices versionados; não atribui uma identidade universal
a sources com e sem profile.

## 7. Índices e constraints

Evidência estática principal:

- `ux_shadow_running_user_source` em `(user_id, symbol, source)` com predicado
  `profile_id IS NULL AND completed_at IS NULL`;
- `uq_shadow_lab_active_profile_symbol` em `(profile_id, symbol, source)` com
  predicado `profile_id IS NOT NULL AND status IN ('RUNNING','PENDING')`;
- `ux_shadow_trades_decision_id_canonical` em `decision_id` para linhas canônicas.

A consulta ao catálogo de um banco descartável não foi possível.

## 8. ON CONFLICT

O INSERT canônico foi alterado para o alvo explícito:

```sql
ON CONFLICT (user_id, symbol, source)
    WHERE profile_id IS NULL AND completed_at IS NULL
DO NOTHING
```

Com isso, violações de índices não relacionados deixam de ser silenciosamente
suprimidas por esse caminho.

## 9. Testes de idempotência

O teste existente agora verifica o alvo e o predicado literais do índice
parcial. Os testes relacionados de duplicidade, lineage, L1, L3 e captura nativa
passaram. Testes reais de INSERT concorrente ficaram condicionados ao banco
descartável indisponível.

## 10. Contrato de lineage

Lineage completa exige as versões de profile e score engine quando há profile;
ausência mantém `eligible_for_training=false`. Sources sem profile usam a
identidade operacional parcial e continuam segregadas por `source`. O dataset
oficial também exige o contrato point-in-time e campos de versão/hash.

## 11. Consulta de lineage

A consulta busca a versão SHADOW/CHAMPION mais recente quando existe profile,
mas os IDs versionados não vieram no objeto de lineage. Ela é determinística,
ocorre dentro da mesma sessão/transação e evita inventar IDs. O teste foi
atualizado para representar a consulta e inspecionar o último `db.execute`, que
é o INSERT.

## 12. Mocks L1

Os dois mocks foram movidos para `app.database.CeleryAsyncSessionLocal`, o
namespace real de onde a função faz import local. Nenhum símbolo artificial foi
adicionado à produção e nenhum assert foi removido.

## 13. `_ind_captured_at`

O valor é gravado dentro do payload de indicadores L1 como
`_features_captured_at`, derivado de `promotion_at`. Não alimenta a coluna
persistida `features_captured_at`, que é produzida por
`capture_native_snapshot()` no INSERT. Ele foi preservado porque renomeá-lo sem
mapear consumidores históricos ampliaria o escopo; o dataset oficial permanece
filtrado pelo contrato nativo persistido.

## 14. Banco descartável

Indisponível. O Docker CLI respondeu com versão, mas o daemon não respondeu aos
probes; não foi localizado `psql` nem serviço PostgreSQL local. A conexão da
aplicação não foi usada, pois não foi comprovada como descartável.

## 15. Migration 131

Grafo estático válido: `130_pool_asset_exclusions -> 131_ml_governance_v2`.
O DML sobre `ml_models`, `config_profiles` e `ml_label_contracts` não foi
exercitado. Decisão específica: `BLOCKED_MIGRATION_131`.

## 16. Migration 132

Grafo estático válido:
`131_ml_governance_v2 -> 132_calibration_orchestration_v2`. Upgrade/downgrade
online não executados.

## 17. Migration 133

Grafo estático válido:
`132_calibration_orchestration_v2 -> 133_native_feature_capture (head)`. A
migration permanece sem backfill/default temporal e contém trigger de
imutabilidade, mas seus testes de DDL/trigger reais não foram executados.

## 18. Testes finais

- suíte ampliada: `170 passed` em `4.19s` [test];
- suíte mínima: `12 passed` em `2.75s` [test];
- falhas: `0` [test];
- skips: `0` [test].

## 19. Dataset oficial

O SQL revisado exige `capture_contract_version='point-in-time-v1'`, timestamp de
captura, hash e versões de extractor/schema. Os testes relacionados passaram.
Não houve acesso ou alteração de dataset de produção.

## 20. Commits

- `558c30b test(ml): update remaining L1 lineage test doubles`;
- `eada593 fix(ml): target shadow trade idempotency conflicts explicitly`.

O commit `RECOVERED_INTEGRATED_BASELINE` não foi criado porque o gate de banco
descartável não fechou.

## 21. Estado final do Git

Branch `codex/ml-profile-intelligence-v2`. O worktree preexistente permanece
sujo e preservado. Os commits T4H foram cirúrgicos; o commit de produção contém
somente o hunk de conflito explícito, sem absorver os demais hunks já existentes
em `shadow_trade_service.py`. Nenhum push foi executado.

## 22. Riscos

- DML da migration 131 não ensaiado;
- DDL e downgrade 131–133 não exercitados;
- trigger da 133 não provado em PostgreSQL real;
- índices foram reconciliados por migrations versionadas, não por catálogo vivo;
- baseline integrado ainda não commitado.

## 23. Decisão final

```text
BLOCKED_MIGRATION_131
```

## 24. Próxima fase

Disponibilizar/iniciar PostgreSQL descartável, executar a sequência completa
130→131→132→133, downgrade/upgrade da 133, cenários DML da 131 e testes de
trigger. Somente depois criar o commit do baseline recuperado e retomar T4F.

## 25. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | COMANDO/QUERY | VALOR LITERAL |
|---|---|---|---|
| falhas T4G reproduzidas=4 | [test] | pytest pela raiz | `4 failed` |
| testes iniciais aprovados=166 | [test] | pytest pela raiz | `166 passed` |
| falhas de path finais=0 | [test] | pytest pela raiz | nenhuma |
| testes ampliados finais=170 | [test] | pytest final | `170 passed` |
| testes mínimos finais=12 | [test] | pytest mínimo | `12 passed` |
| falhas finais=0 | [test] | pytest final | `0 failed` |
| skips finais=0 | [test] | pytest final | nenhum skip reportado |
| heads Alembic=1 | [test] | `alembic heads` | `133_native_feature_capture (head)` |
| commits T4H criados=2 | [git] | `git log` | `558c30b`, `eada593` |
| bancos descartáveis validados=0 | [test] | probes Docker/PostgreSQL | nenhum disponível |
| pushes=0 | [git] | execução T4H | nenhum |

