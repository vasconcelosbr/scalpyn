# RELATÓRIO T4B — Backfill com temporalidade comprovada

## 1. Resumo executivo

Status: BLOQUEADO. Decisão final: `BLOCKED_NO_ELIGIBLE_ROWS`.

O campo `features_captured_at` foi introduzido sem default e sem retrofill pela migration `131_ml_governance_v2`, mas o fluxo principal o preenche com `decision.created_at`. Esse é exatamente um fallback proibido pelo prompt T4B: timestamp presente, porém não uma captura independente comprovada. No cutoff T4, nenhuma linha atende temporalidade provada.

## 2. Commit de partida

`ea00871` [git].

## 3. Escopo e restrições

- Auditoria de código, Git, migration, schema e produção.
- Banco acessado em transação `REPEATABLE READ` com `SET TRANSACTION READ ONLY` e `ROLLBACK` [query].
- Nenhum DDL/DML, migration, backfill, treino, promoção ou alteração de flag.

## 4. Linha do tempo e origem de `features_captured_at`

1. A coluna aparece na migration `backend/alembic/versions/131_ml_governance_v2.py`, revisão `131_ml_governance_v2`, criada em 11/jul [code].
2. Tipo: `TIMESTAMPTZ`, nullable, sem default [code/query].
3. A migration declara explicitamente que não executa backfill histórico [code].
4. Não há trigger para preencher `features_captured_at` [query].
5. O fluxo `_create_from_decision` persiste `features_captured_at = getattr(decision, "created_at", None)` [code].
6. Esse valor é derivado do timestamp da decisão, não de uma evidência autônoma da captura; o prompt proíbe `decision.created_at` como fallback [prompt].
7. O fluxo Strategy Lab recebe `captured_at=promotion_at` [code], igualmente sem prova independente suficiente para autorizar backfill nesta execução.

Era de coluna nativa: primeiro valor observado em `2026-07-11T17:14:19.587466Z` [query]. Era de temporalidade comprovada: NÃO IDENTIFICADA.

## 5. Imutabilidade

A trigger ativa `trg_shadow_features_snapshot_immutable`, `BEFORE UPDATE`, executa `prevent_shadow_features_snapshot_update()` [query]. O snapshot é imutável após INSERT, mas imutabilidade não prova a origem temporal do timestamp.

## 6. Contrato e classificação

- `ELIGIBLE_PROVEN_TEMPORALITY`: prova positiva de captura original, lineage completa e nenhuma origem proibida.
- `UNKNOWN_TEMPORALITY`: timestamp ausente ou origem não demonstrada.
- `INVALID_TEMPORALITY`: origem demonstradamente proibida, inferida ou inconsistente.

Reason codes aplicados:

- `MISSING_FEATURES_CAPTURED_AT` e `CAPTURE_TIMESTAMP_SOURCE_UNPROVEN` para linhas sem timestamp.
- `CAPTURE_TIMESTAMP_INFERRED_FROM_CREATED_AT` para linhas preenchidas pelo fluxo `decision.created_at`.
- `MISSING_*_LINEAGE` adicional quando aplicável.

O seletor seguro aceita somente `ELIGIBLE_PROVEN_TEMPORALITY`. Como o total elegível é zero, não foi criado comando de backfill nem seletor permissivo.

## 7. Evidências SQL e resultados literais

### Estrutura

```text
shadow_trades.features_captured_at | timestamp with time zone | nullable=YES | default=NULL
shadow_trades.features_snapshot    | jsonb                    | nullable=YES | default=NULL
```

### Distribuição no cutoff

```text
total=110479
with_snapshot=110479
without_snapshot=0
with_captured_at=3484
without_captured_at=106995
superficially_complete=3484
```

### Consistência temporal

```text
captured_after_created=0
captured_equal_created=0
captured_before_created=3484
min_captured_at=2026-07-11T17:14:19.587466Z
max_captured_at=2026-07-12T04:23:52.762671Z
```

O fato de todos os timestamps precederem o INSERT do shadow é coerente com a cópia de `decision.created_at`; não transforma essa cópia em prova de captura.

### Lineage

```text
total_with_captured_at=3484
incomplete_lineage=72
```

### Por source

| source | total | com snapshot | com captured_at |
|---|---:|---:|---:|
| L3_REJECTED | 76.624 [query] | 76.624 [query] | 3.044 [query] |
| L3 | 19.583 [query] | 19.583 [query] | 160 [query] |
| L3_LAB | 7.374 [query] | 7.374 [query] | 207 [query] |
| L1_SPECTRUM | 4.615 [query] | 4.615 [query] | 53 [query] |
| L3_SIMULATED | 2.283 [query] | 2.283 [query] | 20 [query] |

## 8. Dry-run e resultado por lane

Classificação no cutoff:

```text
ELIGIBLE_PROVEN_TEMPORALITY=0
UNKNOWN_TEMPORALITY=106995
INVALID_TEMPORALITY=3484
```

Disponibilidade para backfill por lane:

- L1 XGBoost: `0` elegíveis [calc: seletor exige proveniência positiva].
- L3 LightGBM: `0` elegíveis [calc].
- L3 CatBoost: `0` elegíveis [calc].

Nenhuma capacidade de treino futuro pode ser inferida deste backfill.

## 9. Reconciliação

`110.479 = 0 + 106.995 + 3.484` [calc]. A reconciliação fecha exatamente.

Totais por source: `76.624 + 19.583 + 7.374 + 4.615 + 2.283 = 110.479` [calc].

Reason codes não são mutuamente exclusivos; uma linha pode acumular falha temporal e de lineage.

## 10. Segurança aplicada

O fallback `features_captured_at or created_at` foi removido de `_snapshot_group_key`. Na ausência de timestamp comprovado, o agrupador cai em `feature_only:` diagnóstico; não promove temporalidade.

## 11. Testes

Teste direcionado garante que `created_at` não altera a chave quando `features_captured_at` está ausente. UNKNOWN/INVALID permanecem fora porque não existe seletor de backfill habilitado e o resultado elegível é zero.

## 12. Arquivos alterados

- `backend/app/services/ml_challenger_service.py`: remove fallback temporal proibido.
- `backend/tests/test_snapshot_group_identity.py`: prova contra fallback a `created_at`.
- Este relatório: evidências e decisão.

## 13. Riscos e condição da próxima fase

T5/T6 permanecem proibidas. Para reabrir o desenho seria necessário introduzir futuramente uma fonte de captura nativa independente, atômica e auditável, acumular linhas novas e provar lineage completa. Nenhuma linha atual pode ser convertida por heurística.

## 14. Decisão final

`BLOCKED_NO_ELIGIBLE_ROWS`.

## 15. Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | QUERY/COMANDO | VALOR LITERAL |
|---|---|---|---|
| total=110.479 | [query] | distribuição geral | `110479` |
| timestamp presente=3.484 | [query] | distribuição geral | `3484` |
| timestamp ausente=106.995 | [query] | distribuição geral | `106995` |
| lineage incompleta=72 | [query] | validação lineage | `72` |
| elegíveis=0 | [query/code] | classificador fail-closed | `0` |
| reconciliação | [calc] | `0+106995+3484` | `110479` |

