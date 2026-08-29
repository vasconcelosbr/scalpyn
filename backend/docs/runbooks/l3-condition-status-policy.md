# Política de status das condições L3

Contrato de envelope: `l3_gate_evaluation_envelope_v3`.

As seis políticas abaixo pertencem a `spot_engine.scanner`, são editáveis em
`/settings/strategies` e são recarregadas no início de cada scan. O snapshot
completo, a versão e o hash estável da configuração acompanham cada envelope.

| Camada | Ausente/inválido | Efeito autorizado |
|---|---|---|
| `signals` | `SKIPPED`; condição `required` fecha o gate | fail-closed somente para `required` |
| `entry_triggers` | `SKIPPED`; condição `required` fecha o gate | fail-closed somente para `required` |
| `global_entry_triggers` | mesma regra dos triggers do profile | gate adicional em `AND` |
| `block_rules` simples | `SKIPPED` nunca dispara a proteção | fail-open observável |
| `block_rules` `OR` | ignora condições puladas; todas puladas tornam o grupo `SKIPPED` | fail-open observável |
| `block_rules` `AND` | `legacy`: uma pulada torna o grupo `SKIPPED`; `not_satisfied`: grupo `FAIL` | ambas não disparam a proteção |

Motivos persistidos: `indicator_not_available`, `indicator_invalid_value`,
`zero_not_allowed`, `indicator_not_implemented` e
`rule_disabled_missing_indicator`.

`l3_zero_is_value` não transforma qualquer zero em dado válido. Ele libera zero
somente para indicadores cujo domínio canônico admite esse valor; atualmente,
`volume_spike`. Restrições de domínio de ADX, larguras e spread continuam
válidas.

`breakout_distance_pct` e `psar_trend` constam no catálogo estrutural como sem
produtor canônico no objeto L3. Com `l3_missing_indicator_policy=warn`, cada
ocorrência permanece visível como `SKIPPED/indicator_not_implemented`. Com
`disable_rule`, a ocorrência fica explicitamente desativada e identificada por
`rule_disabled_missing_indicator`; ela nunca falha em silêncio.

O toggle `l3_v3_contract_preserve` controla o merge não destrutivo posterior ao
Social Score. Mesmo com o toggle desligado, uma decisão `ALLOW` sem contrato v3
válido gera o erro estruturado `ALLOW_WITHOUT_VALID_V3` no limite de escrita do
outbox.
