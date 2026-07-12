# T4J — PostgreSQL efêmero remoto e execução do T4I

## 1. Resumo executivo

Decisão final: `BLOCKED_RESOURCE_DESTRUCTION`.

Um projeto Railway exclusivo foi criado fora do projeto Scalpyn e recebeu um
único PostgreSQL efêmero. O isolamento foi comprovado e o schema começou vazio.
O bootstrap Alembic bloqueou na migration `000_baseline_prod_schema`, antes da
130, deixando uma sessão presa em um bloco `DO`. Após repetição controlada, o
comportamento se repetiu. O PostgreSQL service foi removido, mas o projeto ficou
agendado para exclusão diferida e ainda aparece na listagem; portanto o gate de
destruição integral não fechou.

## 2. Decisão de entrada

`BLOCKED_DISPOSABLE_POSTGRES_UNAVAILABLE` [prompt/documento].

## 3. Estratégia escolhida

Railway temporário e exclusivo. Nenhum recurso foi criado no projeto Scalpyn de
produção.

## 4. Provisionamento

- Projeto temporário: `t4j-disposable-1717`.
- Project ID mascarado: `ce11e9d1-****-****-****-********442a`.
- Service ID mascarado: `dfaf1243-****-****-****-********7400`.
- Environment ID mascarado: `0323ecbd-****-****-****-********1e36`.
- Serviço único: PostgreSQL Railway.
- Criado em `2026-07-12T17:17:12.297Z` [railway].

## 5. Isolamento

O project ID temporário difere do Scalpyn de produção. O projeto possuía somente
o PostgreSQL, sem app service, variáveis herdadas ou dados copiados. A consulta
inicial encontrou `0` tabelas públicas [postgres].

## 6. Versão PostgreSQL

`PostgreSQL 18.4 (Debian 18.4-1.pgdg13+1)` [postgres].

## 7. IDs mascarados

Somente os IDs mascarados acima são registrados. URL, usuário completo de
conexão e senha não foram gravados no repositório ou neste relatório.

## 8. Bootstrap Alembic

Em database vazio, `alembic upgrade 130_pool_asset_exclusions` iniciou:

```text
Running upgrade -> 000_baseline_prod_schema
```

Ele não concluiu a migration baseline. Uma segunda execução falhou ao criar
`alembic_version` com `LockNotAvailableError`, causada pela sessão órfã da
primeira execução. Após encerrar essa sessão e repetir com Python UTF-8, o
processo voltou a permanecer ativo no mesmo bloco `DO`.

Após janela adicional, `pg_stat_activity` mostrou a sessão no bloco que verifica
`trade_simulations_pkey`, com `wait_event_type='Client'` e
`wait_event='ClientRead'`. Decisão técnica: `BLOCKED_ALEMBIC_BOOTSTRAP`.

## 9. Migrations 130–133

- 130: não alcançada;
- 131: não executada;
- 132: não executada;
- 133: não executada.

## 10. DML da 131

Não executado, pois o bootstrap anterior à 130 não fechou.

## 11. Trigger e imutabilidade

Não exercitados. A migration 133 não foi alcançada.

## 12. Índices e constraints

Não validados no catálogo final porque a baseline foi revertida/não concluída.

## 13. ON CONFLICT

Não exercitado em PostgreSQL real nesta fase; o schema de `shadow_trades` não
foi reconstruído pelo bootstrap.

## 14. Hash

Não persistido nem recalculado no PostgreSQL efêmero.

## 15. Testes

Os testes PostgreSQL posteriores ao bootstrap não foram executados. Permanecem
como evidência anterior: `170 passed` na suíte ampliada e `12 passed` na mínima.

## 16. Baseline recuperado

Não criado. Os gates Alembic e destruição não fecharam.

## 17. Commits

Nenhum commit de código ou baseline foi criado nesta fase. Somente este relatório
pode ser versionado separadamente.

## 18. Higiene de secrets

- URL pública foi mantida apenas em memória de processo;
- nenhuma senha foi impressa;
- nenhum `.env` foi criado;
- nenhum secret de produção foi copiado;
- nenhuma URL completa foi commitada.

## 19. Destruição dos recursos

- PostgreSQL service: removido e ausente da listagem [railway].
- Serviços restantes no projeto: `0` [railway].
- Projeto: exclusão aceita, mas diferida; ainda aparece na listagem com
  `deletedAt='2026-07-14T17:23:39.521Z'` [railway].
- Credencial funcional: removida com o service.

Como o project ID ainda existe, a exigência literal de destruição verificável
não foi satisfeita.

## 20. Riscos

- a cadeia Alembic não recria o schema do zero neste ambiente;
- o bloqueio ocorre antes da migration 130;
- 131–133 continuam sem exercício PostgreSQL real;
- o projeto vazio permanece visível até a exclusão diferida do Railway;
- baseline recuperado continua não consolidado.

## 21. Decisão final

```text
BLOCKED_RESOURCE_DESTRUCTION
```

Bloqueio técnico associado:

```text
BLOCKED_ALEMBIC_BOOTSTRAP
```

## 22. Próxima fase

Confirmar após `deletedAt` que o project ID temporário deixou de existir. Em nova
execução efêmera, diagnosticar/corrigir a migration baseline ou o runtime que
fica preso no bloco `DO` antes de tentar migrations 130–133. T4F, baseline, push
e deploy permanecem bloqueados.

## 23. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | COMANDO/QUERY | VALOR LITERAL |
|---|---|---|---|
| projetos temporários criados=1 | [railway] | `railway project list --json` | `t4j-disposable-1717` |
| serviços PostgreSQL criados=1 | [railway] | `railway status --json` | `Postgres` |
| tabelas públicas iniciais=0 | [postgres] | `information_schema.tables` | `0` |
| sessões órfãs encontradas=1 | [postgres] | `pg_stat_activity` | `1` |
| sessões órfãs terminadas=1 | [postgres] | `pg_terminate_backend` | `True` |
| migrations 130–133 concluídas=0 | [test] | Alembic bootstrap | nenhuma |
| serviços restantes no projeto=0 | [railway] | `project list` | `0` |
| project IDs ainda listados=1 | [railway] | `project list` | `ce11e9d1-...` |
| pushes=0 | [git] | execução T4J | nenhum |
| deploys de produção=0 | [git] | execução T4J | nenhum |

