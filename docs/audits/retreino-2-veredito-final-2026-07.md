# Retreino Nº 2 v2 — Gate de Marco

Data: 2026-07-05 13:01:17 UTC
Status: **AGUARDANDO MARCO** — V0.1 falhou; V1–V4 não executados; nenhum treino, nenhum consumo de test set, nenhuma promoção.

## V0 — Pré-Voo

### Gate de Marco

```sql
SELECT COUNT(*)
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
  AND pnl_pct IS NOT NULL
  AND features_snapshot IS NOT NULL
  AND features_snapshot::text <> '{}'
  AND created_at >= (:dataset_query_cutoff - INTERVAL '30 days')
  AND created_at <= :dataset_query_cutoff
  AND created_at >= :ml_dataset_valid_from;
```

- Conexão: `production_psycopg2_ok` via Railway `Postgres` / `DATABASE_PUBLIC_URL` (segredo omitido). [query]
- `ml_dataset_valid_from`: `2026-06-14 21:33:10.277143+00`. [config: ml]
- `ml_win_fast_threshold_seconds`: `14400`; `dataset_query_cutoff = NOW() - 14400s`: `2026-07-05 09:01:18.449105+00:00`. [config/query]
- Elegíveis pela query do trainer: `2494`. [query]
- Marco `ml_retrain_min_eligible_rows`: `2800`. [config: ml]
- Resultado: `2494 < 2800` → **PARAR**. [calc]
- Range temporal dos elegíveis: `2026-06-14 21:33:10.591666+00:00` até `2026-07-05 09:00:19.883276+00:00`. [query]

### Inflow recente

| dia UTC | elegíveis novos |
| --- | --- |
| 2026-06-28 | 85 |
| 2026-06-29 | 224 |
| 2026-06-30 | 166 |
| 2026-07-01 | 329 |
| 2026-07-02 | 255 |
| 2026-07-03 | 178 |
| 2026-07-04 | 165 |

- Média dos últimos 6 dias completos disponíveis: `219.5/dia`. [calc]
- Déficit: `2800 - 2494 = 306`. [calc]
- Data projetada do marco: `2026-07-07 (ceil(306/219.5) dias)`. [calc]

## Decisão Operacional

Como o marco não foi atingido, o prompt determina parar em V0. Portanto:
- V1 não foi executado.
- V2 não foi executado; test set não foi lido.
- V3 não foi executado.
- V4/plano de ramo não dispara.
- `ml_forward_scoring_enabled` não foi alterado.
- Nenhum modelo foi treinado, promovido ou rejeitado nesta execução.

## Ledger de Evidências

| número/reportado | origem | valor literal/fórmula |
| --- | --- | --- |
| conexão | [query] psycopg2 readonly | production_psycopg2_ok |
| elegíveis | [query] COUNT trainer | 2494 |
| marco | [config: ml] | 2800 |
| déficit | [calc] marco - elegíveis | 2800 - 2494 = 306 |
| média diária | [calc] últimos 6 dias completos | 219.5/dia |
| projeção | [calc] ceil(déficit/média) | 2026-07-07 (ceil(306/219.5) dias) |

## Runner Output Verbatim

```json
{
  "connection": "production_psycopg2_ok",
  "now_utc": "2026-07-05 13:01:18.449105",
  "dataset_query_cutoff": "2026-07-05 09:01:18.449105+00:00",
  "valid_from": "2026-06-14 21:33:10.277143+00",
  "win_fast_threshold_seconds": 14400,
  "ml_retrain_min_eligible_rows": 2800,
  "eligible": 2494,
  "remaining": 306,
  "first_created": "2026-06-14 21:33:10.591666+00:00",
  "last_created": "2026-07-05 09:00:19.883276+00:00",
  "inflow": [
    [
      "2026-06-28",
      85
    ],
    [
      "2026-06-29",
      224
    ],
    [
      "2026-06-30",
      166
    ],
    [
      "2026-07-01",
      329
    ],
    [
      "2026-07-02",
      255
    ],
    [
      "2026-07-03",
      178
    ],
    [
      "2026-07-04",
      165
    ]
  ],
  "mean_daily_last_6_complete_days": 219.5,
  "projected_marco": "2026-07-07 (ceil(306/219.5) dias)"
}
```
