# MTF Capital e fluxo

Capital em janelas móveis de 4h, 1h e 5m, terminando no fechamento MTF, e fluxo individual de 5m. Contexto observacional; não altera `evaluate()`, score, vetos, plano, admissões ou ordens. O `engine_dump()` exclui `pump_flow`, preservando a identidade das decisões e o ATR herdado em edições somente de fluxo.

## Configuração

Em Configurar análise MTF → Capital e fluxo. A configuração opcional `mtf_analysis.pump_flow` é persistida pelo serviço de configuração existente. Sem configuração, a funcionalidade fica desativada. O bootstrap publica um preset explícito, editável e não calibrado. Todos os limites de confirmação, cobertura, validade e consulta ficam na configuração. Não há pesos adicionais nem soma entre janelas sobrepostas.

## Dados e momento

`pump_capital_flow_1m` fornece os agregados do universo Pump de cada minuto; a composição histórica pode variar. `flow_buckets_1m` fornece compras e vendas do ativo monitorado. Há escopo de usuário no Capital, validação do universo no Redis, corte por minuto fechado e `computed_at <= captured_at`. A captura pode ocorrer após o fechamento: não é apresentada como informação disponível retroativamente no fechamento. Janelas incompletas, último minuto ausente, snapshot antigo e ausência de negócios são explícitos. Não extrapolamos minutos ausentes.

A confirmação exige delta individual e persistência mínimos, além das janelas de Capital selecionadas com cobertura suficiente e estado neutro ou entrada. Falta de dados produz indisponível, nunca neutro. O contexto tem hash próprio e é congelado no payload das avaliações existentes, incluindo histórico e Shadow. Nenhuma migração necessária.

## Comparação

`GET /api/mtf/pump-flow/report?watchlist_id=...`, mesmo Bearer existente. Usa o último evento de cada simulação MTF, separa baseline, confirmed, not_confirmed e unavailable, por versões de configuração MTF e fluxo. Baseline e confirmed se sobrepõem: são os mesmos fills e custos, não duas carteiras independentes. Exibe N, encerradas, média líquida e ambiguidades. Dados antigos não são preenchidos retroativamente; benefício e significância não são presumidos. Resultados começam a amadurecer após novas entradas e saídas Shadow.

## Indicadores adicionais recomendados

CVD e delta por janela para confirmação de agressão; profundidade e slippage no tamanho pretendido para liquidez; progresso/eficiência versus fluxo para divergência; volume relativo para participação; extensão e pavios para exaustão. Exibir fonte, timeframe, idade e cobertura. Não duplicar os pontos de preço/volume já presentes no score MTF. Derivativos podem ser contexto separado; ML indisponível não deve gerar confirmação.
