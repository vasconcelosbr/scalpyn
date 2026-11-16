# FIX-MTF-UI-001 v2 — resultado da implementação

Implementado na branch `codex/mtf-ui-001-v2`. Documento revisado: [FIX-MTF-UI-001_v2.md](FIX-MTF-UI-001_v2.md). **Não publicado. Não houve ativação automática da nova versão de zonas, alteração de autorização L3 ou habilitação de ordens reais.**

## Escopo e evidência

- `[código]`: implementação e testes presentes nesta branch.
- `[comando]`: saída de teste, build ou medição local anexada.
- `[query]`: leitura de produção, sem escrita.
- `[replay]`: cálculo local sobre candles públicos Gate; não grava avaliação ou simulação.
- `[ABERTO]`: não comprovado nesta entrega.

Base de código: `7a33124e02faf7d43ebf33e12debde8a0369468c`. Na leitura inicial, Railway API estava em `SUCCESS`, deployment `6c6f27f6-7317-4f39-af88-d390a47e60cd`, nesse SHA. O frontend Vercel estava em `READY`, deployment `dpl_4D2LRkzARYqRow5hb7fky5hqwGb4`, SHA `408220ba9edda9fcb28a0f5138416d993779d043` [leitura dos provedores]. Esses registros são a base examinada, **não um deploy desta correção**.

## Resultado por item

| Item | Resultado | Evidência e limite |
|---|---|---|
| P0-1 · Zonas | CORRIGIDO, optativo | `mtf_chart_v2` exige janela explícita, funde faixas, deduplica instantes, preserva origem/confirmação e classifica pela faixa inteira. Dentro da faixa é zona em teste. `mtf_chart_v1` continua padrão. Comparação em `zone-version-comparison.json`; não é calibração. |
| P0-2 · Saída intermitente | CORRIGIDO e testado localmente | Falha transitória mantém o gráfico do mesmo contexto como consulta histórica. Ausência conta somente snapshots bem-sucedidos. A API verifica participação antes e depois do cálculo, inclusive com cursor. Refresh concorrente único, repetição limitada e proteção contra logout/troca de conta. |
| P0-3 · Aquecimento | CORRIGIDO | Pré-janela usa a janela real dos produtores. A avaliação mantém seu domínio original; falha no prefixo visual não altera um domínio de avaliação válido. Paridade do motor em testes e comparação de EMA200 com a função canônica em `ema-parity.json`. Não se afirma confronto com indicadores persistidos em produção. |
| P0-4 · Pavio ASTER | VERIFICAÇÃO PARCIAL, sem alteração de dados | OHLC dos candidatos examinados coincide com a resposta Gate: `gate-candle-comparison.json`. O candle exato da imagem, sem timestamp inequívoco, permanece NÃO VERIFICADO. Nenhum candle foi corrigido ou descartado. |
| P0-5 · Observação independente da tela | VERIFICADO SEM NOVA TASK | Task, roteamento e agenda já existem. GETs continuam sem persistência. Produção contém avaliações em `production-observations.json`; PostgreSQL local confirma deduplicação e isolamento. O agendador usa `include_display=False`, sem o histórico adicional de desenho. A agregação por símbolo não comprova cadência perfeita por watchlist. |
| P1-1 · Marcadores | CORRIGIDO | Orçamento total, prioridade do gatilho, sinais recentes e pivôs por tipo. Overlays persistidos; detalhes completos no crosshair. |
| P1-2 · Zonas no desenho | CORRIGIDO | Zonas do timeframe selecionado, proximidade apenas visual, rótulos separados. Confirmação fora da janela visível não é desenhada retroativamente. |
| P1-3 · Indicadores iniciais | CORRIGIDO | Lista inicial configurável, demais séries em chips, preferência persistida. |
| P1-4 · Painéis | CORRIGIDO e verificado no navegador | Famílias agrupadas, recolhimento, alturas mínimas e rolagem. A distribuição nativa de alturas foi corrigida usando proporções e espaço de arredondamento. Dimensões medidas em `browser-checks.json`. |
| P1-5 · Precisão | CORRIGIDO | Metadados Gate do par; indisponibilidade explícita. Não aplica precisão de preço a percentuais, volumes ou indicadores. |
| P1-6 · Janela visível | CORRIGIDO | Configuração por timeframe, histórico adicional navegável, preservação do intervalo no mesmo contexto. |
| P2-1 · Incremental | CORRIGIDO, com limite de desempenho documentado | Cursor por usuário/watchlist/perfil/configuração; revisão por timestamp; remoção de overlays; zonas inalteradas não reenviadas. Cursor inválido, contexto diferente ou perda de sobreposição exigem resposta completa. Carga fria continua custosa, conforme medições abaixo. |
| P2-2 · Tema | CORRIGIDO | Instância do chart preservada e nenhuma nova leitura de mercado na troca de tema. |
| P2-3 · Carga inicial | CORRIGIDO | Estado de carregamento, último ativo/watchlist/timeframe restaurados quando acessíveis. |
| P3-1 · Score bruto | CORRIGIDO | Bruto separado do efetivo, vetos preservados, ordenação visual por bruto/distância sem efeito de autorização. |
| P3-2 / P3-4 · Linguagem | CORRIGIDO | Motivos configuráveis em português, tradução dos enums e estados do histórico, fallback sem código interno cru. |
| P3-3 · Horário do candle | CORRIGIDO | Intervalo de abertura–fechamento avaliado; candle em formação identificado separadamente. |
| P3-5 · Tema do shell | CORRIGIDO | Controle rotulado como tema do ambiente MTF. |
| P3-6 · Troca de ativo | CORRIGIDO | Timeframe preservado e resposta atrasada do contexto anterior descartada. |
| P3-7 · Perfis | CORRIGIDO | Nome, timeframe e identificador desambiguam perfis; não exclui perfil válido por nome repetido. |
| P3-8 · Preços | CORRIGIDO com ausência explícita | Topo/legenda usam candles Gate e seu horário. Lista identifica snapshot da watchlist e seu timestamp quando disponível; não inventa horário quando ausente. |

