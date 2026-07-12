# T4I — PostgreSQL descartável, migrations e baseline

## 1. Resumo executivo

Decisão: `BLOCKED_DISPOSABLE_POSTGRES_UNAVAILABLE`.

O provisionamento local permitido foi tentado. O Docker CLI está instalado e o
Docker Desktop foi iniciado, mas o daemon Linux não respondeu aos probes. Não
há PostgreSQL/`psql` local comprovado nem sandbox efêmero configurado. Conforme
a condição de parada, a conexão da aplicação não foi usada como atalho.

## 2. Decisão de entrada

`BLOCKED_MIGRATION_131` [prompt/documento].

## 3. Método de provisionamento

Opção A, Docker local. `docker version`, `docker info` e probes formatados do
server foram tentados. O processo Docker Desktop foi iniciado oculto, mas o
endpoint `npipe:////./pipe/dockerDesktopLinuxEngine` permaneceu sem resposta.

## 4. Isolamento

Nenhum banco foi criado. Portanto, nenhuma conexão de banco foi aceita como
descartável e nenhuma credencial da aplicação foi utilizada.

## 5. Versão PostgreSQL

`NÃO DISPONÍVEL`. Sem daemon, a imagem compatível não pôde ser iniciada.

## 6. Cadeia Alembic

Validação estática preservada:

```text
130_pool_asset_exclusions
  -> 131_ml_governance_v2
  -> 132_calibration_orchestration_v2
  -> 133_native_feature_capture (head)
```

## 7. Bootstrap

Não executado. O bloqueio ocorreu antes da criação do PostgreSQL descartável;
portanto não é possível classificar a reconstrução como
`BLOCKED_ALEMBIC_BOOTSTRAP`.

## 8. Migration 130

Não aplicada.

## 9. Migration 131

Não aplicada. SHA-256:
`BAA944AB2F82F932B4ED7CB74923252C173F3775E20080B16F378D83AA808327`.

## 10. Cenários DML da 131

Não executados. Nenhum DML foi direcionado a banco remoto ou da aplicação.

## 11. Migration 132

Não aplicada. SHA-256:
`92BDBD97102BF0B903A4CD41893FA807A16FAE033886422B5FB426D6CEC4951C`.

## 12. Migration 133

Não aplicada. SHA-256:
`2FEA640794E0B06FE493B0690F522FD4B5E5E4EED0FF5C8F44E5645477C641A4`.

## 13. Schema

Não validado em catálogo PostgreSQL real.

## 14. Trigger

Não criado nem consultado em PostgreSQL real.

## 15. Imutabilidade

Não exercitada em PostgreSQL real.

## 16. Índices e constraints

Não consultados em catálogo descartável. A evidência permanece somente
estática pelas migrations.

## 17. ON CONFLICT

Não exercitado em PostgreSQL real. A correção explícita do T4H permanece no
commit `eada593`.

## 18. Hash

O teste de persistir/reler/recalcular não foi executado por ausência do banco.
Hashes do inventário:

| arquivo | SHA-256 |
|---|---|
| `shadow_trade.py` | `D680EEC3900B549E8CB8D7F5C60CA033684458BE8670269A7D8DB419F41735AF` |
| `shadow_trade_service.py` | `DBFAF719BDAAA7BB889BB680E5CD48DB30720FAA7D90CF95733D35A1AC2E22B7` |
| `feature_contract_v2.py` | `9B33A74F27702B1A48639E953F4A3269C968EE5B58362B30B2000820666305B4` |

## 19. Testes

Nenhum novo teste PostgreSQL foi executado. Permanecem como última evidência os
resultados T4H: suíte ampliada `170 passed` e mínima `12 passed`.

## 20. Commits

Nenhum commit de baseline recuperado foi criado. Os gates PostgreSQL não foram
satisfeitos.

## 21. Estado Git

- Branch: `codex/ml-profile-intelligence-v2` [git].
- HEAD de entrada: `029b78ce75329b3e0b3c1707a76ff667617bd0c3` [git].
- Worktree preexistente preservado.
- Nenhum push, deploy, backfill ou migration de produção foi executado.

## 22. Destruição do banco

Não aplicável: nenhum container/database foi criado. Não existe recurso T4I a
destruir.

## 23. Riscos

- migrations 131–133 continuam sem validação PostgreSQL real;
- DML da 131 continua não exercitado;
- trigger da 133 continua não comprovado;
- índices parciais e `ON CONFLICT` continuam sem teste de integração;
- baseline integrado não pode ser consolidado.

## 24. Decisão final

```text
BLOCKED_DISPOSABLE_POSTGRES_UNAVAILABLE
```

## 25. Próxima fase

Disponibilizar um daemon Docker funcional, uma instalação PostgreSQL local
isolada ou um sandbox efêmero exclusivo. Em seguida, repetir integralmente o
T4I. T4F, baseline, push, deploy e migrations de produção continuam bloqueados.

## 26. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | COMANDO/QUERY | VALOR LITERAL |
|---|---|---|---|
| heads Alembic=1 | [test] | `alembic heads` | `133_native_feature_capture (head)` |
| containers T4I criados=0 | [test] | probes Docker | nenhum |
| databases T4I criados=0 | [test] | provisionamento | nenhum |
| migrations aplicadas=0 | [test] | execução T4I | nenhuma |
| commits de baseline=0 | [git] | `git log` | nenhum |
| pushes=0 | [git] | execução T4I | nenhum |
| deploys=0 | [git] | execução T4I | nenhum |

