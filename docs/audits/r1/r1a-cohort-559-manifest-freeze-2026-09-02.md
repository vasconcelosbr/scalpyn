# R1.A — manifesto congelado da coorte de 559

Data do congelamento: **2026-09-02** [git]. Escopo: somente leitura. Nenhuma linha
de `shadow_trades` foi alterada; este documento e `r1a_cohort_559_manifest.json`
são os únicos artefatos escritos.

## Por que isto era necessário

O predicado original do R0 (`entry_timestamp >= '2026-08-31 19:15:59.243302Z'`,
`exit_price_semantics = 'CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL'`, `status =
'COMPLETED'`), sem limite superior, deixou de reproduzir `559` assim que mais
Shadows completaram: hoje devolve `838` (`SL 371/TP 114/TRAILING 353`) [query].
Sem um corte de tempo fixo ou uma lista de IDs, a coorte "de 559" não é um
conjunto estável — é um instantâneo que se perde a cada novo Shadow concluído.

## Corte que reproduz a coorte exata

```sql
SELECT id, symbol, entry_timestamp, completed_at, outcome
FROM shadow_trades
WHERE entry_timestamp >= timestamptz '2026-08-31 19:15:59.243302Z'
  AND exit_price_semantics = 'CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL'
  AND status = 'COMPLETED'
  AND completed_at <= timestamptz '2026-09-01T20:25:18.679618Z'
ORDER BY id
```

Testado contra o Postgres produtivo (`default_transaction_read_only=on`,
somente `SELECT`) [query]:

| Métrica | Valor obtido | Valor gravado (R0/prompt) |
|---|---:|---:|
| N | 559 | 559 |
| SL_HIT | 246 | 246 |
| TP_HIT | 88 | 88 |
| TRAILING_STOP | 225 | 225 |
| TIMEOUT | 0 | 0 |
| Líquido médio | -0,2647280849374118% | -0,264728% |

Match exato em N, composição por desfecho e retorno líquido médio. **Esta é a
coorte correta.**

## Manifesto

`r1a_cohort_559_manifest.json` (mesmo diretório) contém, para cada um dos 559
Shadows: `id`, `symbol`, `entry_timestamp`, `completed_at`, `outcome`; mais a
query definidora, a composição por desfecho, o retorno líquido médio gravado, e
`id_list_sha256` — hash SHA-256 da lista de IDs ordenada, para detectar qualquer
mutação futura do conjunto.

```
id_list_sha256 = b8c4e875521e2ddda06be79630f3b4d8cd7c5b6bae7f990db08b900cce9f6667
```

## Uso obrigatório daqui em diante

Qualquer execução futura de R1.A (busca de velas `1m` finais na Gate, reparo,
reavaliação com barreiras congeladas) deve filtrar por
`shadow_trades.id = ANY(:ids)` usando a lista deste manifesto — nunca por
`entry_timestamp`/`completed_at` recalculado ao vivo, que já provou divergir.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL |
|---|---|---|
| N=559, SL/TP/TRAILING=246/88/225 | [query] | corte `completed_at <= 2026-09-01T20:25:18.679618Z` |
| líquido médio -0,2647280849374118% | [query] | `avg(net_return_pct)` sobre a mesma coorte |
| hash da lista de IDs | [calc] | `sha256(','.join(sorted(ids)))` |
| coorte ao vivo hoje sem corte superior | [query] | `n=838; SL=371,TP=114,TRAILING=353` |
