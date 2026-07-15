# Reconciliação Performance Institucional × Gate

## Resumo técnico

A divergência tinha duas causas independentes. O contador usava o grão de fill
(`trade_id`), enquanto o Histórico de ordens da Gate usa o grão de ordem
(`order_id`). Além disso, o FIFO tratava taxas cobradas no ativo-base como se o
valor nominal já estivesse em USDT.

## Evidência de produção

Janela: dia civil de 12/07/2026 em `America/Sao_Paulo`.

| Verificação | Resultado literal |
|---|---:|
| Fills em `exchange_executions` | 11 |
| Ordens únicas | 10 |
| Ordem dividida em múltiplos fills | `1098328423263` |
| Fills dessa ordem | `25239152`, `25239153` |
| P&L antes da correção | -0.03436994 USDT |
| Taxas antes da correção | 0.32111034 USDT |
| P&L após a correção | 0.21484245 USDT |
| Taxas após a correção | 0.07189795 USDT |
| ROI realizado após a correção | 0.58477394060592526500% |

## Correção

- O resumo conta `COUNT(DISTINCT COALESCE(NULLIF(order_id, ''), trade_id))`.
- Taxa em moeda de cotação permanece inalterada.
- Taxa em ativo-base é convertida por `fee × fill.price`.
- Moeda de taxa desconhecida não é silenciosamente tratada como USDT.
- `position_lifecycle` foi reconstruída a partir dos fills brutos preservados.

## Alinhamento patrimonial com a Gate

`invested_usdt` é giro acumulado dos lotes fechados, não capital único da
conta. O saldo é reutilizado após cada venda, portanto somar o capital aplicado
em todos os fechamentos produz um valor maior que o patrimônio disponível.

O PnL diário da Gate foi reproduzido como variação patrimonial: patrimônio
atual direto de `/wallet/total_balance` menos patrimônio no início do dia. O
baseline foi reconstruído revertendo os fills do dia sobre os saldos atuais e
marcando o inventário inicial pelo preço de abertura da primeira vela horária
do dia em `America/Sao_Paulo`.

| Métrica | Resultado literal |
|---|---:|
| Giro executado | 36.73940220 USDT |
| Patrimônio inicial reconstruído | 9.485723577215001 USDT |
| Patrimônio atual Gate | 9.63230141 USDT |
| PnL patrimonial | 0.14657783278499892 USDT |
| Retorno patrimonial | 1.5452467235824094% |

A interface passou a nomear os valores explicitamente como `Giro executado
(período)`, `PnL de hoje na Gate` e `Valor ganho hoje na Gate`.

## Limitações

O lucro é P&L realizado FIFO líquido. Vendas podem consumir inventário comprado
em dias anteriores, portanto o valor representa o resultado realizado no dia da
venda, não apenas pares cuja compra e venda ocorreram no mesmo dia.

## Verificação

- Testes unitários de conversão de taxa: aprovados.
- Railway deployment: `4e1247eb-9642-4ac5-ae1c-b6837da39d90`, `SUCCESS`.
- Vercel deployment: `dpl_5AwuQV45irx2oE4i7obSmjFvLdvF`, `READY`.
- Página pública: HTTP `200`.
- API sem credencial: HTTP `401`, comportamento esperado.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| fills=11 | [query: exchange_executions] | `(11, 10)` |
| ordens=10 | [query: order_id distinto] | `(11, 10)` |
| P&L corrigido=0.21484245 | [query: position_lifecycle] | `Decimal('0.21484245')` |
| taxas corrigidas=0.07189795 | [query: position_lifecycle] | `Decimal('0.07189795')` |
| ROI=0.58477394060592526500% | [calc: pnl/invested×100] | `0.21484245 / 36.73940220 × 100` |
