# T2 — Gate de decisão do contrato heterogêneo APPROVED

Status: AGUARDANDO DECISÃO

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

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| ATR_DYNAMIC total=481 | [query] | `total_n=481` |
| volume 11/jul=433 | [query] | `day=2026-07-11; n=433` |
| volume parcial 12/jul=48 | [query] | `day=2026-07-12; n=48` |
| déficit=2.519 | [calc] | `3000 - 481` |
| ETA rápida=5,8176 dias | [calc] | `2519 / 433` |
| ETA conservadora=10,4740 dias | [calc] | `2519 / 240.5` |

