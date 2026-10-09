# Integridade de preço do Shadow e execução externa

## Problema confirmado

O construtor de assets de `pipeline_scan` aplicava os indicadores depois do
preço de metadata. Um indicador chamado `price`, calculado a partir de candle,
sobrescrevia a cotação; `_price_source_at` continuava sendo o horário de metadata.
O consumidor de Shadow recebia uma identidade inconsistente de preço e horário.
Além disso, o refresh de metadata não prova o horário de um evento de mercado.

O teste de regressão reproduz a colisão encontrada no caso UNI. O JSON de Shadow
fornecido e a ordem real também não devem ser associados apenas pelo símbolo:
o executor é externo e não registrou esse vínculo nas tabelas do Scalpyn.

## Comportamento corrigido

- `indicators.price` continua disponível como indicador; não sobrescreve o preço
  de metadata no asset. `price_envelope` identifica esse preço como referência,
  com relógio de refresh de metadata, sem atribuir qualidade de execução.
- Novos Shadows canônicos de origem `L3` estimam a compra usando o livro spot da
  Gate e o valor configurado da simulação. Consomem os níveis de venda necessários
  para o orçamento completo. Livro ausente, inválido, cruzado, antigo ou sem
  profundidade suficiente impede a criação; não há fallback para candle.
- O horário de entrada é o da captura, sem retroagir à decisão. Preço de
  referência, estimativa observada e preço realizado são campos distintos;
  realizado permanece ausente porque o Shadow não envia ordens.
- `entry_quote` congela fonte, par, lado, orçamento, quantidade estimada, níveis
  consumidos, relógios da exchange, início/fim da requisição e idade da evidência.
  A idade combina o intervalo entre `current` e `update` da Gate com todo o tempo
  decorrido desde o início da requisição local. Isso inclui a latência de forma
  conservadora e evita depender da sincronização absoluta entre os relógios.
  Um cache hit preserva os horários originais.
- O limite de idade continua vindo de `shadow_entry_max_lag_seconds`, já
  governado pela configuração. Nenhum limiar de estratégia foi alterado.
  Imediatamente antes da inserção são revalidadas a idade e a autorização.
- `/api/watchlists/l3-consolidated/assets` passa a retornar, por padrão, somente
  candidatos atuais com `executable=true` e `expires_at` futuro. O piso de
  visibilidade histórica não alimenta esse fluxo. A opção
  `include_non_executable=true` destina-se a exibição; linhas expiradas têm
  `executable=false`, mesmo quando mantidas visíveis.
- `execution_context` declara a relação entre decisão e Shadow. Um Shadow que
  cobre uma posição ativa pode pertencer a uma decisão anterior. Registros
  antigos sem `entry_quote` aparecem como `LEGACY_UNVERIFIED` nessa projeção.

O contrato aplica-se a novas entradas canônicas L3. Shadows diagnósticos de outras
origens e posições históricas mantêm seus contratos existentes. Não há migração,
reescrita de P&L histórico nem recálculo silencioso de labels de treinamento.

## Alteração necessária no executor externo

O endpoint não é uma fila de ordens. A presença de um símbolo não autoriza uma
compra por si só. O executor deve:

- Ler o fluxo padrão, validar `executable` e `expires_at` usando relógio UTC
  sincronizado e considerar a latência da requisição.
- Persistir a intenção e reservar de forma atômica `authorization_id` antes de
  enviar a ordem, para que polling, concorrência e retries não dupliquem compras.
  Se houver timeout após envio, reconciliar a ordem na Gate antes de reenviar.
- Consultar novamente o fluxo imediatamente antes de enviar e exigir a mesma
  autorização ainda vigente. Se ausente, alterada, expirada ou bloqueada, não
  executar essa intenção. A validação remota não é atômica com a exchange;
  registrar os horários de todas as etapas permite auditar esse intervalo.
- Buscar uma cotação nova na Gate para o tamanho REAL da ordem. Não usar
  `current_price` nem `entry_quote.value` como preço garantido. Limites adicionais
  de slippage devem vir da configuração aprovada do executor, nunca de constantes
  improvisadas. A estimativa Shadow pode usar orçamento diferente do teste real.
- Guardar `decision_id`, `authorization_id`, `shadow_id`, `shadow_decision_id`,
  `order_id`, cada fill, preço, quantidade, timestamp, taxa e moeda da taxa.
  A compra independente conserva seu preço médio e custo próprios.
- Acompanhar a posição real e executar sua política de saída. Um fechamento
  simulado não vende a posição real. Só fills de venda confirmam resultado
  realizado; enquanto aberta, a marcação depende da quantidade líquida disponível,
  preço de saída executável e custos. Não copiar o P&L do Shadow.

Essas alterações exigem o código do sistema externo. Não foram implementadas
implicitamente no Scalpyn nem enviadas ordens para a conta Gate.

## Verificação e acompanhamento

Os testes cobrem colisão de preço de candle, orçamento multinível, dados ausentes
ou inválidos, profundidade insuficiente, cache antigo, relógios independentes,
requisição lenta, expiração durante persistência, vínculo de decisão anterior
e exclusão de autorizações expiradas no fluxo padrão.

Após publicação, conferir o SHA dos serviços API e consumidores do outbox,
status terminal do deploy, saúde e logs. Consultar, somente em leitura, novas
linhas `shadow_trades.source='L3'`: `entry_price_contract_version`, `entry_quote`,
horários e relacionamento com a decisão. Se não houver novo sinal autorizado,
registrar ausência de amostra; não criar trades ou relaxar filtros para validar.

Separar as coortes antigas e novas em análises e treinamento. Uma auditoria de
contaminação histórica deve comparar os preços com evidência contemporânea antes
de decidir exclusão ou recálculo. `LEGACY_UNVERIFIED` não afirma que todo registro
antigo esteja errado. A entrada mais fiel não transforma saídas por barreiras de
candle em fills reais nem garante resultados iguais entre os dois sistemas.

Referência de protocolo: [Gate API oficial](https://www.gate.com/docs/developers/apiv4/en/).
