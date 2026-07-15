# Prompt — Profile Intelligence: análise técnica, derivativos e contexto macro

## Papel

Você é um engenheiro sênior responsável pelo Scalpyn, uma plataforma institucional de trading cripto. Implemente no módulo **Profile Intelligence** a capacidade de analisar um ativo combinando estrutura técnica spot, posicionamento em derivativos, order flow e contexto macro.

Trabalhe sobre o repositório existente. Antes de alterar código:

- Leia o `AGENTS.md` do projeto.
- Consulte o grafo em `graphify-out/graph.json` com `graphify query`.
- Inspecione os serviços, modelos, migrations, APIs e componentes existentes.
- Preserve alterações não relacionadas presentes no workspace.
- Não invente dados, thresholds ou contratos de API.

## Objetivo

Permitir análises como:

> O ativo permanece em tendência de baixa, negocia abaixo das médias estruturais, apresenta momentum neutro ou fraco, concentração excessiva de participantes em uma direção, fluxo agressor contrário e ambiente macro desfavorável.

O sistema deve transformar essa leitura em:

- Evidência quantitativa auditável.
- Classificação de regime.
- Signals.
- Block Rules.
- Scoring Rules.
- Recomendações de profile.
- Explicação textual derivada exclusivamente dos dados calculados.

## Fontes obrigatórias

### Mercado spot

- Preço atual.
- OHLCV com candles fechados.
- Variação de preço em múltiplas janelas.
- RSI.
- EMA estrutural configurável, incluindo EMA120 e EMA200.
- Distância percentual entre preço e médias.

### Derivativos

- Funding rate atual e histórico.
- Long/short ratio.
- Open interest e sua variação.
- Taker buy volume.
- Taker sell volume.
- Taker ratio canônico, calculado como buy volume dividido pelo volume total.
- Liquidações long e short, quando disponíveis.

### Contexto macro

- Fear & Greed Index.
- Regime macro já disponível no Scalpyn.
- BTC dominance e contexto de mercado, quando existentes no pipeline atual.

## Requisitos de ingestão

- Reutilize Gate.io REST e WebSocket já existentes.
- Não use candle como fallback para order flow real.
- Colete e persista `contract_stats`, incluindo long/short ratio e open interest.
- Persistir funding, macro e order flow com timestamp de origem.
- Cada valor deve carregar fonte, horário, idade, status e confiança.
- Dados ausentes ou stale devem resultar em `NO_DATA`; nunca em zero fictício.
- Não misture valores capturados depois do momento da decisão.
- Use joins temporais do tipo *as-of* para reconstrução histórica.

## Features esperadas

O snapshot operacional deve poder representar, no mínimo:

```text
price_change_24h
price_change_7d
rsi
ema120
ema200
distance_to_ema120_pct
distance_to_ema200_pct
funding_rate
funding_rate_change
long_short_ratio
long_short_ratio_change
open_interest_usdt
open_interest_change_pct
taker_buy_volume
taker_sell_volume
taker_ratio
liquidations_long_usdt
liquidations_short_usdt
fear_greed_index
btc_dominance
market_regime
```

Os nomes finais devem seguir as convenções existentes do projeto. Não crie aliases concorrentes sem necessidade.

## Profile Intelligence

### Buckets

- Tornar todos os buckets configuráveis em JSONB.
- Proibir ranges sobrepostos ou com gaps involuntários.
- Criar buckets para tendência estrutural, crowding, funding, order flow e macro.
- Registrar missingness por indicador.
- Persistir a configuração efetiva e seu hash em cada run.

### Análise estatística

- Calcular resultados por profile, versão, direção, timeframe, liquidez e regime.
- Separar discovery, validation e holdout temporal.
- Usar baseline da mesma janela e população.
- Reportar suporte, intervalos de confiança e degradação temporal.
- Aplicar correção para múltiplas hipóteses.
- Não classificar combinação sem validation real.
- Nunca preencher ausência de validation com zero ou com métricas de discovery.

### Classificação operacional

O motor deve distinguir:

- Tendência estrutural favorável, neutra ou desfavorável.
- Momentum de recuperação, equilíbrio ou deterioração.
- Crowding long, balanceado ou short.
- Domínio comprador, neutro ou vendedor.
- Regime macro risk-on, neutro ou risk-off.

Essas classificações devem ser explicáveis por regras e valores observados.

