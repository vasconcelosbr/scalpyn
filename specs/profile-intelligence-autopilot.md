# Profile Intelligence Auto-Pilot Spot

## Objetivo

Criar um Auto-Pilot global no Profile Intelligence para calibrar continuamente todos os Strategy Profiles usados em L3, testar clones em Shadow Portfolio, criar novos profiles e watchlists a partir de combinações promissoras e promover automaticamente apenas candidatos com desempenho comprovado.

O sistema deve buscar alto Win Rate e P&L sem alterar diretamente profiles ativos, evitando profiles duplicados, repetição de padrões perdedores e degradação de estratégias já eficientes.

## Contexto

O recurso será usado pelo proprietário da conta Scalpyn dentro de Profile Intelligence.

Atualmente, Top Winners, Top Losers, combinações e sugestões dependem de ações manuais. Profiles semelhantes podem ser criados com pequenas variações, profiles existentes não recebem calibração recorrente e a promoção para uma watchlist/pipeline exige configuração manual.

O Auto-Pilot deve integrar:

- Profile Intelligence;
- Strategy Profiles;
- Score Engine Configuration;
- Watchlists e Pipeline L3;
- Shadow Portfolio;
- configuração do Spot Trading;
- trilha de auditoria e histórico de versões.

## Requisitos

1. **DEVE permitir controlar o Auto-Pilot por um botão global Liga/Desliga dentro de Profile Intelligence.**
   - O estado deve valer para toda a conta.
   - O Auto-Pilot deve atuar sobre todos os profiles associados a L3.
   - Ao desligar, deve parar novas análises, calibrações, criações, promoções e rollbacks automáticos.
   - Desligar não deve desativar profiles ou watchlists já promovidos.

2. **DEVE executar um ciclo automático de análise a cada 24 horas.**
   - O ciclo deve analisar indicators, combinations, suggestions, profiles L3, candidatos Shadow e profiles promovidos para Spot real.
   - O processo deve ser contínuo enquanto o Auto-Pilot estiver ligado.
   - Cada ciclo deve ser idempotente para não repetir ações já concluídas.

3. **DEVE preservar os profiles originais usados em L3.**
   - Um profile original nunca deve ser alterado diretamente pela calibração automática.
   - Toda calibração deve gerar um clone versionado.
   - O clone deve manter vínculo com o profile de origem, versão anterior, ciclo de calibração e métricas que justificaram sua criação.
   - O histórico completo deve permanecer consultável.

4. **DEVE permitir liberdade total de calibração nos clones.**
   - O Auto-Pilot pode adicionar, remover ou alterar indicadores, operadores, limites, Signal Conditions e regras de Scoring.
   - O Auto-Pilot não pode alterar diretamente o profile original ou uma versão live em avaliação.

5. **DEVE converter Top Winners em Signal Conditions dos clones.**
   - Cada regra deve preservar o indicador correto, operador e limiar.
   - Indicadores diferentes nunca podem ser convertidos para o mesmo campo por fallback de interface.
   - O sistema deve registrar a evidência estatística usada para incluir cada Signal Condition.

6. **DEVE converter Top Losers em regras negativas de pontuação.**
   - As regras negativas devem ser criadas ou reutilizadas no Score Engine Configuration.
   - As regras devem ser associadas ao Scoring do clone.
   - Top Losers não devem gerar Block Rules automaticamente.
   - A penalidade e o impacto máximo no score devem ser configuráveis.

7. **DEVE gerar candidatos a novos profiles a partir de Combinações Descobertas e Sugestões de Novos Profiles.**
   - O profile gerado deve preservar os indicadores, operadores e valores da combinação.
   - O profile deve ser criado inicialmente em modo Shadow.
   - O profile deve ser Spot e não Futures nesta entrega.
   - O profile deve usar as configurações de risco e capital de `/trading-desk/spot`.

