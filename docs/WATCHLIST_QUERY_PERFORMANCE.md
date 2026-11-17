# Consulta da decisão mais recente da Watchlist

A classificação do perfil utiliza apenas a decisão mais recente de cada par
perfil/ativo. A consulta anterior extraía os campos JSONB durante a ordenação e
deduplicação do histórico; os registros históricos contêm grandes conjuntos de
indicadores que não são necessários para escolher a decisão.

`latest_profile_decisions_statement` primeiro seleciona os IDs usando os mesmos
filtros de usuário, par perfil/ativo e tipo de evento, com a mesma ordenação por
data e ID. Depois busca a evidência JSONB somente para esses IDs pela chave
primária. A classificação, os motivos de pendência e a validação MTF permanecem
inalterados. Não há cache, truncamento do universo nem busca de uma aprovação
mais antiga quando a decisão atual é inválida.

O endereço `/api/watchlists/l3-consolidated/assets`, sua autenticação, o campo
`items` e o contrato observacional de Armados são preservados. A leitura não
autoriza ordens. Não há migração ou alteração de configuração de perfil.

O teste PostgreSQL compara a projeção com a consulta anterior, incluindo empates,
perfis diferentes, isolamento por usuário, eventos excluídos, ausência de
evidência e pares sem decisão. Também verifica no plano real que o JSONB não
é projetado na etapa de ordenação/deduplicação. A publicação deve incluir a API
e o coletor de histórico MTF, que compartilham esse carregador.