## Geração de configuração de profile

### Signals

Use Signals para confirmações obrigatórias, por exemplo:

- Recuperação de média estrutural.
- Melhora de momentum.
- Retorno do fluxo comprador.
- Redução de crowding.
- Confirmação de open interest.

### Block Rules

Use Block Rules quando uma condição isolada ou uma confluência tornar a entrada insegura, como:

- Tendência estrutural contrária.
- Crowding severo contra a operação.
- Order flow contrário.
- Funding extremo.
- Regime macro incompatível.
- Dados críticos ausentes ou stale.

### Scoring

- Usar somente Scoring Rules master existentes ou criadas de forma auditada.
- Aplicar pontos e penalidades configuráveis.
- Nenhuma operação pode ser liberada sem atingir o score mínimo configurado.
- A recomendação deve explicar por que uma condição virou Signal, Block Rule ou Scoring Rule.

## API

Criar ou ampliar endpoints para:

- Consultar o contexto híbrido atual de um símbolo.
- Consultar histórico das features.
- Executar análise no Profile Intelligence.
- Retornar evidências, freshness e proveniência.
- Recomendar profiles compatíveis.
- Gerar preview da alteração.
- Criar challenger `SHADOW_ONLY`.
- Aplicar alteração versionada após confirmação humana.
- Fazer rollback.

Use schemas Pydantic explícitos. Valide UUIDs, símbolos, períodos e limites. Não exponha exceções internas ao cliente.

## Interface

Na rota `/profile-intelligence`:

- Adicionar uma visão de contexto de mercado por ativo.
- Exibir separadamente Técnico, Derivativos, Order Flow e Macro.
- Mostrar valor, direção, freshness, fonte e confiança.
- Destacar confluências e contradições.
- Mostrar o raciocínio que produziu cada recomendação.
- Permitir selecionar um profile existente ou criar challenger.
- Exibir diff antes/depois.
- Manter live trading desativado.
- Diferenciar `NO_DATA`, erro de API e ausência histórica.
- Uma linha inválida não pode derrubar a página.

Mantenha o padrão visual dark luxury fintech já existente. Priorize densidade informacional, legibilidade e navegação por teclado.

## Segurança e auditoria

- Toda criação deve nascer como `SHADOW_ONLY`.
- Dry-run deve ser rigorosamente read-only.
- Toda alteração deve criar nova versão e audit log.
- Registrar dataset, janela, configuração, código, run e usuário responsável.
- Impedir features pós-entrada em Signals, Block Rules ou Scoring.
- Não gravar secrets ou tokens nos logs.
- Implementar idempotência e proteção contra concorrência.

## Testes obrigatórios

### Backend

- EMA120 e EMA200 calculadas apenas com histórico suficiente.
- Long/short ratio coletado e persistido corretamente.
- Funding e macro reconstruídos por timestamp.
- Taker ratio utiliza trades reais.
- Missing/stale não vira zero.
- Splits temporais não apresentam leakage.
- Recomendação gera Signals, Blocks e Scoring válidos.
- Dry-run não escreve.
- Challenger nunca habilita live trading.
- Multi-tenancy em todos os endpoints.

### Frontend

- Renderização dos quatro blocos de contexto.
- Estados loading, erro, `NO_DATA` e stale.
- Preview de diff.
- Confirmação humana.
- Criação de challenger.
- Payload incompleto não causa exceção client-side.

### Integração

Validar o fluxo:

```text
coleta
→ snapshot temporal
→ classificação de contexto
→ análise estatística
→ recomendação
→ preview
→ challenger SHADOW_ONLY
→ acompanhamento
→ promoção ou rollback
```

## Restrições

- ZERO HARDCODE: thresholds e períodos devem vir de configuração persistida e editável.
- Não alterar módulos não relacionados.
- Não criar fallback silencioso.
- Não produzir métricas sem evidência.
- Não usar IA como fonte de números.
- Não ativar live trading.
- Não considerar concluído enquanto testes e validações não passarem.

## Entrega

Ao finalizar, apresente:

- Arquivos alterados.
- Migrations criadas.
- Endpoints adicionados ou modificados.
- Configurações JSONB introduzidas.
- Testes executados e outputs literais.
- Limitações restantes.
- Checklist requisito por requisito.
- Ledger de Evidências para todo número reportado.
- Atualização do Graphify com `graphify update .`.
