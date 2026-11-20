# Estrutura, estado e conflito MTF

## Escopo

Apresentação e telemetria aditiva de leitura. O motor `mtf_chart_engine.py`, classificadores, score, vetos, gatilho, proximidade, configurações persistidas e serviços Shadow não foram alterados. Os novos campos são acrescentados nos handlers GET, depois do cálculo canônico, fora dos observadores e da persistência de decisões.

## Mapeamento anterior à edição

Fonte: commit `4b6c2d84cec19e2c09af4300c77b7136ff45aa57`.

| Elemento | Fonte anterior |
|---|---|
| Cabeçalho, lista, leitura multiframe | `frontend/app/grafico-mtf/page.tsx:211`, `:224`, `:248` |
| Plano simulado | `frontend/components/mtf/MTFEntryContext.tsx:7` |
| Pivôs, preço de HL/LH e confirmação | `frames[tf].pivots`; schema `frontend/components/mtf/types.ts:6`; cálculo `backend/app/services/mtf_chart_engine.py:51` |
| Estado estrutural | `analysis.context_4h.trend`; H1/M5 podem usar o mesmo classificador `backend/app/services/mtf_chart_engine.py:68` |
| EMA, ATR e fechamento | `frames[tf].indicators`, `frames[tf].atr`, `frames[tf].candles`; `backend/app/services/mtf_chart_service.py:248` |
| Candle em formação | `frames[tf].candles[].closed`; `backend/app/services/mtf_chart_service.py:263` |
| Capital | `analysis.pump_flow.capital[tf].state/ratio`; `frontend/components/mtf/MTFPumpFlow.tsx:31` |
| Lista Consolidado | Avaliação validada de `mtf_history_observations`; ainda sem frames completos no contrato da lista |

Os payloads e a captura anterior do EVAA estão no pacote local de evidências. Na carga inicial observada, não ocorreu remoção falsa; isso não demonstra a causa exata do evento histórico relatado pelo usuário.

## Contrato aditivo

Schema completo: `docs/mtf-structure-context.schema.json`; implementação tipada: `backend/app/schemas/mtf_structure.py`.

- `GET /api/mtf/chart`: novo `structure_context` na raiz, preservando integralmente `analysis`.
- `GET /api/mtf/watchlist`: no Consolidado, novo `items[].structure_context` proveniente do ID exato da observação já validada. O SELECT é limitado por usuário, watchlist e IDs. Sem recaptura, gravação ou alteração do loader compartilhado de L3.
- `structure_context`: origem, instante, validade, identidade, frames estruturais, preço de referência, pivô de invalidação, distância em ATR e divergências categóricas.
- Dados ausentes/vencidos retornam `null`, nunca valores estimados. O cliente também invalida a telemetria vencida ou incompatível com a avaliação.

Fórmulas: idade = piso do tempo entre confirmação e leitura dividido pela duração do timeframe; extensão = (fechamento fechado − EMA50 do mesmo candle) / ATR; distância estrutural = (preço de referência − nível do pivô) / ATR; variação em formação = (close / open − unidade) em percentual. O preço de referência do gráfico é o fechamento mais recente disponível do candle M5, identificado no payload como `latest_5m_candle_close`.

Sem faixas de classificação configuradas: o valor de extensão não recebe normal/esticada/extrema. Os números usados para duração, conversão percentual, validação positiva e formatação não são limites de negociação. Nenhum threshold foi acrescentado.

Limitação comprovada: o snapshot de observação do EVAA consultado não contém EMA50; a lista apresenta extensão indisponível. O gráfico possui a série configurada e pode exibir a extensão. Outros perfis podem ter snapshots diferentes. A telemetria não mistura fontes incompatíveis para preencher o campo.

## Configuração proposta, sem valores ou gravação

Nome sugerido: `config_profiles(config_type="mtf_analysis").config_json.presentation.structure_extension_bands`.

Estrutura proposta, ainda não implementada ou persistida:

```typescript
Array<{
  label: "normal" | "esticada" | "extrema";
  min_abs_atr: number | null;
  max_abs_atr: number | null;
}>
```

A escolha dos limites, inclusividade e eventual uso de extensão absoluta deve ser aprovada pelo usuário antes de implementar classificação. A tela atual não depende dessa chave.

## Carregamento

O fluxo anterior fazia `setMembership('removed')` tanto a partir de uma resposta independente da lista (arquivo anterior `page.tsx:109`) quanto imediatamente após o erro de remoção do gráfico (`page.tsx:160`). O backend verifica associação antes e depois de calcular um gráfico frio (`backend/app/api/mtf_chart.py:130`); leituras em instantes diferentes podem divergir. Nenhuma dessas condições demonstrava que a remoção ainda era válida após uma resposta mais recente confirmando o ativo.

A reprodução controlada usa o payload real capturado do EVAA: gráfico retorna remoção, consulta atualizada da lista ainda contém o ativo, próxima consulta do gráfico retorna a avaliação. A UI anterior adotava o estado removido nessa ordem. A nova função `frontend/lib/mtfMembership.ts:4` reconcilia a associação antes de exibir remoção. Presença ou falha na reconciliação mantém carregamento, sem publicar avaliação antiga como atual. Ausência confirmada mantém a mensagem de remoção. Respostas de contextos cancelados são ignoradas.

O profile é resolvido no endpoint pelo membro quando não há override; não foi identificado defeito nessa resolução. O fetch não depende do timeframe selecionado, portanto a troca de timeframe não é necessária para a recuperação. Não há evidência suficiente para atribuir o incidente histórico especificamente a cache ou profile.

## Arquivos e validação

- `backend/app/api/mtf_chart.py`: acrescenta telemetria nas respostas GET.
- `backend/app/services/mtf_structure.py`: projeção pura e leitura dos snapshots validados.
- `backend/app/schemas/mtf_structure.py`: contrato de apresentação.
- `frontend/components/mtf/MTFStructurePanel.tsx`: painel completo e versão compacta; tooltip, conflito neutro e validade.
- `frontend/components/mtf/MTFEntryContext.tsx`: compõe estrutura e entrada sem alterar o status.
- `frontend/app/grafico-mtf/page.tsx`: integra os painéis e reconcilia carregamento/remoção.
- `frontend/app/grafico-mtf/style.css`: estilo responsivo.
- `frontend/components/mtf/types.ts`: tipos aditivos.
- `frontend/lib/mtfStructure.ts`, `mtfMembership.ts`: apresentação e reconciliação de leituras.
- Testes de estrutura, API, identidade, categorias, indisponibilidade, carregamento e renderização.
- `frontend/eslint.config.mjs`: corrige escopo das regras de plugins por linguagem; não altera regra de produto.
- CI e invariantes de publicação incluem a nova proteção.

Evidências locais em `C:/Users/ricar/Downloads/MTF-Structure-Semantics/`: payloads anteriores, projeções correspondentes, `evaluation-parity.json`, `local-browser-proof.json`, lint, testes e capturas antes/depois.

O teste completo do frontend revelou uma falha pré-existente em `frontend/lib/shadowRejectedConsolidation.test.ts:30`, que espera um trecho literal antigo no Shadow Portfolio. O arquivo testado e o teste não foram modificados. O blob do arquivo testado no HEAD anterior e no checkout é idêntico: `b8b7d821818bc4ffac9ce42493be9d9d3d3a085b`. Não houve alteração de Shadow para contornar o teste. Os testes MTF e o lint são registrados separadamente; não se declara a suíte global integralmente verde.
