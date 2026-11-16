# Default timeframe e candles H4

Selecionar o Default Timeframe atualiza os indicadores de candles já inseridos em filtros, sinais, regras de score, bloqueios e gatilhos, incluindo operandos de comparações. Períodos de cálculo, limiares, pesos, provedores e políticas de validade são preservados. Novas condições herdam o profile antes do timeframe da política global de preenchimento.

Capitalização, preço atual, volume de ticker, funding, livro de ofertas, scores compostos e janelas fixas de fluxo/referência não recebem um timeframe de candle. Comparações que declaram explicitamente preço de candle como OHLCV preservam essa semântica.

H4 é aceito no editor, importação, validação e cache do ProfileEngine. O caminho comum de inputs carrega as identidades de candles declaradas em todas as seções antes da filtragem, score e refresh. H4 spot calcula sob demanda a partir de candles fechados e contíguos da Gate, com a configuração de indicadores do dono do profile, e cache separado por usuário, configuração e candle. Candles abertos, lacunas, séries antigas e fallback de outra corretora não são usados nesse produtor. Nenhuma ordem é criada pelos testes ou pelo produtor. Futures continua dependendo da sua série própria, sem substituir dados por candles spot.

## Evidência antes da publicação

- [comando] `npm run build`: `Compiled successfully`; TypeScript concluído.
- [comando] testes frontend direcionados: `tests 52`, `pass 52`, `fail 0`.
- [comando] testes backend direcionados: `107 passed in 3.86s`.
- Replay sintético H4 do gatilho obrigatório `h4-rsi-required`: configuração `{"operator": ">", "value": 50, "timeframe": "4h"}`; entrada `49` resulta `STRATEGY_BLOCK`/`FAIL`; entrada `51` resulta `ALLOW`/`PASS`. O snapshot mantém o timeframe. Nenhuma chamada de ordem.
- [query] runtime de produção antes da publicação: `{"active_l3_watchlists": 1, "trigger_mismatches": [], "orders_blocked": true}`.
- [query] experimento do produtor em processo efêmero, com a configuração real da conta: `{"symbol":"BTC_USDT","candidate_count":70,"timeframes":["4h"],"producers":["profile_closed_gate_4h_v1"]}`. Isto comprova a consulta/calculadora, não um deploy.
- Teste anterior `test_block_engine_emits_condition_level_audit_for_virtual` possui expectativa sem o campo `timeframe`, embora o BlockEngine da main já o emita. Falha reproduzida com o módulo obtido de `origin/main`; não foi alterada a expectativa para esconder essa divergência.

| Valor reportado | Origem | Literal |
|---|---|---|
| Resultado frontend | comando de teste | `tests 52`, `pass 52`, `fail 0` |
| Resultado backend | comando de teste | `107 passed in 3.86s` |
| Replay | fixture e assertions | `49`, `51`, `value: 50`, `FAIL`, `PASS` |
| Paridade L3 | query + merge real | `active_l3_watchlists: 1`, `trigger_mismatches: []` |
| Série real | produtor efêmero | `candidate_count: 70`, `timeframes: ["4h"]` |

Evidências de deploy e interface devem ser registradas no ledger de publicação somente após confirmação.
