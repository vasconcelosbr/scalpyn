# Correção do preço de entrada do Shadow

## Causa confirmada

O construtor de assets aplicava os indicadores depois do preço de metadata.
Um indicador chamado `price`, calculado a partir de candle, sobrescrevia a
cotação; `_price_source_at` continuava sendo o horário de metadata. O Shadow
recebia uma identidade inconsistente de preço e horário.

## Correção restrita ao preço

- Preservar o preço de metadata no asset, mantendo o preço analítico dentro de
  `indicators`. Identificar o envelope da decisão como referência, pois o refresh
  de metadata não prova o horário de um evento de mercado.
- Registrar novas entradas de Shadows canônicos L3 spot com uma estimativa de
  compra pelo livro da Gate, considerando o orçamento configurado da simulação
  e os níveis de venda necessários para cobri-lo integralmente.
- Congelar o preço observado, fonte, par, lado, orçamento, quantidade estimada,
  níveis consumidos e horários da exchange e da requisição local. O preço da
  decisão permanece separado como referência; preço realizado permanece ausente,
  porque Shadow é simulação.
- Usar o horário da captura, sem retroagir ao instante da decisão. A idade da
  evidência combina o intervalo `current - update` da Gate com o tempo decorrido
  desde o início da requisição. Isso inclui a latência conservadoramente e não
  depende de sincronização absoluta entre os relógios. Cache preserva os horários
  originais. Revalidar a idade antes de inserir o Shadow.
- Não registrar uma entrada com livro ausente, inválido, cruzado, antigo ou sem
  profundidade suficiente; não substituir evidência indisponível por candle.
  O limite de idade continua vindo de `shadow_entry_max_lag_seconds`, já
  governado pela configuração.

## Escopo preservado

A API do L3 Consolidado, sua visibilidade, autorização, expiração e consumidores
externos conservam o comportamento anterior. Esta correção não exige mudanças no
robô externo. Não altera filtros, thresholds, políticas de saída ou ordens reais.
Também não reescreve preços ou resultados históricos. Outras origens diagnósticas
e futuros mantêm seus contratos existentes.

O preço calculado representa a estimativa executável da simulação no momento da
captura. Não promete igualdade com uma ordem real de outro tamanho ou enviada
em outro instante. Fechamento do Shadow continua sendo resultado simulado.

## Validação

Os testes reproduzem a colisão de preço do caso UNI e cobrem estimativa multinível,
profundidade insuficiente, livro inválido ou antigo, cache, diferença entre
relógios, requisição lenta, vencimento da cotação antes da inserção e ausência de
retroação do horário de entrada. Uma consulta pública de leitura à Gate verificou
o contrato com dados reais, sem enviar ordens.

Na publicação, conferir o SHA dos workers que criam Shadows, saúde e logs.
Novas linhas canônicas devem conter `entry_price_contract_version` igual a
`gate_spot_entry_quote_v1`, o snapshot `entry_quote`, e `entry_price` igual ao
valor da estimativa. Se não houver novo sinal autorizado, registrar ausência de
amostra; não criar trades ou relaxar filtros para validar.

Referência de protocolo: [Gate API oficial](https://www.gate.com/docs/developers/apiv4/en/).
