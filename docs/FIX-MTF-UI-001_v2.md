# FIX-MTF-UI-001 v2 — implementação revisada

Revisão aprovada em conversa. Substitui a v1 como orientação de implementação. Os relatos de produção da v1 são hipóteses de reprodução, não resultados verificados desta revisão.

## Contratos obrigatórios

- Estender a página, o renderizador, a API e o motor MTF existentes. Nenhum motor paralelo ou integração com execução real.
- Manter `operational_effect=false`, isolamento do Shadow MTF, bloqueio de ordens reais e histórico append-only. Não alterar autorização L3, TTL, limite horário ou deduplicação do Shadow Portfolio.
- Separar configuração de apresentação das regras de cálculo. Preferências visuais não mudam a identidade da avaliação. Novos parâmetros configuráveis são persistidos por ConfigService e editáveis pela interface; presets explícitos não são resultados calibrados.
- Não ativar automaticamente regras novas em configurações salvas. Versão observacional nova mantém leitura compatível da versão anterior. Comparar resultados antes/depois sem reescrever histórico.
- Trabalhar em branch; entregar código, testes, evidências e pendências. Publicação e promoção operacional não são implicitamente autorizadas por este documento.

## Estabilidade e autorização

- Manter o último gráfico válido do mesmo contexto em falhas transitórias, com horário e aviso de desatualização. Nunca reutilizar payload de outro ativo, perfil ou usuário.
- A saída do Consolidado pode ser legítima: autorização expirada, veto, falta de cotação ou ausência de Shadow confirmado. Não transformar tolerância visual em extensão da autorização.
- Consultas da lista informam instante/identidade de snapshot. Chart continua verificando participação atual; remoção confirmada é apresentada como saída da lista. Dados mantidos na tela passam a consulta histórica, sem gatilho/estado atual confirmado.
- Debounce de remoção somente na apresentação, contando respostas de lista bem-sucedidas; falhas não contam como ausência. Retirar imediatamente o selo de elegibilidade ao detectar saída.
- Renovação de sessão no cliente HTTP compartilhado, com refresh concorrente único, repetição limitada e encerramento da sessão somente após rejeição definitiva. Falha de rede no refresh não deve destruir credenciais.

## Aquecimento e zonas

- Buscar uma pré-janela suficiente para cada candle exibido, mantendo o tamanho e as funções de cálculo do produtor canônico. Recortar apenas a apresentação. Não trocar a inicialização da EMA por um prefixo diferente.
- ATR e padrões usam funções canônicas sobre candles fechados; dados insuficientes resultam em ausência com motivo. Não preencher lacunas nem usar candle em formação para gerar sinal.
- Preservar o domínio histórico de pivôs usado pelo motor anterior durante a correção visual. Ampliar histórico de desenho não pode alterar tendência, zonas ou plano silenciosamente.
- Para a versão observacional de zonas: restringir pivôs à janela declarada, agrupar faixas sobrepostas/próximas e deduplicar os pivôs constituintes. Preservar origem e instante de disponibilidade.
- Preço de referência: fechamento M5 avaliado. Faixa inteiramente abaixo é suporte; inteiramente acima é resistência; preço dentro da faixa é zona de teste, sem inferir rompimento pelo ponto médio. Somente pivôs confirmados até a avaliação podem participar.
- Zonas do gráfico pertencem ao timeframe selecionado; a zona de contexto do plano continua explicitamente identificada como H1. Separar limites de desenho de limites de cálculo.
- Comparar distância, plano, vetos e status entre versões no mesmo conjunto de candles/configuração. Divergência impede ativação automática, mas não bloqueia a entrega de melhorias visuais independentes.

## Interface

- Renderizador persistente: atualizar dados e opções sem remover/recriar o chart por polling, tema ou seleção de indicadores. Preservar zoom do mesmo contexto.
- Indicadores iniciais: EMA9/EMA21, volume e média, RSI e ADX canônicos disponíveis. Outros indicadores continuam selecionáveis. Persistir indicadores, overlays, painéis recolhidos, ativo, timeframe e watchlist.
- Limitar marcadores por orçamento total configurado, dando prioridade ao gatilho atual, depois sinais recentes e pivôs recentes. Mostrar detalhes completos no crosshair; filtros de desenho não alteram reconhecimento do motor.
- Mostrar zonas próximas, com rótulos sem colisões. Agrupar DI/ADX e as variantes RSI em painéis comuns. Garantir alturas mínimas e rolagem quando necessário.
- Preservar timeframe ao trocar ativo. Restaurar seleção salva quando ainda acessível; fallback para candidato atual com informação clara.
- Formatação de preço deriva de metadados Gate do par, com estado de indisponibilidade explícito. Não inventar precisão por símbolo. Quantidades, percentuais e indicadores não usam precisão de preço.
- Escala completa por padrão. Recorte de extremos é opcional e apenas visual, com indicação dos valores fora de escala e botão para restaurar escala completa.
- Score bruto é soma dos componentes disponíveis, separado do score efetivo e dos vetos. Não usar bruto para autorizar trade; ordenação por proximidade é apenas visual e explicitamente identificada.
- Traduzir enums/motivos na apresentação, com fallback compreensível para códigos desconhecidos. Rotular intervalo de candle fechado e horários das fontes de preço.
- Tema limitado ao ambiente MTF, com rótulo claro. Nomes de perfil repetidos são desambiguados por timeframe/identificador, não excluídos.

## Dados incrementais e cache

- Estender `/api/mtf/chart`, sem canal paralelo. Resposta inicial completa; respostas seguintes atualizam candles/séries por timestamp, incluindo revisão do candle aberto.
- Cursor vinculado ao contexto e às versões de cálculo. Mudança de perfil, configuração, versão, lacuna ou cursor inválido exige reset explícito. Não misturar deltas de contextos diferentes.
- Cache bruto pode ser compartilhado para dados públicos Gate; cálculo inclui identidade de indicadores e configurações. Autorização de usuário/watchlist é revalidada fora do cache.
- Preferências não alteram hash da avaliação. Polling e backoff configuráveis; mudanças puramente locais de tema/timeframe não fazem nova leitura de mercado.

## Verificações

- Task `app.tasks.mtf_chart.observe` já existe. Verificar agendamento, consumo, watchlists monitoradas, fechamentos elegíveis e idempotência; corrigir a causa descoberta sem duplicar task.
- Investigar o candle ASTER indicado usando timestamp e OHLCV exatos. Comparar captura armazenada, resposta do gráfico e fonte Gate. Sem timestamp inequívoco, registrar não verificado; não corrigir dados nem concluir que um pavio é erro apenas pela aparência.
- Testar falha transitória, refresh concorrente, logout, troca rápida de ativo, remoção real e retorno à lista; nenhum dado antigo pode manter autorização atual.
- Testar paridade de indicadores no mesmo candle, ausência de lookahead, insuficiência de histórico, fusão de zonas e manutenção de vetos; comparar versões sem gravar decisões de teste em produção.
- Testar delta equivalente ao snapshot completo e invalidação de cache/cursor; medir tamanho e latência por comando real, sem estimativas apresentadas como resultados.
- Verificar layout nos temas e timeframes em viewport compacta e ampla, marcadores, legenda, seleção e persistência. Registrar evidência autenticada quando disponível.

## Entrega

`FIX-MTF-UI-001_v2_RESULT.md`: status por item, evidência, testes, capturas, configurações propostas/salvas e limites da verificação. Evidência deve distinguir código, teste local, leitura de produção e inferência. Nenhuma declaração de publicação sem implantação terminal e verificação do runtime.
