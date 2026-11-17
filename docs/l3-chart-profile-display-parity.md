# Consistência do L3 Consolidado no Gráfico MTF

O gráfico consultava candidatos internos do Shadow e avaliações de outra fonte. Um ativo Armado na aba Aprovados podia ficar ausente no gráfico com o filtro Só alinhados.

O endpoint de leitura `/api/mtf/watchlist?watchlist_id=l3-consolidated` passa a usar todos os aprovados no perfil e a mesma observação validada por `/api/watchlists/l3-consolidated/assets`. A capacidade das simulações não limita a lista. Pendências continuam separadas; observações inválidas não fornecem estado Armado ao gráfico.

A abertura de `/api/mtf/chart` verifica a participação nessa lista de exibição antes e depois do cálculo. A seleção de um perfil exploratório não substitui o estado consolidado na barra lateral. O observador interno e seus limites permanecem independentes.

O endpoint externo, autenticação Bearer, campos `items`, regras dos perfis e bloqueio de ordens permanecem inalterados. `ARMED` é um estado observacional e não autoriza execução.

Validação: testes de API para participação fora da capacidade Shadow, estado compartilhado, pendências e leitura sem mutações; integração PostgreSQL para a observação mais recente e isolamento de perfil/usuário; build TypeScript. Evidência da publicação será registrada no ledger local após verificação autenticada.
