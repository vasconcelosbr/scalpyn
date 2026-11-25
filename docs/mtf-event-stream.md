# Prévia MTF por eventos

O navegador abre um WebSocket autenticado diretamente no backend configurado em
BACKEND_URL. O token é enviado no primeiro frame, nunca na URL. O backend verifica
assinatura, tipo, expiração, proprietário da watchlist e associação do ativo antes
de consultar dados ou abrir a assinatura pública da Gate.io.

O canal spot.candlesticks entrega OHLCV completo do candle em formação. Cada evento
válido recalcula a mesma função forming_preview usada na leitura REST, em memória,
sem esperar poll_seconds. Usa volume base (campo a), não volume cotado (v).
Documentação do contrato: https://www.gate.com/docs/developers/apiv4/ws/

Os dados oficiais, pivôs confirmados, plano, score operacional, histórico e áudio
não são substituídos pelo canal. O evento devolve apenas prévia e candle visual.
Mensagens de outro ativo, atrasadas, repetidas, inválidas ou fora de ordem são
descartadas. A identidade do perfil e dos indicadores também precisa coincidir.

Fechamento ou mudança de janela solicita nova leitura pelo caminho canônico e
descarta o cache REST daquele ativo. A marca de fechamento do WebSocket não é
promovida a evidência de decisão. Reconexão também começa com nova leitura, sem
reconstruir candles perdidos por suposição.

poll_seconds permanece como recuperação REST e intervalo de revalidação de acesso,
configuração, fluxo e spread. Não controla o envio dos eventos de candle. Pivôs e
tendência do Bitcoin continuam confirmados; capital e fluxo conservam a frequência
e o horário de sua fonte. A latência depende da entrega da corretora, rede e cálculo;
não há promessa de atualização antes de um evento recebido.

A interface identifica Ao vivo / eventos da Gate.io. Perda de conexão invalida a
prévia do stream; REST de recuperação é identificado explicitamente. As linhas
continuam compactas e animadas. O fechamento da aba cancela a assinatura upstream.

Validação: projeção por eventos em configurações legadas e JSON, isolamento do
status oficial, volume base, ordem temporal, rollover, autenticação, propriedade,
mesclagem sem regressão para cache REST e preservação de contexto no frontend.