8. **DEVE detectar duplicidade antes de criar um clone ou novo profile.**
   - A assinatura canônica deve considerar indicadores, operadores, direção das regras, Signal Conditions, regras negativas de Scoring e contexto Spot/L3.
   - A ordem das regras não deve tornar profiles diferentes.
   - Limiares com variação relativa de aproximadamente 20% devem pertencer à mesma família semântica.
   - Exemplo: `RSI >= 70` e `RSI >= 65` devem ser considerados equivalentes para impedir criação redundante.
   - O sistema deve comparar o candidato com profiles ativos, Shadow, reprovados e históricos.
   - Se já existir equivalente, o Auto-Pilot deve reutilizar a família existente, registrar a duplicidade e não criar outro profile.

9. **DEVE manter memória de padrões reprovados.**
   - Uma combinação ou família semântica reprovada deve ficar bloqueada por 60 horas.
   - Durante o bloqueio, variações dentro da tolerância de duplicidade não podem gerar novos candidatos.
   - O registro deve conter assinatura, métricas, motivo da reprovação, início e término do bloqueio.

10. **DEVE limitar a 30 o total de candidatos simultâneos em Shadow.**
    - O limite é global para a conta.
    - Quando uma nova vaga for necessária, deve sair o candidato com menor Win Rate observado.
    - O candidato removido deve ser desativado, não excluído.
    - Profile, watchlist, trades, métricas e relatórios devem permanecer no histórico.

11. **DEVE criar uma watchlist própria e exclusiva para cada novo profile candidato.**
    - A watchlist deve ser criada automaticamente junto com o profile.
    - Deve usar o padrão atual de criação e configuração de watchlists.
    - A Watchlist L2 vigente deve ser a fonte dos ativos para todas as novas watchlists automáticas.
    - A nova watchlist deve ser configurada como `Level: L3 - Signal + Score`.
    - O Strategy Profile L3 associado deve ser o novo profile criado.
    - Profile e watchlist devem possuir vínculos persistentes entre si e com a combinação/sugestão de origem.

12. **DEVE avaliar candidatos em Shadow até existir evidência suficiente.**
    - A avaliação normal deve ocorrer ao alcançar 100 trades ou 36 horas, o que ocorrer primeiro.
    - Se completar 36 horas com menos de 50 trades, o candidato deve continuar em Shadow.
    - Nenhuma promoção ou reprovação pode ocorrer com menos de 50 trades.
    - Ao alcançar 50 trades depois das 36 horas, o candidato pode ser avaliado.

13. **DEVE usar Win Rate e P&L médio como métricas de promoção.**
    - O candidato deve ter Win Rate maior ou igual a 80%.
    - O candidato deve ter P&L médio maior ou igual a 0,5%.
    - Os dois mínimos são obrigatórios.
    - Se existir um profile atual para a mesma função L3, o candidato deve superar o atual em Win Rate ou P&L.
    - A outra métrica não pode ser inferior à métrica correspondente do profile atual.
    - Se não existir candidato aprovado, o profile atual deve permanecer ativo.

14. **DEVE reprovar candidatos que não atinjam os mínimos após uma avaliação válida.**
    - O profile candidato e sua watchlist devem ser desativados.
    - Eles devem permanecer armazenados para histórico.
    - A família semântica deve entrar no bloqueio de 60 horas.
    - Nenhum profile substituto deve ser promovido apenas para preencher uma vaga.

15. **DEVE promover automaticamente candidatos aprovados para operações Spot reais.**
    - A promoção não deve exigir aprovação manual.
    - O candidato promovido deve substituir o profile anterior na associação L3 da watchlist correspondente.
    - O sistema deve manter a associação anterior para rollback.
    - A ativação live deve respeitar integralmente configurações, limites, disponibilidade de saldo, credenciais, gates e bloqueios de risco definidos em `/trading-desk/spot`.
    - O Auto-Pilot não deve possuir configuração paralela de capital.
    - Não deve existir limite próprio para a quantidade de profiles Spot promovidos simultaneamente.