## Testes e capturas

Saídas literais [comando]:

```text
77 passed in 9.81s
```

Esse conjunto cobre motor MTF, API, aquecimento, zonas, cursor, PostgreSQL real isolado, autorização pública L3, estabilidade ALLOW/Shadow e recuperação M5. Ver [checks.txt](evidence/mtf-ui-v2/checks.txt) para comandos e saída final dos testes frontend/build/lint.

O navegador usa uma **sessão isolada de teste**, respostas capturadas da Gate e interceptação de todas as chamadas API. Não é prova de autenticação ou funcionamento da nova versão em produção. Foram exercitados persistência, canvas estável, troca de ativo/timeframe, recolhimento, escala recortada, falhas, remoção/retorno e resposta atrasada. Registro: [browser-checks.json](evidence/mtf-ui-v2/browser-checks.json).

Capturas anteriores usam os assets publicados do frontend com respostas congeladas, também interceptadas. Capturas posteriores usam o build local. Os candles não foram capturados simultaneamente entre as versões; servem para comparar apresentação, não preço ou resultado de trading.

- [Antes — ASTER 5M](evidence/mtf-ui-v2/before-ASTER_USDT-5M-light.png)
- [Depois — ASTER 5M claro](evidence/mtf-ui-v2/ASTER_USDT-5M-light.png)
- [Depois — ASTER 5M escuro](evidence/mtf-ui-v2/ASTER_USDT-5M-dark.png)
- [Depois — viewport compacta](evidence/mtf-ui-v2/ASTER_USDT-1366x768.png)
- As demais capturas de AAVE/ASTER e timeframes estão em [evidence/mtf-ui-v2](evidence/mtf-ui-v2/).

## Divergência das zonas e medição de desempenho

A comparação usa o mesmo conjunto de candles/configuração em cada par antes/depois. Distâncias mudam em parte da amostra; a versão nova permanece optativa. Planos ausentes continuam `null`, sem preencher TP por suposição. Valores e configurações literais: [zone-version-comparison.json](evidence/mtf-ui-v2/zone-version-comparison.json).

