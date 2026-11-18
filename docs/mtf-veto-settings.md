# Filtros de veto MTF

O diálogo de configuração do Gráfico MTF tem uma aba **Vetos**. Ela reúne controles independentes para baixa do ativo, lateralidade, baixa do Bitcoin, spread, distância do stop e retorno/risco líquido. Os limites existentes são reaproveitados, sem redefinir valores salvos. A validade dos dados também fica nessa aba, separada dos filtros opcionais.

`mtf_analysis.veto_filters` é validado no backend e persistido pelo serviço de configuração existente, com auditoria e invalidação de cache. Configurações anteriores recebem os controles ativos na leitura, sem atualização do banco. O conjunto padrão é excluído do hash do motor para preservar identidades de observações e contratos antigos. Uma alteração efetiva dos controles altera o hash e exige nova avaliação; simulações abertas continuam usando seu snapshot original.

Desativar o filtro de Bitcoin remove o veto de baixa e a exigência de seu contexto para os demais ativos. Não remove requisitos do ativo analisado. Desativar o filtro de spread remove somente o limite: a leitura do spread continua obrigatória para calcular o plano. Desativar retorno/risco remove o mínimo tanto na avaliação quanto na entrada simulada, mantendo custos, risco calculável e validade dos alvos. Dados ausentes/vencidos e plano inválido não são opcionais. Nenhum controle habilita ordens reais.

Validação: testes comportamentais de cada filtro, integridade dos dados, compatibilidade de hashes, contratos congelados, API de gravação, formulário e sincronização com JSON. A aceitação local usa dados sintéticos e gravações simuladas. A validação de produção usa leitura autenticada e edição de rascunho com cancelamento, sem desativar filtros na conta do usuário.

O comando amplo `npm test` também executou um teste preexistente de Shadow Portfolio que procura a string `if (source) params.set("source", source)`; a implementação atual utiliza outra montagem de parâmetros. Esses arquivos não foram alterados nesta entrega. Os testes MTF, lint dos arquivos alterados e build passaram.