16. **DEVE adiar a ativação real quando as condições operacionais não forem seguras.**
    - Se Spot Trading estiver desligado, faltarem credenciais válidas, não houver saldo permitido ou existir um bloqueio de risco, o profile deve ficar com estado `APPROVED_WAITING_LIVE`.
    - O profile aprovado deve permanecer em Shadow enquanto aguarda.
    - A ativação deve ocorrer automaticamente quando todos os gates operacionais forem satisfeitos.

17. **DEVE continuar monitorando profiles promovidos.**
    - A revisão completa deve ocorrer a cada 24 horas.
    - O sistema também deve monitorar degradação para rollback emergencial.
    - As métricas de referência devem ser registradas no momento da promoção.

18. **DEVE realizar rollback automático quando Win Rate ou P&L cair 20% relativamente ao valor registrado na promoção.**
    - A queda de qualquer uma das duas métricas deve acionar rollback.
    - Exemplo: Win Rate promovido de 80% gera limite de rollback em 64%.
    - O clone degradado deve sair imediatamente da operação real.
    - A última versão promovida e ainda válida deve ser restaurada automaticamente na watchlist L3.
    - Se não houver versão anterior aprovada, o profile degradado deve voltar para Shadow e a operação real deve ser removida.

19. **DEVE produzir um Relatório Executivo interno a cada 24 horas.**
    - O relatório deve ficar dentro de Profile Intelligence.
    - Não deve enviar e-mail, Telegram ou notificação externa nesta entrega.
    - Deve apresentar, no mínimo:
      - estado global do Auto-Pilot;
      - ciclo analisado;
      - profiles originais e clones;
      - candidatos criados, deduplicados, bloqueados, reprovados e promovidos;
      - métricas de Shadow e live;
      - Win Rate e P&L médio;
      - comparação contra profile anterior;
      - motivo de cada decisão;
      - watchlist e associação L3;
      - profiles aguardando condições para live;
      - rollbacks executados;
      - famílias perdedoras bloqueadas e prazo restante;
      - erros e ações não executadas.

20. **DEVE manter uma trilha de auditoria imutável.**
    - Deve registrar Liga/Desliga, início e fim de ciclo, criação, calibração, deduplicação, bloqueio, avaliação, reprovação, promoção, espera por gates, ativação live, desativação e rollback.
    - Cada evento deve conter usuário, timestamp, profile, versão, watchlist, combinação/sugestão, métricas de entrada, thresholds aplicados, decisão, motivo e resultado.
    - Falhas parciais devem ser auditadas sem deixar associações inconsistentes.

21. **DEVE usar estados explícitos para o ciclo de vida dos candidatos.**
    - Estados mínimos:
      - `SHADOW_COLLECTING`;
      - `SHADOW_READY_FOR_REVIEW`;
      - `REJECTED`;
      - `APPROVED_WAITING_LIVE`;
      - `LIVE`;
      - `ROLLED_BACK`;
      - `DISABLED`;
      - `DUPLICATE_SKIPPED`;
      - `LOSS_FAMILY_COOLDOWN`.

22. **DEVE executar operações multiobjeto de forma transacional ou compensável.**
    - Criação de profile, watchlist, associação L3 e registro de auditoria não pode deixar objetos órfãos.
    - Promoção e rollback devem trocar associações de forma atômica.
    - Se uma etapa falhar, o sistema deve reverter as alterações ou registrar uma tarefa segura de compensação.

23. **DEVE impedir ciclos concorrentes do Auto-Pilot para a mesma conta.**
    - Apenas um ciclo pode executar por usuário.
    - Execuções duplicadas, retries e reinícios não podem criar profiles ou watchlists adicionais.
    - O sistema deve usar chaves idempotentes por usuário, janela de 24 horas, profile e assinatura canônica.

24. **DEVE permitir inspeção manual sem interromper o processo.**
    - O usuário deve poder visualizar profiles, versões, watchlists, candidatos, métricas e auditoria.
    - O usuário pode desligar globalmente o Auto-Pilot.
    - Alterações manuais não devem apagar o histórico automático.

25. **PODE permitir filtros de visualização no Relatório Executivo.**
    - Por estado;
    - por profile de origem;
    - por watchlist;
    - por período;
    - por decisão;
    - por família semântica.

