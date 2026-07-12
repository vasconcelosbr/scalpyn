# T1 — Embargo de maturidade

Status: BLOQUEADA — critérios do prompt são mutuamente incompatíveis com o holding observado.

Cutoff herdado de T0A: `2026-07-12T04:26:11.753960Z` [query].

## Pré-validação

Query obrigatória:

```sql
SELECT ttt_timeout_minutes, COUNT(*),
       MAX(EXTRACT(EPOCH FROM (completed_at - created_at))/60) AS max_holding_min
FROM shadow_trades
WHERE completed_at >= '2026-07-01'
GROUP BY 1 ORDER BY 2 DESC;
```

Output no universo congelado:

```text
ttt_timeout_minutes=180 | count=83454 | max_holding_min=3184.9268586
```

## Contradição

- Embargo sugerido: `180 + 60 = 240 min` [calc].
- Pior holding observado: `3.184,9268586 min` [query].
- Margem mínima para cobrir o pior caso: `3.184,9268586 - 180 = 3.004,9268586 min` [calc].
- Horizonte total correspondente: `3.184,9268586 min = 53,08211431 h` [calc: `/60`].
- O critério simultâneo exige que nenhuma linha com `created_at < cutoff - 24h` seja afetada. Um embargo de `53,08211431 h` necessariamente ultrapassa `24 h`; portanto, não é possível atender aos dois critérios com a fórmula especificada.

## Dry-run da margem sugerida de 60 min

```text
excluded on 2026-07-12 = 564
middle rows created before cutoff-24h = 102333
middle rows excluded = 0
```

Comparação de positive rate:

| cohort | N | positivos | positive_rate |
|---|---:|---:|---:|
| miolo 48h anterior | 13.715 [query] | 5.774 [query] | 42,0999% [query] |
| cauda 48h sem embargo | 13.774 [query] | 5.370 [query] | 38,9865% [query] |
| cauda 48h com embargo de 240 min | 13.210 [query] | 5.068 [query] | 38,3649% [query] |

- Gap sem embargo: `42,0999% - 38,9865% = 3,1134 p.p.` [calc].
- Gap com embargo sugerido: `42,0999% - 38,3649% = 3,7350 p.p.` [calc].
- A margem sugerida mantém o gap abaixo de `5 p.p.`, mas não o reduz; nessa janela agregada ele aumenta `0,6216 p.p.` [calc].

## Mudança

Nenhuma mudança de código/config foi aplicada. A implementação fica bloqueada até a política ser escolhida explicitamente entre:

1. embargo operacional limitado ao horizonte contratual (`ttt_timeout + margem`), abandonando o requisito de cobrir o maior holding histórico anômalo; ou
2. maturidade baseada em `label_resolved_at/completed_at` e `max_label_horizon`, com tratamento explícito das resoluções tardias; ou
3. embargo pelo pior holding observado, aceitando que o miolo de 24h será afetado.

## Critério de aceite

- Cobrir pior holding: NÃO ATENDIDO pela margem sugerida.
- Não afetar miolo anterior a 24h: ATENDIDO apenas pela margem sugerida de 60 min.
- Gap de cauda abaixo de 5 p.p.: ATENDIDO antes e depois, mas não houve redução.
- Zero hardcode: preservado; nenhuma constante/configuração foi criada.

## Efeitos colaterais verificados

- Nenhuma escrita no banco.
- Nenhuma alteração em `config_profiles`.
- Nenhum retreino/deploy.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| timeout=180 min | [query] | `ttt_timeout_minutes=180` |
| N=83.454 | [query] | `count=83454` |
| max holding=3.184,9268586 min | [query] | `max_holding_min=3184.9268586` |
| margem mínima=3.004,9268586 min | [calc] | `3184.9268586 - 180` |
| excluídas=564 | [query] | `day=2026-07-12; excluded_n=564` |
| gap com embargo=3,7350 p.p. | [calc] | `0.4209989063 - 0.3836487509` |

