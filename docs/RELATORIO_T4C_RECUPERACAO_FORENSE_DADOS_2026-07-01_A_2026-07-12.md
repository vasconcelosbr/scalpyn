# T4C — Recuperação forense 01–12/jul

## Decisão

`BLOCKED_HISTORICAL_RECOVERY_NOT_PROVEN`.

Sessão read-only, `REPEATABLE READ`, `SET TRANSACTION READ ONLY`, encerrada com `ROLLBACK`.

## Evidência encontrada

- Universo do período: `83.982` shadow trades [query].
- Com `decision_id`: `4.304` [query].
- Decisions órfãs: `0` [query].
- Decisions com `indicators_snapshot` upstream: `4.304` [query].
- Upstream com timestamp próprio por feature: `0` [query].
- Com `ranking_id`: `0` [query].

Os snapshots upstream provam conteúdo para parte do universo, mas não o timestamp independente exigido. `decision.created_at` permanece prova insuficiente.

## Classificação e reconciliação

- `ELIGIBLE_RECOVERED_INDEPENDENT_EVIDENCE=0` [query/code].
- `INVALID_TEMPORALITY=3.484` [query: timestamp derivado do fluxo anterior].
- `RESEARCH_ONLY_UNPROVEN_TEMPORALITY=80.498` [calc: `83.982 - 3.484`].

Reconciliação: `83.982 = 0 + 80.498 + 3.484` [calc].

## Uso permitido

As `80.498` linhas sem prova temporal podem permanecer em `ML_DATASET_LEGACY_RESEARCH_ONLY`, nunca para treino/holdout/aprovação oficiais. Nenhuma escrita ou classificação foi persistida.

## Ledger

| NÚMERO | ORIGEM | VALOR LITERAL |
|---|---|---|
| total período | [query] | `83982` |
| decisions vinculadas | [query] | `4304` |
| upstream com snapshot | [query] | `4304` |
| upstream com timestamp independente | [query] | `0` |
| recuperável | [query/code] | `0` |
| research-only | [calc] | `83982 - 3484 = 80498` |

