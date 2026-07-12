# T3 — Gate do `ml_dataset_valid_from`

Status: CONCLUÍDA — usuário confirmou manter `2026-07-01`.

Cutoff herdado de T0A: `2026-07-12T04:26:11.753960Z` [query].

## Histórico

O código fail-closed para `ml_dataset_valid_from` entrou no commit `34f5b164ffcf36d68716f92df90532187fb32bd4`, em `2026-07-04T11:17:13-03:00`, autor `vasconcelosbr` [git]. O Git registra a implementação do contrato, não a mutação dos valores de produção.

O audit log de produção registra:

```json
{
  "id": "c75a6395-8c27-4c05-8204-d670c6539f62",
  "config_id": "4e445c54-3a00-4478-98c5-3336ee6fb425",
  "changed_by": "8080110c-ee9d-4a2b-a53f-6bef86dd8867",
  "changed_at": "2026-07-08T21:35:01.558689+00:00",
  "change_description": "Set ML dataset_valid_from to 2026-07-01 for L1 and L3",
  "previous_ml_dataset_valid_from": "2026-07-05T19:45:49+00:00",
  "new_ml_dataset_valid_from": "2026-07-01T00:00:00+00:00"
}
```

Config atual:

```json
{
  "ml_dataset_valid_from": "2026-07-01T00:00:00+00:00",
  "ml_l3_dataset_valid_from": "2026-07-11T03:21:06+00:00"
}
```

Fonte: `[config: ml]`, `id=4e445c54-3a00-4478-98c5-3336ee6fb425`, `updated_at=2026-07-11T14:43:28.315129+00:00`.

## Homogeneidade L1 desde junho

| Semana UTC | barrier_mode | tp_pct_applied | N | mediana de chaves |
|---|---|---:|---:|---:|
| 08/jun | ATR_DYNAMIC | 1,5 | 20 [query] | 93 [query] |
| 08/jun | FIXED | 0,8 | 228 [query] | 93 [query] |
| 08/jun | FIXED | 1,0 | 991 [query] | 86 [query] |
| 15/jun | ATR_DYNAMIC | 1,5 | 481 [query] | 92 [query] |
| 22/jun | ATR_DYNAMIC | 1,5 | 619 [query] | 85 [query] |
| 29/jun | ATR_DYNAMIC | 1,5 | 1.449 [query] | 92 [query] |
| 06/jul | ATR_DYNAMIC | 1,5 | 827 [query] | 92 [query] |

## Leitura e recomendação

O histórico desde 01/jun não é homogêneo:

- a primeira semana observada mistura três contratos [query];
- a mediana de riqueza varia de `85` a `93` chaves [query];
- a convergência para ATR_DYNAMIC ocorre somente depois da semana inicial.

Pelo gate definido no prompt, não há suporte para recuar cegamente a fronteira a 01/jun. Recomendação: manter `2026-07-01` e substituir o gate bruto da L1 pelo readiness multidimensional de T9.

## Mudança

Nenhuma. `config_profiles` permaneceu intacta.

Decisão do usuário: manter `ml_dataset_valid_from=2026-07-01T00:00:00+00:00` e tratar readiness em T9.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| mudança em 08/jul 21:35:01 UTC | [query] | `changed_at=2026-07-08T21:35:01.558689+00:00` |
| fronteira anterior=05/jul | [query] | `2026-07-05T19:45:49+00:00` |
| fronteira nova=01/jul | [query] | `2026-07-01T00:00:00+00:00` |
| FIXED 1,0 na semana 08/jun=991 | [query] | `n=991` |
| FIXED 0,8 na semana 08/jun=228 | [query] | `n=228` |
| ATR_DYNAMIC na semana 08/jun=20 | [query] | `n=20` |
| faixa de mediana=85–93 | [query] | `min=85; max=93` |
