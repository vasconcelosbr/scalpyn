# Gráfico MTF observacional

Entrada pela Watchlist, botão **Abrir Gráfico MTF** no L3 Consolidado; rota `/grafico-mtf`.
O dropdown usa as watchlists Spot pertencentes ao usuário. O Consolidado continua usando sua autorização canônica. Uma lista vazia permanece vazia. A seleção de outras listas não promove ativos para L3.

## Contrato e isolamento

Dados públicos reais da Gate; cálculo determinístico; `mode=SHADOW`, `operational_effect=false`, `calibrated=false`. Nenhuma chamada de ordem, integração com treinamento, promoção de estratégia ou escrita no Shadow Portfolio existente. A seção de explicação usa templates, sem LLM.

`config_profiles.config_type=mtf_analysis` contém a configuração completa, editável na engrenagem da tela. A migração instala um preset experimental explícito; contas novas precisam salvá-lo na tela. Chaves ausentes ou configuração inválida falham fechadas. O preset integral está congelado na migração e validado contra o schema em teste PostgreSQL. Preferências usam `mtf_preferences`, com fallback local.

O profile determina apenas o timeframe inicial. Os indicadores reutilizam funções e janelas dos produtores existentes; indicadores sem histórico compatível são apresentados como indisponíveis. Indicadores de metadados não são convertidos em séries falsas de candles. O score MTF não substitui o score nem a autorização do pipeline.

## Tempo, sinais e simulação

Candles abertos são cinza e excluídos da detecção. O contexto superior é congelado no fechamento do candle de gatilho. Pivôs só existem depois da confirmação à direita. Gaps, ausência de ATR, contexto BTC indisponível e spread sem origem Gate vetam quando aplicáveis. O spread é observado no momento da coleta, não reconstruído retroativamente.

`mtf_chart_evaluations` preserva a avaliação e a configuração; identidade inclui usuário, watchlist, ativo, profile, configuração de indicadores, versão do motor e candle. Repetições não reescrevem a avaliação. `mtf_chart_shadow_events` guarda a evolução separadamente, também por inserção.

A entrada exibida é teórica no fechamento. A simulação só pode preencher na abertura de um candle posterior ao instante real de observação. Taxas e slippage são congelados por avaliação; tamanho considera o risco líquido e o limite de capital por cenário, sem simular uma carteira compartilhada. Se stop e alvo ocorrem no mesmo candle, stop prevalece e a ambiguidade é registrada. Breakeven após parcial vale a partir do próximo candle. Lacunas no caminho futuro ficam sem resultado, em vez de inventar PnL. Fechamento horário abaixo do suporte congelado invalida uma simulação aberta. Stops e perdas são exclusivamente simulados.

O relatório usa o último evento terminal com resultado conhecido de cada avaliação, agrupado por padrão, timeframe e hash de configuração. Mostra amostra, expectativa líquida, custos, ambiguidades e intervalo aproximado apenas acima do mínimo configurado. Observações não são necessariamente independentes; o relatório é descritivo e não calibra nem autoriza execução.

## Operação

O worker consome `app.tasks.mtf_chart.observe` na fila estrutural de cálculo. O agendador restrito `python -m scripts.run_observation_beat` preserva a lista observacional de produção e acrescenta somente o novo observador. Apenas listas explicitamente monitoradas têm avaliações persistidas; selecionar uma lista permite prévia, e **Ativar observação Shadow** adiciona essa lista à configuração isolada.

Desativar `enabled` interrompe novas avaliações, mantendo a conclusão das simulações abertas. Downgrade da migração preserva observações. O rollback de interface não apaga dados.

## Reutilização

- `frontend/components/shadow-portfolio/TradeCandlestickChart.tsx:52`: renderer existente, biblioteca e cores; extensão `MTFCandlestickChart` na linha 215.
- `backend/app/services/feature_engine.py:12`: funções canônicas de indicadores.
- `backend/app/tasks/compute_indicators.py:1012`: warmup e janela estrutural compartilhada.
- `backend/app/tasks/compute_mtf_indicators.py:27`: janela canônica do produtor horário.
- `backend/app/services/market_data_service.py:424`: parser/cliente Gate; orderbook na linha 597; opção Gate-only preserva o comportamento de outros consumidores.
- `backend/app/api/watchlists.py:1189`: associação canônica e Consolidado, somente leitura.
- `backend/app/services/config_service.py:98`: persistência, histórico e invalidação de cache das configurações.
- `backend/app/tasks/celery_app.py`: roteamento e limites existentes.

## Validação reproduzível

```powershell
cd backend
$env:MTF_TEST_DATABASE_URL='postgresql+psycopg2://mtf_test@127.0.0.1:55439/mtf_chart_test'
python -m pytest tests/test_mtf_chart_engine.py tests/test_mtf_chart_api.py tests/test_mtf_chart_postgres.py tests/test_profile_default_timeframe.py tests/test_celery_routing_invariants.py -q
python -m alembic heads
python -m scripts.verify_mtf_chart_market_parity --inputs ../.env.mtf-inputs.json --output ../docs/evidence/mtf-market-parity.json
cd ../frontend
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js app/grafico-mtf/page.tsx components/mtf/*.ts components/shadow-portfolio/TradeCandlestickChart.tsx
node node_modules/next/dist/bin/next build
```

Os testes PostgreSQL exigem banco explicitamente denominado `mtf_chart_test` e criam schemas descartáveis. Nunca apontar para produção. A conferência de mercado compara entradas Gate fechadas idênticas e janelas canônicas, não snapshots do pipeline capturados em instantes diferentes. Evidência literal em `evidence/mtf-market-parity.json`.

Limitação preexistente: `test_profile_runtime_config.py::test_block_engine_emits_condition_level_audit_for_virtual` espera um dicionário sem `timeframe`; a implementação canônica já devolve esse campo. Esta entrega não altera o BlockEngine.
