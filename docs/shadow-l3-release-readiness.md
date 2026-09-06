# Shadow L3: implementação e prontidão de publicação

Status da revisão anterior: IMPLEMENTADO LOCALMENTE; PUBLICAÇÃO ENTÃO BLOQUEADA.

Autorização posterior do operador: "aplicar em produção sem restrições para execução imediata após o deploy."
A restrição de calibração prévia foi removida. A implantação e a configuração passam a ser verificadas no release;
esta autorização não transforma evidência ausente em validação empírica.

As alterações estão no checkout isolado `codex/shadow-l3-continuation`.
A política inicial é OBSERVE, sem parâmetros econômicos preenchidos. O salvamento autenticado de uma configuração completa em APPLY autoriza a execução e gera auditoria. Nenhuma ordem real nem resultado histórico foi alterado.

## Validação local

Comando executado no backend, com PYTHONPATH incluindo a raiz e o backend e banco
PostgreSQL local exclusivo em SHADOW_L3_TEST_DATABASE_URL:

```
python -m pytest tests/test_shadow_l3_continuation.py tests/test_shadow_l3_persistence.py tests/test_shadow_trailing_contract.py tests/test_shadow_trailing_policy_v2.py tests/test_celery_routing_invariants.py tests/test_strategy_lab_native_capture.py tests/test_profile_runtime_config.py -q
```

Saída literal [query: testes locais]:

```
86 passed, 1 warning in 5.76s
```

O aviso é da dependência langchain/Pydantic com o Python local; não é falha do avaliador.
Os testes direcionados do frontend também passaram [query: Node test runner]:

```
node --import tsx --test lib/shadowPortfolioAnalysisScope.test.ts lib/shadowReportOutcomeFilters.test.ts lib/shadowRejectedConsolidation.test.ts
```

Saída literal:

```
tests 9
pass 9
fail 0
```

O build de produção do frontend passou. Interface autenticada publicada: NÃO CONFIRMADA.

## Impedimento de publicação

A skill `scalpyn-deploy-source-guardrails` exige interromper a publicação quando um
`rules_snapshot` persistido aparece sem condições esperadas. O achado abaixo já existe
em produção, antes da aplicação desta mudança; esses trades têm `INVALID_PROFILE_CONTRACT`.
Não foi corrigido nem reclassificado nesta implementação.

Consulta somente leitura [query: produção]:

```sql
SELECT s.id,s.symbol,s.watchlist_id,s.decision_id,s.profile_name,p.is_active,
       jsonb_array_length(COALESCE(p.config->'entry_triggers'->'conditions','[]')),
       s.rules_snapshot,s.lineage_status
FROM shadow_trades s LEFT JOIN profiles p ON p.id=s.profile_id
WHERE s.id IN (
 'ecbdddaf-7b2e-4a54-b8fa-774cfb29a2ea',
 '68581808-49a3-45dd-b18a-bb3721f4d7d5',
 'a90a4c60-bb10-4aec-b151-bf9a5cbf0142');
```

Valores literais obtidos nas consultas [query: produção]:

| shadow_id | símbolo | decision_id | perfil ativo | condições no perfil | rules_snapshot | lineage_status |
|---|---|---|---|---|---|---|
| 68581808-49a3-45dd-b18a-bb3721f4d7d5 | ASTER_USDT | 662873 | true | 5 | null | INVALID_PROFILE_CONTRACT |
| a90a4c60-bb10-4aec-b151-bf9a5cbf0142 | ADA_USDT | 662874 | true | 5 | null | INVALID_PROFILE_CONTRACT |
| ecbdddaf-7b2e-4a54-b8fa-774cfb29a2ea | DASH_USDT | 662876 | true | 5 | null | INVALID_PROFILE_CONTRACT |

A comparação da composição atual de configurações retornou literalmente:

```json
{"active_watchlists": 19, "trigger_mismatches": []}
```

Isso prova a preservação das condições na composição consultada; não corrige o snapshot
ausente nos trades. O replay do snapshot original da UNI retornou literalmente:

```json
{"allowed": true, "matched": ["obv", "volume_delta", "taker_ratio", "rsi", "macd_histogram"], "failed_required": [], "skipped": []}
```

Esse replay descreve apenas aquela entrada; não prova autorização das entradas inválidas.

## Etapas ainda pendentes

- Resolver o impedimento ou obter exceção explícita de publicação exclusivamente em observação.
- Integrar em main e executar novamente o guard de origem antes de publicar.
- Publicar migração aditiva, API, consumidores de dados e frontend pelo mesmo commit canônico.
- Verificar esquema, serviços, ingestão prospectiva, snapshots novos, gravação e leitura
  da configuração e interface autenticada. Não há registro PASS de release.
- Coletar evidência prospectiva, registrar critérios de aceite, comparar candidatos em
  períodos separados e aprovar a configuração antes de qualquer aplicação.

## Ledger de evidências

| Número reportado | Origem | Valor literal da fonte |
|---|---|---|
| Testes do backend | [query: pytest] | 86 passed, 1 warning in 5.76s |
| Testes direcionados do frontend | [query: Node test runner] | tests 9; pass 9; fail 0 |
| Watchlists na comparação | [query: composição canônica/efetiva] | active_watchlists: 19 |
| Condições esperadas por perfil dos casos listados | [query: produção] | 5, 5, 5 |
| Identificadores das decisões | [query: produção] | 662873, 662874, 662876 |

Os parâmetros numéricos nos testes são cenários sintéticos e não defaults econômicos.
