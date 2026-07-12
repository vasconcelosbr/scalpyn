# T2 — Gate de decisão do contrato heterogêneo APPROVED

Status: CONCLUÍDA NO CÓDIGO — opção C aprovada; ativação aguarda configuração/deploy.

Cutoff herdado de T0A: `2026-07-12T04:26:11.753960Z` [query]. Nenhum filtro de contrato foi alterado.

## Composição diária da lane L3

| Dia UTC | barrier_mode | tp_pct_applied | N |
|---|---|---:|---:|
| 2026-07-01 | FIXED | 1,0 | 78 [query] |
| 2026-07-01 | FIXED | 1,5 | 12 [query] |
| 2026-07-02 | FIXED | 1,0 | 741 [query] |
| 2026-07-03 | FIXED | 1,0 | 904 [query] |
| 2026-07-04 | FIXED | 1,0 | 586 [query] |
| 2026-07-05 | FIXED | 1,0 | 487 [query] |
| 2026-07-06 | FIXED | 1,0 | 309 [query] |
| 2026-07-07 | FIXED | 1,0 | 282 [query] |
| 2026-07-08 | FIXED | 0,6 | 218 [query] |
| 2026-07-08 | FIXED | 1,0 | 44 [query] |
| 2026-07-09 | FIXED | 0,6 | 46 [query] |
| 2026-07-10 | FIXED | 0,6 | 43 [query] |
| 2026-07-11 | ATR_DYNAMIC | 1,5 | 433 [query] |
| 2026-07-11 | FIXED | 0,6 | 73 [query] |
| 2026-07-12 parcial até 04:26 UTC | ATR_DYNAMIC | 1,5 | 48 [query] |

Leitura: a migração para ATR_DYNAMIC aparece em 11/jul, mas há somente `2` dias observados e o segundo é parcial [query]. A evidência indica migração recente, ainda com baixa significância temporal.

## Ritmo para N homogêneo

- ATR_DYNAMIC observado desde 05/jul: `481` [query].
- Déficit até `3.000`: `3.000 - 481 = 2.519` [calc].
- Dia completo de 11/jul: `433/dia` [query].
- Média/mediana ingênua incluindo 12/jul parcial: `240,5/dia` [query, N=2 dias].
- Cenário pelo único dia completo: `2.519 / 433 = 5,8176 dias`; ETA aproximada `2026-07-17 23:59 UTC` [calc].
- Cenário conservador pela média com dia parcial: `2.519 / 240,5 = 10,4740 dias`; ETA aproximada `2026-07-22 15:49 UTC` [calc].

As ETAs são projeções frágeis, não garantias, porque há somente um dia completo sob o contrato novo.

## Opções

### A — ATR_DYNAMIC somente

Filtrar APPROVED imediatamente para ATR_DYNAMIC e não retreinar até `N >= 3.000`. Preserva homogeneidade, mas deixa aproximadamente `481` linhas no cutoff e adia treino.

### B — Contract-aware

Manter contratos históricos, incluir as cinco features econômicas nas lanes APPROVED e REJECTED e estratificar/pesar o split por `(barrier_mode, tp_pct_applied)`. Preserva volume, mas exige validação rigorosa por contrato para impedir que o contrato dominante esconda colapso em outro.

### C — Híbrido

Aplicar B agora e migrar para A quando o volume ATR_DYNAMIC homogêneo atingir o gate configurado. É a recomendação padrão do prompt e preserva informação enquanto a nova política acumula evidência.

## Decisão e implementação

O usuário escolheu C.

- A lane APPROVED mantém todos os contratos enquanto `homogeneous_contract_rows < ml_l3_atr_dynamic_only_min_rows`.
- Ao atingir o gate configurado, a mesma política migra para o contrato ativo `shadow_barrier_mode + shadow_tp_pct`.
- As cinco features `tp_pct_applied`, `sl_pct_applied`, `reward_risk_ratio`, `break_even_probability` e `barrier_mode_encoded` agora entram em todas as lanes CatBoost, inclusive REJECTED.
- Cada split recebe pesos inversos por `(barrier_mode, tp_pct_applied)`; nas lanes de inteligência, esses pesos são combinados com o peso por evento e reescalados para preservar o `effective_n` por evento.
- `contract_distribution_by_split` é persistida nos metadados do modelo.
- Ausência de `ml_l3_atr_dynamic_only_min_rows` falha fechado; nenhum valor foi hardcoded.

## Critério de aceite

- Features econômicas em APPROVED e REJECTED: ATENDIDO no builder compartilhado.
- Balanceamento por contrato: ATENDIDO por pesos inversos dentro de cada split.
- Distribuição por split persistida: ATENDIDO em `contract_distribution_by_split` quando o treino for executado.
- Migração ATR_DYNAMIC condicionada a N: ATENDIDO por gate configurável.
- Evidência runtime de treino: AGUARDANDO configuração/deploy; nenhum retreino é permitido antes de fechar P0.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| ATR_DYNAMIC total=481 | [query] | `total_n=481` |
| volume 11/jul=433 | [query] | `day=2026-07-11; n=433` |
| volume parcial 12/jul=48 | [query] | `day=2026-07-12; n=48` |
| déficit=2.519 | [calc] | `3000 - 481` |
| ETA rápida=5,8176 dias | [calc] | `2519 / 433` |
| ETA conservadora=10,4740 dias | [calc] | `2519 / 240.5` |
