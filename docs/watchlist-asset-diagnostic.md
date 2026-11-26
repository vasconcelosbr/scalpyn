# Diagnóstico completo do ativo

Em `/watchlist`, o símbolo nas listas Approved, Rejected e Aguardando dados abre
`/watchlist/{watchlistId}/{symbol}`. O acesso também está disponível nas listas
Futures e no consolidado, usando a watchlist de origem. A seta conserva o resumo.

O endpoint autenticado `GET /api/watchlists/{watchlistId}/assets/{symbol}/diagnostic`
valida o proprietário da watchlist antes de consultar qualquer evidência. Um ativo
com apenas um registro de rejeição continua acessível. Ausência de vínculo retorna
404; respostas são privadas e não armazenáveis em cache.

O documento `WATCHLIST_ASSET_DIAGNOSTIC_V1` reúne os registros completos de:

- ativo e rejeições da watchlist;
- classificação atual do perfil e decisão persistida;
- metadados de mercado;
- última leitura de indicadores por timeframe, mercado e produtor;
- última observação e avaliação MTF da watchlist;
- observação MTF do consolidado para o mesmo perfil;
- configuração atual do perfil, indicadores e MTF.

Cada fonte conserva seus horários e dados brutos. A configuração atual não é
apresentada como configuração histórica da decisão. A tabela mostra cada campo
dos indicadores sem sobrepor produtores; valores nulos, zero e false são distintos.
As seções expansíveis preservam todos os campos das evidências, inclusive entradas
e regras congeladas quando presentes. Ausência de registro fica explícita.

Exportar JSON completo baixa o mesmo documento consultado, incluindo campos que
os filtros da tabela ocultam. O proxy transmite a resposta como stream para não
bufferizar evidências extensas. Trata-se dos últimos snapshots persistidos, não de
um arquivo com toda a série histórica. A leitura não recalcula indicadores, agenda
tarefas, altera configurações, aprova ativos ou autoriza execução.

Validação: testes de isolamento por proprietário, ativos rejeitados sem membership,
produtores distintos, leituras futuras e mercado incorreto; testes de navegação
em todos os níveis, exportação integral e transmissão de payload extenso. Os testes
PostgreSQL usam banco isolado na CI; a consulta também foi executada em transação
READ ONLY na produção antes da publicação.