Medições do transporte final com dados públicos ao vivo, JSON compacto sem compressão [comando]:

| Ativo | Carga fria local (s) | Consulta seguinte local (s) | Resposta completa (bytes) | Delta (bytes) |
|---|---:|---:|---:|---:|
| AAVE_USDT | 24.851328099997772 | 6.808724300000904 | 1001400 | 6753 |
| ASTER_USDT | 30.024201399999583 | 6.210694999997941 | 997523 | 6658 |

Fonte literal: [benchmark.json](evidence/mtf-ui-v2/benchmark.json). São observações individuais; não representam SLA, percentil ou significância estatística. O tamanho pode crescer quando fecham candles ou mudam zonas. O ganho demonstrado é na transferência incremental; **não foi demonstrada redução de latência da carga fria**. A consulta adicional de participação na API também não está incluída nesse benchmark da função local.

A medição anterior de produção está em [production-baseline-latency.json](evidence/mtf-ui-v2/production-baseline-latency.json). Ambiente, fonte temporal e conjunto de séries diferem: não é um experimento A/B comparável. O custo da pré-janela de desenho não foi adicionado ao agendador de avaliações.

## Configurações e ativação

Nenhuma configuração de produção foi salva nesta tarefa. O formulário existente de configuração passa a oferecer o preset explícito de `presentation`; o usuário pode revisá-lo e salvá-lo via ConfigService. Preferências visuais não alteram o hash da avaliação.

Para a versão nova de zonas é necessário selecionar explicitamente `schema_version: "mtf_chart_v2"` e fornecer `zone_lookback_candles` completo. A comparação usou este JSON [config: replay], não promovido:

```json
{"zone_lookback_candles":{"1h":300,"4h":300,"5m":500}}
```

Chaves de preferência adicionadas: `last_symbol`, `last_watchlist`, `last_timeframe`, `enabled_indicators`, `show_patterns`, `show_pivots`, `show_zones`, `collapsed_panels`, `clipped_scale`. Salvamentos são serializados para preservar a ordem das escolhas. Sessões antigas sem refresh token salvo precisam de novo login para obter renovação automática.

O preset integral de apresentação está copiado em [presentation-preset.json](evidence/mtf-ui-v2/presentation-preset.json). São parâmetros editáveis de apresentação, não thresholds calibrados de trading.

## Limites e próximo portão

- `[ABERTO]` Candle exato do print ASTER, por ausência de timestamp inequívoco.
- `[ABERTO]` Ensaio prolongado da versão nova no ambiente implantado e validação autenticada pós-publicação.
- `[ABERTO]` Comparação com séries persistidas do pipeline no mesmo candle; a evidência atual compara a função e janela canônicas localmente.
- `[ABERTO]` Latência e capacidade sob concorrência em homologação/produção. Não há alegação de melhora da carga fria.
- Publicação e ativação da nova versão de zonas não foram realizadas. Ordens reais, gates L3, TTL, limites de encaminhamento e histórico operacional não foram alterados.

## Ledger de evidências numéricas

| Número reportado | Origem | Valor literal da fonte |
|---|---|---|
| Testes backend aprovados | [comando] pytest, `checks.txt` | `77 passed in 9.81s` |
| Tempos AAVE_USDT | [comando] `benchmark.json` | `cold_seconds: 24.851328099997772`, `warm_seconds: 6.808724300000904` |
| Tempos ASTER_USDT | [comando] `benchmark.json` | `cold_seconds: 30.024201399999583`, `warm_seconds: 6.210694999997941` |
| Tamanhos AAVE_USDT | [comando] `benchmark.json` | `full_bytes: 1001400`, `delta_bytes: 6753` |
| Tamanhos ASTER_USDT | [comando] `benchmark.json` | `full_bytes: 997523`, `delta_bytes: 6658` |
| Janela usada na comparação | [config: replay] `zone-version-comparison.json` | `"zone_lookback_candles":{"1h":300,"4h":300,"5m":500}` |

Demais valores detalhados permanecem nos JSON de fonte, acompanhados de contexto. Nenhuma estimativa de taxa de acerto, expectativa ou promoção operacional foi derivada desses testes.