## Regras de decisão

### Janela de avaliação

1. Avaliar quando `trades >= 100`.
2. Caso contrário, avaliar quando `elapsed_hours >= 36` e `trades >= 50`.
3. Se `elapsed_hours >= 36` e `trades < 50`, continuar coletando dados em Shadow.

### Promoção

Promover somente quando todas forem verdadeiras:

1. `win_rate >= 0.80`;
2. `avg_pnl_pct >= 0.005`, considerando representação decimal canônica;
3. amostra válida conforme a janela de avaliação;
4. ausência de duplicata equivalente já melhor;
5. ausência de bloqueio da família;
6. quando houver incumbent, superar seu Win Rate ou P&L;
7. quando houver incumbent, não ser inferior na outra métrica;
8. todos os gates de segurança Spot permitirem ativação, ou entrar em `APPROVED_WAITING_LIVE`.

### Reprovação

Após amostra válida, reprovar quando:

- `win_rate < 0.80`; ou
- `avg_pnl_pct < 0.005`.

### Rollback

Após promoção, executar rollback quando:

- `current_win_rate < promotion_win_rate * 0.80`; ou
- `current_avg_pnl_pct < promotion_avg_pnl_pct * 0.80`.

### Deduplicação

1. Canonicalizar nomes e aliases de indicadores.
2. Ordenar regras antes de calcular a assinatura.
3. Preservar direção e operador.
4. Agrupar limites com diferença relativa de até aproximadamente 20%.
5. Comparar contra todo o histórico, não apenas profiles ativos.
6. Não criar candidato quando existir equivalente de desempenho igual ou melhor.

## Restrições

- A primeira entrega deve operar somente com Spot.
- Futures fica explicitamente fora desta entrega.
- O frontend continua em Next.js e o backend em FastAPI.
- O backend e workers são publicados no Railway.
- O frontend é publicado na Vercel.
- O PostgreSQL deve ser a fonte de verdade para estado, versões, métricas e auditoria.
- Redis pode ser usado para locks, filas e idempotência, mas não como única fonte de verdade.
- O scheduler deve executar em um único proprietário lógico para evitar ciclos duplicados.
- Toda decisão numérica deve usar dados reais do Shadow Portfolio ou da operação live; não pode fabricar métricas ausentes.
- Win Rate deve carregar contagem de trades.
- P&L deve ter unidade e representação inequívocas.
- Profiles reais devem obedecer aos gates existentes do Spot Trading.

## Casos extremos

1. **Auto-Pilot desligado durante um ciclo**
   - O ciclo deve terminar apenas a etapa segura atual e não iniciar novas mutações.
   - O cancelamento e o ponto de parada devem ser auditados.

2. **Candidato completa 36 horas com menos de 50 trades**
   - Continuar em `SHADOW_COLLECTING`.
   - Não promover nem reprovar.

3. **Candidato atinge 100 trades antes de 36 horas**
   - Avaliar imediatamente.

4. **Nenhum candidato atinge os mínimos**
   - Manter o profile atual.
   - Registrar as reprovações.
   - Não promover substituto.

5. **Novo candidato excede o limite de 30**
   - Desativar o candidato Shadow com menor Win Rate.
   - Preservar todo o histórico.

6. **Combinação equivalente a profile existente**
   - Não criar novo profile nem watchlist.
   - Registrar `DUPLICATE_SKIPPED`.

7. **Família equivalente reprovada há menos de 60 horas**
   - Não criar candidato.
   - Registrar `LOSS_FAMILY_COOLDOWN`.

8. **Profile aprovado sem condições para Spot real**
   - Manter em Shadow como `APPROVED_WAITING_LIVE`.
   - Reavaliar gates operacionais automaticamente.

9. **Profile live degrada 20% em qualquer métrica**
   - Executar rollback automático.
   - Restaurar última versão válida ou voltar para Shadow.

