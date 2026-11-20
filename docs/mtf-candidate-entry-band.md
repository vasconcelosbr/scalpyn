# Faixa candidata de entrada

O Gráfico MTF apresenta uma referência visual do suporte selecionado pela avaliação confirmada. A faixa não é uma ordem, uma promessa de entrada ideal ou um novo filtro de negociação.

Configuração: **Gráfico MTF → Configurar análise MTF → Avançado → Faixa candidata de entrada → Exibir faixa candidata de entrada**. O controle persiste em `presentation.entry_band_enabled`; configurações antigas continuam válidas. A proximidade usa o campo existente `zone_proximity_atr`, editável em ARMADO, sem criar outro limite operacional.

O limite inferior é `analysis.zone_1h.low`. O superior é `analysis.zone_1h.high + zone_proximity_atr × ATR do 1H`. Não existe extensão inferior: o motor só seleciona suportes cujo limite inferior não está acima do fechamento de referência. A faixa usa o suporte da avaliação, mesmo quando o gráfico exibe outro período. O botão **Enquadrar faixa** ajusta somente a escala visual.

- Âmbar: aguarda confirmação.
- Vermelho: avaliação bloqueada pelos vetos existentes.
- Verde: gatilho confirmado, com uma linha para a entrada do plano Shadow.
- Indisponível: sem suporte, dados vencidos, contexto divergente ou plano expirado/incompatível.

Os limites não são arredondados no cálculo; a apresentação segue a precisão do par. A faixa é uma guia da avaliação atual estendida pelo painel, não um sinal histórico. Ao vencer sua validade, o navegador remove o desenho mesmo sem receber outra resposta. A validade usa a configuração existente de frescor e, quando aplicável, a expiração do plano.

O serviço acrescenta `analysis.entry_candidate` depois da avaliação e dos diagnósticos. A configuração de apresentação fica fora de `engine_dump`; score, vetos, identidade da estratégia e simulações permanecem intactos. Os testes exercitam essa igualdade, os estados, o vencimento e o salvamento visual com ATR herdado.

Para ativar em configuração persistida, publique primeiro API e os consumidores do schema MTF (structural_compute e MTF history), além do frontend. Só depois salve o novo campo pela API de configuração. Compare os documentos antes/depois removendo apenas esse campo; todas as demais configurações devem permanecer idênticas.
