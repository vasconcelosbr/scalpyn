# T1 — Embargo de maturidade

Status: CONCLUÍDA NO CÓDIGO — política 2 aprovada pelo usuário; ativação aguarda configuração/deploy.

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

## Decisão e mudança

O usuário aprovou a política 2: maturidade baseada em resolução point-in-time e horizonte máximo do label, sem converter o maior holding histórico anômalo em embargo global.

- `_load_shadow_data` agora exige `dataset_query_cutoff` único.
- A linha só entra quando `COALESCE(label_resolved_at, completed_at) <= dataset_query_cutoff`.
- A observação também precisa estar madura: `created_at <= dataset_query_cutoff - (ttt_timeout_minutes + ml_maturity_embargo_margin_minutes)`.
- `ml_maturity_embargo_margin_minutes` é obrigatório, não negativo e lido de `config_profiles(config_type='ml')`; ausência falha fechado.
- O mesmo cutoff é persistido em `ml_models.dataset_query_cutoff` para ambas as lanes.
- A chave ainda não foi gravada em produção porque o prompt proíbe escrita de configuração fora de T6.

## Critério de aceite

- Tratar resoluções tardias sem embargo global pelo outlier: ATENDIDO pela política aprovada.
- Não afetar miolo anterior a 24h: ATENDIDO no dry-run da margem candidata de 60 min.
- Gap de cauda abaixo de 5 p.p.: ATENDIDO antes e depois, mas não houve redução.
- Zero hardcode: ATENDIDO; margem obrigatória vem de `config_profiles`.
- Ativação em produção: AGUARDANDO a chave de configuração e deploy.

## Efeitos colaterais verificados

- Nenhuma escrita no banco.
- Nenhuma alteração em `config_profiles`; ausência da chave bloqueia treino de forma explícita.
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