10. **Falha ao criar watchlist após criar profile**
    - Reverter o profile ou executar compensação idempotente.
    - Não deixar profile candidato ativo sem watchlist exclusiva.

11. **Falha durante troca da associação L3**
    - Manter a associação anterior ativa.
    - Não deixar a watchlist sem profile válido.

12. **Duas execuções tentam calibrar o mesmo profile**
    - Apenas a execução detentora do lock pode prosseguir.
    - A outra deve encerrar como duplicada.

13. **Métrica ausente ou inconsistente**
    - Não tomar decisão de promoção, reprovação ou rollback.
    - Registrar `INSUFFICIENT_EVIDENCE`.

14. **Profile original excluído manualmente durante avaliação**
    - Suspender o candidato e exigir reconciliação.
    - Não promover automaticamente sem vínculo válido.

15. **Profile ou watchlist alterado manualmente**
    - Criar nova versão de referência.
    - Não sobrescrever silenciosamente a alteração manual.

## Fora do escopo

- Auto-Pilot para Futures.
- Envio de relatórios por e-mail, Telegram ou outros canais externos.
- Alteração direta dos profiles originais.
- Criação automática de Block Rules a partir de Top Losers.
- Configuração própria de capital paralela ao Spot Trading.
- Exclusão permanente de profiles, watchlists, trades ou relatórios reprovados.
- Garantia absoluta de Win Rate futuro.
- Promoção com menos de 50 trades.

## Definição de concluído

- [ ] Existe um botão global Liga/Desliga funcional em Profile Intelligence.
- [ ] O estado do Auto-Pilot persiste por usuário.
- [ ] Um ciclo automático é executado a cada 24 horas sem concorrência duplicada.
- [ ] Todos os profiles usados em L3 são considerados pelo ciclo.
- [ ] Profiles originais permanecem imutáveis.
- [ ] Calibrações são realizadas em clones versionados.
- [ ] Top Winners são convertidos em Signal Conditions corretas.
- [ ] Top Losers são convertidos em regras negativas do Score Engine e associados ao Scoring.
- [ ] O sistema detecta equivalência de thresholds dentro da tolerância aproximada de 20%.
- [ ] Combinações equivalentes não criam profiles duplicados.
- [ ] Famílias reprovadas permanecem bloqueadas por 60 horas.
- [ ] No máximo 30 candidatos permanecem simultaneamente em Shadow.
- [ ] Ao exceder o limite, o candidato com menor Win Rate é desativado.
- [ ] Cada novo candidato recebe uma watchlist exclusiva baseada na Watchlist L2 padrão.
- [ ] Cada watchlist automática é configurada como L3 Signal + Score e ligada ao profile correto.
- [ ] Candidatos são avaliados com 100 trades ou após 36 horas com pelo menos 50 trades.
- [ ] Nenhum candidato é avaliado com menos de 50 trades.
- [ ] Promoção exige Win Rate de pelo menos 80% e P&L médio de pelo menos 0,5%.
- [ ] Um clone com incumbent só é promovido quando supera Win Rate ou P&L sem piorar a outra métrica.
- [ ] Profiles aprovados são ativados automaticamente em Spot quando os gates operacionais permitem.
- [ ] Profiles aprovados aguardam em Shadow quando Spot ou os gates de risco bloqueiam live.
- [ ] Profiles live continuam sendo reavaliados continuamente.
- [ ] Queda relativa de 20% no Win Rate ou P&L aciona rollback.
- [ ] Rollback restaura a última versão aprovada disponível.
- [ ] Sem versão anterior, o profile degradado volta para Shadow.
- [ ] Profiles e watchlists reprovados permanecem disponíveis no histórico.
- [ ] O Relatório Executivo é gerado dentro de Profile Intelligence a cada 24 horas.
- [ ] Toda decisão e mutação possui evento de auditoria com métricas e motivo.
- [ ] Criação, promoção e rollback não deixam objetos ou associações órfãos.
- [ ] Retries e reinícios não criam profiles ou watchlists duplicados.
- [ ] O sistema opera somente em Spot nesta entrega.
