# R1.C.2 — quote_volume do canônico: item de correção do corte, não urgência isolada

Data: **2026-09-02** [git]. Escopo: somente leitura.

## Achado

O contrato `v1` já havia mostrado `13/455` divergências de `quote_volume` por
escala `0,0001`, atribuídas a conversão binária de float, corrigidas no
caminho novo (`ohlcv_shadow`) por normalização decimal
(`_normalize_state_db_record`, `research_ohlcv_service.py:450-463`) [code].

Verificado agora se o mesmo defeito persiste na tabela canônica `ohlcv` que
**ainda alimenta produção**: isolando pares onde `open/high/low/close` batem
exatamente entre `ohlcv` (canônico) e `ohlcv_shadow` (v3) — o que remove o
efeito de candle aberto/contaminado e isola só o erro de escala — e olhando
somente `volume`/`quote_volume`:

```sql
SELECT count(*) FILTER (WHERE abs(o.quote_volume - s.quote_volume) = 0.0001) AS exactly_0001_qv
FROM ohlcv_shadow s
JOIN ohlcv o ON o.time=s.time AND o.symbol=s.symbol AND o.exchange=s.exchange
  AND o.timeframe=s.timeframe AND o.market_type=s.market_type
WHERE s.timeframe='1m' AND s.capture_contract_version='gate_ohlcv_state_v3'
  AND o.open=s.open AND o.high=s.high AND o.low=s.low AND o.close=s.close
```

| Métrica | Valor |
|---|---:|
| Pares 1m com OHLC exatamente idêntico (denominador) | 13.388 |
| Divergência de `quote_volume` por exatamente `0,0001` | 261 |
| Taxa | 1,9494% |
| Divergência de `volume` por exatamente `0,0001` | 0 |

Exemplo literal: `LINK_USDT/2026-09-02T15:44:00Z`, canônico `quote_volume=6349.6377`,
shadow (v3) `quote_volume=6349.6378`.

**Veredito: sim, o `ohlcv` canônico sofre do mesmo erro de escala do v1.**

## Classificação — por que não é urgência isolada

- Magnitude: **1,9494%** dos pares OHLC-idênticos comparados (261/13.388), e
  restrito a `quote_volume` (não a `volume`, não a preço).
- Nenhum consumidor de decisão (score, indicador, barreira) usa `quote_volume`
  hoje — impacto direto em resultado de trade não identificado nesta
  verificação [inferência, não confirmada por grep exaustivo de consumidores].
- O vetor de contaminação **dominante** e já quantificado é o candle-aberto
  (F3 do levantamento de 2026-09-01: `1m` diverge `19,432314%` no canônico
  contra a Gate final), ordens de magnitude maior que este erro de escala.

## Ação proposta — sem execução

Corrigir junto com o corte do coletor antigo (aposentadoria de `ohlcv`
canônico em favor do caminho v3, quando o portão de `72h` fechar), aplicando a
mesma normalização decimal usada em `ohlcv_shadow`. **Não abrir frente de
correção isolada agora** — a magnitude não justifica desviar esforço do
bloqueio principal (F3/portão de corte).

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL |
|---|---|---|
| pares OHLC-idênticos 1m | [query] | `n=13388` |
| divergência quote_volume=0.0001 | [query] | `n=261; taxa=261/13388=1.9494%` |
| divergência volume=0.0001 | [query] | `n=0` |
| exemplo | [query] | `LINK_USDT/2026-09-02T15:44:00Z; canon_qv=6349.6377; shadow_qv=6349.6378` |
