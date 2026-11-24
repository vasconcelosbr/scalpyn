# MTF: contrato JSON completo

O editor de `/grafico-mtf` exporta um contrato `mtf_chart_v3` exclusivo do usuário. Abrir o editor converte a configuração anterior em memória; somente **Salvar configuração** persiste a conversão. Indicadores globais, perfis de trading e configurações de outros usuários não são alterados.

`GET /api/mtf/bootstrap` entrega a configuração salva, `effective_config` (rascunho completo), `effective_preset`, o JSON Schema e o catálogo de produtores suportados. Uma conversão incompleta aparece como erro e preserva a configuração anterior. `POST /api/mtf/config/validate` valida sem gravar; `PUT /api/mtf/config` valida o mesmo contrato e a propriedade das watchlists antes de usar o serviço de configuração e sua invalidação de cache.

## Estrutura

- `timeframes.structure`, `timeframes.zone`, `timeframes.trigger`: papéis das etapas.
- `indicators`: instâncias identificadas por nomes exclusivos. Cada instância declara `family`, `output`, `parameters`, `timeframe`, `enabled`, `display` e `sample_window`.
- `frame_atr`: seleciona a instância ATR usada na geometria de cada timeframe.
- `rules.armed` e `rules.trigger`: grupos recursivos com `logic: "AND"` ou `"OR"` e `conditions`.
- `rules.vetoes`: condições ou grupos que vetam quando verdadeiros.
- `rules.scoring`: grupos com `id`, `points` e `when`. Seus pontos se somam aos pesos existentes.
- `veto_filters`, `score_weights`, padrões, zonas, custos, capital, risco e demais campos anteriores continuam efetivos e editáveis. Para substituir uma contribuição nativa por uma regra, desative seu veto ou zere seu peso explicitamente.
- `pump_flow`: fonte de fluxo real. Continua sendo contexto quando nenhuma regra a referencia; referências podem participar de ARMADO, GATILHO, veto ou score. `asset_window` escolhe a janela do ativo; as chaves de `capital_neutral_ratio` e `require_capital` definem as janelas de capital.
- `presentation` e `structure_ema_period`: apresentação. Não mudam a identidade das decisões.

O catálogo da própria tela enumera timeframes, saídas, parâmetros e referências aceitos. Adicionar um timeframe exige entradas correspondentes em `candle_limits`, `presentation.visible_candles`, `frame_atr` e uma instância ATR habilitada. As janelas antigas permanecem disponíveis para compatibilidade dos consumidores. O timeframe de um indicador pode diferir do timeframe da etapa que o utiliza.

## Condições

Uma condição contém `id`, `label`, `enabled`, `left`, `operator` e `right`. Cada operando possui **apenas** `ref` ou `value`.

Exemplo sem inventar um limiar de estratégia: comparar duas instâncias de média já declaradas no documento:

```json
{
  "id": "medias_alinhadas",
  "label": "Média rápida acima da lenta",
  "enabled": true,
  "left": {"ref": "indicators.media_rapida"},
  "operator": ">",
  "right": {"ref": "indicators.media_lenta"}
}
```

Os nomes de instância são definidos pelo usuário; referências inexistentes ou desabilitadas são rejeitadas. Operadores numéricos exigem operandos numéricos. `between` usa um par ordenado em `right.value`; cruzamentos exigem uma série no operando esquerdo e um número ou outra série no direito.

Uma regra só usa candles fechados disponíveis no instante da decisão. Cruzamentos alinham ambos os operandos aos fechamentos atual e anterior da série esquerda; não consultam valores futuros. Janelas com lacunas ou sem aquecimento suficiente ficam indisponíveis. Dados indisponíveis não aprovam grupos, inclusive OU, nem são substituídos por zero. Um veto com dados ausentes também bloqueia a avaliação com motivo explícito.

## Compatibilidade e diagnóstico

A conversão congela os parâmetros herdados e a janela de cálculo efetiva de cada produtor, mantendo a regra anterior de proximidade para ARMADO e padrão de candle para GATILHO. Não salva nem recalibra automaticamente configurações existentes. Versões antigas continuam usando o motor e as identidades legadas.

O diagnóstico mostra o resultado obrigatório de cada grupo e, separadamente, as condições individuais. Assim, uma alternativa falsa de OU não aparece como bloqueio se outra alternativa aprovou o grupo. Indicadores e fluxo registram valores e configuração no snapshot. Mudanças nas regras invalidam leituras anteriores; históricos e simulações existentes usam a configuração congelada na entrada.

A validade de avaliações, o relógio das simulações e a identificação do timeframe nos relatórios seguem o gatilho configurado. O estudo de retornos futuros do Histórico mantém sua resolução independente de amostragem; isso não altera o timeframe da estratégia.

O contrato é observacional. ARMADO/GATILHO não concedem autorização para enviar ordens reais. A separação entre bloqueio de edição e proteção de ordens continua preservada.

## Verificação

`backend/tests/test_mtf_complete_json.py` cobre conversão, regras efetivas, vetos, pontos, fluxo parcial, alinhamento temporal, parâmetros próprios, relógio da simulação e validação sem escrita. As suítes existentes de MTF verificam compatibilidade com configurações anteriores. O frontend valida o schema publicado e o servidor é a referência final para referências e relações entre campos.
