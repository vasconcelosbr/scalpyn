# T0B — Identidade canônica do snapshot/evento

Status: CONCLUÍDA

Cutoff herdado de T0A: `2026-07-12T04:26:11.753960Z` [query].

## Pré-validação

| Medida | Resultado |
|---|---:|
| Grupos do hash legado com mais de um timestamp | 11.629 [query] |
| Timestamps distintos dentro desses grupos | 81.734 [query] |
| Feature hashes presentes em mais de um símbolo | 19 [query] |
| Maior quantidade de símbolos em um feature hash | 36 [query] |
| Feature hashes presentes em mais de um dia | 7 [query] |
| Maior quantidade de dias em um feature hash | 2 [query] |
| Linhas com `snapshot_id` | 3.484 [query] |
| Linhas com `event_id` | 3.484 [query] |
| Linhas com `features_captured_at` | 3.484 [query] |
| `effective_n` legado | 38.517 [query] |
| `effective_n` canônico | 48.877 [query] |
| Grupos canônicos cruzando símbolos | 0 [query] |

`effective_n` canônico é `10.360` maior que o legado [calc: `48.877 - 38.517`]. A diferença é evidência de que hash puro de features colapsava observações de mercado independentes.

## Mudança

- `backend/app/services/ml_challenger_service.py`: `_snapshot_group_key` agora aplica a prioridade `snapshot_id` → `event_id + symbol + timeframe + exchange` → fallback histórico com mercado, minuto capturado e feature hash → hash puro somente diagnóstico.
- `_load_shadow_data` passou a carregar `features_captured_at`, `timeframe` e `exchange`, necessários à identidade point-in-time.
- O contrato foi versionado como `market_event_v1` e é persistido em `metrics_json.grouping_contract_version` nos próximos modelos.
- `backend/tests/test_snapshot_group_identity.py`: cobre prioridade, separação entre mercados/minutos, agrupamento de Profiles do mesmo evento e versionamento.

## Critério de aceite

- Eventos temporalmente distintos não são unidos apenas por features iguais: ATENDIDO pela inclusão obrigatória do bucket temporal no fallback.
- Profiles do mesmo evento compartilham grupo: ATENDIDO por `snapshot_id`/`event_id` e pelo fallback no mesmo minuto.
- Peso inverso usa grupo canônico: ATENDIDO; o call site existente recebe o retorno atualizado de `_snapshot_group_key`.
- Zero grupo entre splits: ATENDIDO pelo purge existente em `_chronological_split_with_embargo`, validado por teste direcionado do agrupamento e pelos testes existentes de split.
- `grouping_contract_version` persistido: ATENDIDO em `metrics_json` e no payload serializado do modelo.

## Efeitos colaterais verificados

- Nenhuma escrita no banco ou em `config_profiles`.
- Label, features econômicas e threshold não foram alterados.
- O fallback `feature_only` só é alcançado se todos os identificadores e timestamps estiverem ausentes; permanece marcado como diagnóstico, não identidade plena.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| grupos multi-timestamp=11.629 | [query] | `groups=11629` |
| timestamps nesses grupos=81.734 | [query] | `distinct_timestamps_in_groups=81734` |
| hashes cross-symbol=19 | [query] | `hashes=19; max_symbols=36` |
| effective_n legado=38.517 | [query] | `legacy_effective_n=38517` |
| effective_n canônico=48.877 | [query] | `canonical_effective_n=48877` |
| delta=10.360 | [calc] | `48877 - 38517` |

