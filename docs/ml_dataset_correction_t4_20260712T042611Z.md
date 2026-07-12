# T4 — Prova de imutabilidade e temporalidade

Status: BLOQUEADA — spot-check empírico falhou; P1 encerrada conforme o prompt.

Cutoff: `2026-07-12T04:26:11.753960Z` [query].

## Parte A — prova negativa de código

O comando obrigatório não retornou hits:

```powershell
Select-String -Path backend\**\*.py -Pattern "features_snapshot" -Context 1 |
  Where-Object { $_.Line -match "update|UPDATE|\.features_snapshot\s*=" }
```

Cross-check com `rg` encontrou apenas:

- modelos/serialização sem UPDATE;
- teste com atribuição em fixture;
- migration `127_shadow_features_snapshot_immutability.py`, que cria trigger `BEFORE UPDATE OF features_snapshot` e rejeita alteração pós-INSERT.

Parte A: ATENDIDA.

## Parte B — recomputação point-in-time

Amostra determinística:

- L3: `20` [query].
- L3_REJECTED: `20` [query].
- L1_SPECTRUM: `10` [query].
- Período: 01–10/jul.
- OHLCV: últimos `300` candles de `5m` com `time <= created_at` [query].
- Exchange disponível no período: somente `gate.io`, `251.886` candles [query]. Portanto, não houve mistura de exchanges.
- Fórmulas: implementações Wilder EWM de RSI(14) e ADX(14), espelhando `FeatureEngine`.

Resultado: `26/50` dentro das tolerâncias [query/calc], abaixo do mínimo `47/50`.

- Taxa de aprovação: `26 / 50 = 52%` [calc].
- Déficit para o aceite: `47 - 26 = 21` trades [calc].
- Falhas: `50 - 26 = 24` [calc].

Exemplos literais de falha:

| trade_id | source | RSI snapshot/recomputado | ADX snapshot/recomputado | deltas |
|---|---|---|---|---|
| `64b41075-8885-4542-b574-e93c0311b3cb` | L3_REJECTED | 64,48 / 56,05 | 19,49 / 18,90 | 8,43 / 0,59 [query] |
| `88e00716-44ef-44c8-8c31-fd856a07248a` | L3_REJECTED | 44,55 / 43,13 | 31,52 / 21,03 | 1,42 / 10,49 [query] |
| `7a6f543c-9fbc-466d-a519-6c6a21bf62ca` | L3 | 61,51 / 48,01 | 26,91 / 25,51 | 13,50 / 1,40 [query] |
| `02465e1d-66eb-482e-a4ec-abca0041d889` | L3 | 67,63 / 59,55 | 51,36 / 28,69 | 8,08 / 22,67 [query] |

Os 50 resultados, incluindo candles, valores e deltas, foram emitidos integralmente no output read-only da execução T4.

## Decisão obrigatória do prompt

O histórico deve ser classificado como `UNKNOWN_TEMPORALITY`; backfill fica limitado às linhas com `features_captured_at` populado. P1 encerra aqui antes de T5/T6.

Nenhuma escrita foi executada no banco. Nenhum backfill foi preparado ou aplicado.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| amostra=50 | [query] | `sample_n=50` |
| dentro da tolerância=26 | [query] | `within_tolerance=26` |
| falhas=24 | [calc] | `50 - 26` |
| mínimo exigido=47 | [prompt] | `>=47/50` |
| candles gate.io=251.886 | [query] | `exchange=gate.io; n=251886` |
