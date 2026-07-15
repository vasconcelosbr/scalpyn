# PROMPT — Auditoria ML + Auto-Pilot + Profile Intelligence — Scalpyn

Você é um auditor técnico sênior de sistemas de trading quantitativo, ML tabular, XGBoost/LightGBM/CatBoost, Auto-Pilot de regras e arquitetura FastAPI/PostgreSQL/React.

Preciso que você faça uma auditoria profunda no sistema Scalpyn, com foco em:

1. Modelo de ML atual
2. Uso de XGBoost, LightGBM e CatBoost
3. Governança Champion/Challenger
4. Auto-Pilot
5. Profile Intelligence
6. Dataset por profile/regime/skill
7. Risco de mistura de dados
8. Risco de múltiplos modelos influenciarem decisão operacional ao mesmo tempo
9. Qualidade dos outputs apresentados na UI
10. Rastreabilidade completa das decisões automáticas

---

## Regras de Segurança

- Não execute mudanças destrutivas.
- Não rode migrations sem autorização.
- Não altere regras de produção sem aprovação.
- Não re-treine modelos em produção sem autorização.
- Não assuma que uma feature está funcionando apenas porque existe toggle na UI.
- Valide código, banco, logs, tabelas, endpoints, workers e fluxo real.
- Se não houver evidência, não afirme que funciona.
- Se encontrar código morto, marque como código morto.
- Se a UI mostra botão/toggle mas o backend não executa, marque como falso positivo de funcionalidade.
- Se o backend executa mas a UI não mostra origem/modelo/run, marque como risco de rastreabilidade.
- Se o Auto-Pilot aplica mudança sem diff e audit log, marque como crítico.
- Se LightGBM e CatBoost podem gerar sugestões simultâneas sem separação, marque como alto ou crítico dependendo do impacto.
- Se algum dado de futuro entra no treino, marque como crítico.
- Se score antigo entra como feature para prever outcome, marque como risco de leakage/proxy contamination.

---

# 1. Contexto do Sistema

O sistema Scalpyn é um sistema de trading/simulação para cripto spot/futures.

Arquitetura geral:

- Backend FastAPI/Python
- PostgreSQL
- Workers/schedulers
- Railway/Cloud Run/GCP em algumas partes
- Frontend React/Next.js
- Pipeline por camadas:
  - POOL
  - L1
  - L2
  - L3
- Shadow Portfolio para simulações
- Profile Intelligence para análise de indicadores/profiles
- Auto-Pilot para sugerir ou aplicar ajustes em profiles
- ML tabular usando XGBoost e possíveis challengers LightGBM/CatBoost

Objetivo operacional:

Capturar entradas com potencial de alta de aproximadamente **+1%** e evitar entradas com maior probabilidade de queda de **-1%**.

O sistema trabalha com indicadores como:

- RSI
- ADX
- DI+/DI-
- MACD
- MACD Histogram
- ATR%
- EMA9/21/50/200
- VWAP distance
- Bollinger Band Width
- Spread
- Orderbook depth
- Taker ratio
- Volume delta
- Volume 24h
- Market cap
- Score
- Regime de mercado
- Profile name
- Strategy skill
- Auto-Pilot skill
- Symbol
- Session/time features
- Macro context quando disponível

---

# 2. Problema Principal a Auditar

Na UI de configuração do **Profile Intelligence** existem toggles opcionais como:

- Dynamic Combinations
- Association Rules
- Anthropic AI Explanations
- Optuna Search
- LightGBM
- CatBoost

A dúvida crítica é:

**LightGBM e CatBoost estão sendo tratados como simples recursos opcionais independentes, mas ambos são modelos de Gradient Boosting Decision Trees e podem cumprir função parecida.**

Auditar se isso está correto.

O sistema **não deve permitir** que múltiplos modelos tomem decisão operacional ao mesmo tempo sem governança.

Arquitetura desejada:

- Pode existir mais de um modelo instalado.
- Pode existir benchmark com XGBoost, LightGBM e CatBoost.
- Apenas **um modelo** pode ser o decision engine ativo por profile/regime.
- Os demais devem ser challengers/offline/benchmark.
- Auto-Pilot não pode misturar recomendações de modelos diferentes sem registrar origem, métrica e justificativa.
- Profile Intelligence não pode apresentar recomendações sem dizer qual modelo, dataset, profile e run gerou a conclusão.

---

# 2.1 Diagnóstico Prévio Já Levantado — Profile Intelligence, LightGBM e CatBoost

Incorporar este diagnóstico como **hipótese forte inicial**, mas ainda assim validar com evidências no código, banco, logs, workers e UI.

## Diagnóstico fechado até o momento

O **Profile Intelligence está operacional e produz análises**, porém **LightGBM e CatBoost não estão funcionais nesse fluxo**.

As flags estão ligadas no banco, mas são configurações órfãs:

- não instalam bibliotecas;
- não executam treinamento;
- não executam inferência;
- não geram auditoria;
- não geram atribuição de modelo;
- não contribuem para sugestões;
- não alteram o Auto-Pilot;
- não alteram o Profile Intelligence de forma efetiva.

Conclusão preliminar:

```text
Profile Intelligence: saudável / ativo
LightGBM: não funcional / não integrado / contribuição zero
CatBoost: não funcional / não integrado / contribuição zero
```

## Registro local no Graphify

O diagnóstico foi registrado no grafo local com o seguinte comando/resultado:

```text
graphify save-result
Question:
Saúde e contribuição de LightGBM e CatBoost no Profile Intelligence

Answer:
Profile Intelligence está operacional, porém LightGBM e CatBoost não estão implementados no fluxo:
flags estão habilitadas no config, bibliotecas não estão na imagem, não há consumidores backend,
testes, eventos de auditoria ou atribuição de modelo. Contribuição efetiva atual: zero.
O engine usa análises estatísticas, counterfactual miner, dynamic combinations, association rules,
Optuna TPE e Anthropic.

Arquivo salvo:
graphify-out\memory\query_20260619_143636_saúde_e_contribuição_de_lightgbm_e_catboost_no_pro.md
```

Auditar se esse registro está coerente com o estado atual do repositório e se deve ser considerado como evidência histórica, evidência operacional atual ou apenas anotação investigativa.

## Veredito preliminar

| Recurso | Estado | Contribuição ao Profile Intelligence |
|---|---|---|
| Profile Intelligence | Saudável | Ativa |
| LightGBM | Não funcional / não integrado | Zero |
| CatBoost | Não funcional / não integrado | Zero |

## Evidências já levantadas

Validar e confirmar novamente cada evidência abaixo:

1. As duas flags estão habilitadas no banco:

```json
{
  "enable_lightgbm": true,
  "enable_catboost": true
}
```

2. Porém não existe consumidor backend efetivo dessas flags.

As flags aparecem somente em:

```text
schema
defaults
frontend
```

E foram localizadas em:

```text
C:/Users/ricar/Default Directory/ARQUIVOS - Documentos/SCALPYN/scalpyn/scalpyn/backend/app/api/profile_intelligence.py:48
```

3. LightGBM e CatBoost não constam nas dependências ou imagem de produção.

Apenas XGBoost está declarado em:

```text
C:/Users/ricar/Default Directory/ARQUIVOS - Documentos/SCALPYN/scalpyn/scalpyn/backend/requirements.txt:25
```

4. A API testa disponibilidade de LightGBM, mas não executa treinamento ou inferência.

5. CatBoost nem possui teste de disponibilidade.

6. Não existem testes automatizados para LightGBM ou CatBoost.

7. Nenhum evento de auditoria ou combinação possui atribuição LightGBM/CatBoost.

8. A interface permite ativar LightGBM/CatBoost, mas a execução manual não inclui essas opções no payload:

```text
C:/Users/ricar/Default Directory/ARQUIVOS - Documentos/SCALPYN/scalpyn/scalpyn/frontend/app/profile-intelligence/page.tsx:1563
```

## Saúde real do Profile Intelligence

O engine principal está funcionando.

Evidências já levantadas:

| Métrica | Origem | Valor literal |
|---|---:|---:|
| Runs concluídos nos últimos 30 dias | query | 8 |
| Eventos de erro do Profile Intelligence | query | 0 |
| Profiles analisados no último run | query | 25 |
| Buckets no último run | query | 57 |
| Regras avaliadas no último run | query | 38 |
| Combinações dinâmicas no último run | query | 500 |
| Sugestões no último run | query | 10 |
| Requisições HTTP bem-sucedidas na amostra Railway | query | 300/300 |

Interpretação preliminar:

O Profile Intelligence está operacional, mas sua análise atualmente é produzida por:

- estatística de performance;
- indicator lift;
- rule contribution;
- counterfactual miner;
- dynamic combinations;
- association rules;
- Optuna TPE;
- explicações Anthropic.

A análise **não é produzida por LightGBM ou CatBoost**.

## Conclusão preliminar sobre os toggles

Os toggles LightGBM/CatBoost são configurações órfãs e dão uma impressão incorreta de funcionalidade.

Não existem erros desses algoritmos porque eles aparentemente nunca são executados.

Essa conclusão precisa ser validada em auditoria por meio de:

- busca no código por `enable_lightgbm`;
- busca no código por `enable_catboost`;
- busca no código por `lightgbm`;
- busca no código por `catboost`;
- validação do `requirements.txt`;
- validação da imagem/container de produção;
- validação dos payloads frontend/backend;
- validação dos eventos de auditoria;
- validação das sugestões geradas;
- validação dos runs de Profile Intelligence;
- validação de logs dos workers;
- validação de testes automatizados existentes ou ausentes.

## Perguntas adicionais obrigatórias para esta auditoria

Responder explicitamente:

1. As flags `enable_lightgbm` e `enable_catboost` são apenas campos de configuração sem efeito?
2. Existe qualquer path real de execução que importe LightGBM?
3. Existe qualquer path real de execução que importe CatBoost?
4. Existe qualquer modelo LightGBM treinado, persistido ou registrado?
5. Existe qualquer modelo CatBoost treinado, persistido ou registrado?
6. Existe qualquer sugestão cuja origem seja LightGBM?
7. Existe qualquer sugestão cuja origem seja CatBoost?
8. Existe qualquer evento de auditoria com `model_type = lightgbm`?
9. Existe qualquer evento de auditoria com `model_type = catboost`?
10. A UI induz o usuário a acreditar que LightGBM/CatBoost estão ativos?
11. A execução manual do Profile Intelligence ignora esses toggles?
12. O backend ignora esses toggles?
13. Os toggles devem ser removidos, desabilitados ou renomeados como “experimental / não implementado”?
14. O sistema precisa bloquear a ativação desses toggles enquanto não houver implementação real?
15. O Profile Intelligence deve exibir um warning explícito quando uma flag estiver ligada mas a dependência/backend não estiver disponível?

## Ajuste no foco da auditoria

A auditoria não deve mais partir apenas da pergunta genérica:

```text
LightGBM e CatBoost podem conflitar?
```

Ela deve partir de uma hipótese mais precisa:

```text
LightGBM e CatBoost parecem não estar integrados ao fluxo real do Profile Intelligence.
O risco atual não é conflito de decisão entre modelos, mas falsa sensação de funcionalidade,
configuração órfã, ausência de auditabilidade e UI enganosa.
```

Mesmo assim, auditar também o risco futuro:

```text
Se LightGBM/CatBoost forem implementados depois, deve existir governança champion/challenger
antes de permitir que influenciem sugestões, Auto-Pilot ou decisão operacional.
```

## Recomendação preliminar a validar

Até implementação real:

- desabilitar os toggles LightGBM/CatBoost na UI; ou
- mostrar como `Experimental / Não implementado`; ou
- remover do payload de configuração; ou
- adicionar warning explícito:
  - biblioteca não instalada;
  - backend não implementado;
  - contribuição atual zero;
  - não influencia sugestões;
  - não influencia Auto-Pilot.

Se forem implementados futuramente, exigir antes:

- dependências instaladas;
- testes automatizados;
- model registry;
- source_model;
- source_run_id;
- model_type;
- métricas próprias;
- audit log;
- benchmark separado;
- proteção champion/challenger;
- garantia de que challenger não altera produção;
- trilha clara no Profile Intelligence;
- documentação no frontend.

---

# 3. Objetivo da Auditoria

Realize uma auditoria técnica completa para responder:

1. LightGBM e CatBoost estão apenas disponíveis para benchmark ou estão influenciando decisão real?
2. Existe risco de XGBoost, LightGBM e CatBoost gerarem recomendações conflitantes?
3. Existe apenas um modelo champion ativo por profile/regime?
4. Existe registro claro de qual modelo gerou cada recomendação?
5. O Auto-Pilot sabe diferenciar:
   - recomendação estatística
   - recomendação ML
   - recomendação por regra
   - recomendação por Anthropic AI
   - recomendação por associação
   - recomendação por combinação dinâmica?
6. O Profile Intelligence informa corretamente:
   - profiles analisados
   - trades fechados
   - win rate base
   - melhor profile
   - melhor combinação
   - combinações analisadas
   - sugestões pendentes
   - sugestões de alta confiança
   - total de runs
   - status dos runs?
7. A tabela de Indicadores com Melhor Performance informa em quais profiles cada indicador aparece?
8. Quando o mesmo indicador aparece em dois ou mais profiles, a UI mostra todos os profiles associados?
9. Existe botão seguro para incluir/ajustar indicador em um profile?
10. Esse botão sabe se o ajuste deve ir para:
    - signals
    - block_rules
    - scoring
    - entry_triggers
    - minimum_score?
11. Existe trilha de auditoria para tudo que o Auto-Pilot criou, sugeriu, alterou ou rejeitou?
12. Existe risco de mistura de dados entre profiles no ML?
13. O dataset global e o dataset segregado por profile estão claramente separados?
14. Existe risco de leakage por campos como score, direction, outcome, pnl, exit_price, max_profit, future price?
15. Existe risco de NaN ser preenchido como zero indevidamente?
16. Existe risco de o modelo aprender somente a decisão antiga do L3 em vez do outcome real?
17. Existe risco de ALLOW-only bias?
18. Existe separação real entre dados de treino, validação, teste e produção?
19. Existe walk-forward validation ou apenas split simples?
20. Existe política clara de promoção/rejeição de modelo?
21. Existe proteção contra overfitting?
22. Existe controle de Optuna para não promover modelo sobreajustado?
23. Existe versionamento de dataset, features, modelo, threshold e profile_config usado no treino?
24. O Auto-Pilot consegue explicar por que uma regra foi criada ou alterada?
25. O Auto-Pilot consegue reverter alteração ruim?
26. O sistema impede que o Auto-Pilot crie vetos absolutos perigosos que bloqueiam pumps legítimos?
27. O sistema separa bloqueios de risco extremo de filtros estratégicos adaptativos?

---

# 4. Escopo da Auditoria

Auditar, no mínimo, os seguintes componentes:

## 4.1 Backend

- endpoints do Profile Intelligence
- endpoints do Auto-Pilot
- endpoints de ML/model training/model scoring
- serviços de análise de indicadores
- serviços de geração de combinações
- serviços de associação
- serviços de Anthropic AI
- serviços de Optuna
- uso real de LightGBM
- uso real de CatBoost
- uso real de XGBoost

## 4.2 Banco de Dados

- tabelas de trades simulados
- shadow_trades
- profile_intelligence_runs
- profile_intelligence_suggestions
- autopilot logs
- model registry
- model runs
- model metrics
- feature snapshots
- config_profiles
- audit logs
- qualquer tabela relacionada a model champion/challenger

## 4.3 Workers/Jobs

- trainer
- scheduler
- profile intelligence runner
- autopilot runner
- shadow portfolio updater
- feature collector
- model scoring job

## 4.4 Frontend

- tela Profile Intelligence
- Overview
- Indicadores com Melhor Performance
- Combinações
- Sugestões
- Configurações
- toggles LightGBM/CatBoost
- botão de aplicar sugestão em profile
- tela Auto-Pilot
- logs/trilhas de auditoria

## 4.5 Configurações

- env vars
- feature flags
- toggles salvos no banco
- default values
- diferença entre toggle visual e execução real
- fallback quando biblioteca não está instalada

---

# 5. Fase 1 — Inventário Técnico

Mapeie todos os arquivos, classes, funções, endpoints, jobs e tabelas relacionados a:

- XGBoost
- LightGBM
- CatBoost
- Optuna
- Auto-Pilot
- Profile Intelligence
- Dynamic Combinations
- Association Rules
- Anthropic AI Explanations
- model training
- model scoring
- champion/challenger
- profile suggestions
- applying suggestions to profiles

Entregue uma tabela com:

| Componente | Arquivo | Função/Classe | Responsabilidade | Usado em produção? | Apenas UI? | Código morto? | Risco encontrado | Evidência |
|---|---|---|---|---|---|---|---|---|

---

# 6. Fase 2 — Auditoria dos Toggles LightGBM e CatBoost

Verifique profundamente:

1. Os toggles LightGBM e CatBoost são apenas opções de benchmark?
2. Eles podem ser ligados ao mesmo tempo?
3. Se ligados ao mesmo tempo, o que acontece?
4. Eles treinam modelos separados?
5. Eles geram recomendações separadas?
6. Eles alteram Profile Intelligence?
7. Eles alteram Auto-Pilot?
8. Eles alteram decisão L3?
9. Eles alteram score?
10. Eles alteram sugestões?
11. Eles escrevem em tabelas separadas?
12. Existe model_name/model_type/model_version em todos os registros?
13. Existe run_id único por modelo?
14. Existe separação entre:
    - benchmark result
    - production decision
    - autopilot suggestion?
15. Existe risco de ensemble implícito não documentado?
16. Existe risco de recomendações conflitantes serem agregadas sem critério?

Classifique o risco:

- **CRÍTICO:** dois ou mais modelos podem influenciar decisão operacional simultaneamente sem governança.
- **ALTO:** dois ou mais modelos geram sugestões sem origem clara.
- **MÉDIO:** toggles existem mas execução é apenas parcial.
- **BAIXO:** modelos são apenas benchmark offline.
- **OK:** arquitetura champion/challenger está clara e segura.

Recomendação esperada:

A UI não deveria exibir LightGBM e CatBoost como simples toggles independentes sem explicar o papel de cada um.

Propor arquitetura ideal:

```text
ML Engine Mode:
- XGBoost only
- LightGBM only
- CatBoost only
- Benchmark all, production champion unchanged

Production Champion:
- XGBoost
- LightGBM
- CatBoost
```

Restrições:

- Apenas 1 champion ativo por profile/regime.
- Challengers não podem alterar regras.
- Challengers não podem aprovar/rejeitar trade.
- Challengers não podem alterar threshold de produção.
- Challengers só podem gerar benchmark e relatório.
- Promoção exige critérios mínimos.

---

# 7. Fase 3 — Auditoria Champion/Challenger

Verifique se existe uma política real de champion/challenger.

Audite:

- Onde o champion atual é salvo?
- Existe champion por profile?
- Existe champion global?
- Existe champion por regime?
- Existe champion por skill?
- Existe histórico de champion?
- Existe challenger?
- Existe promoção automática?
- Existe bloqueio de promoção automática?
- Quais métricas promovem modelo?
- Quais métricas rejeitam modelo?
- O Auto-Pilot respeita o champion?
- Profile Intelligence exibe o champion usado?

Critérios mínimos recomendados para promoção:

- precision no threshold operacional superior ao champion
- FPR dos perdedores inferior ao champion
- expected PnL superior ao champion
- win rate por profile superior ao champion
- estabilidade em walk-forward
- drawdown evitado superior
- volume mínimo de trades
- amostra mínima por profile/regime
- sem leakage
- sem overfit
- sem queda severa em período recente
- diferença estatisticamente relevante

Gerar diagnóstico:

- existe governança suficiente?
- quais campos faltam?
- quais tabelas faltam?
- quais endpoints faltam?
- quais travas faltam?

---

# 8. Fase 4 — Auditoria do Dataset e Risco de Mistura

Auditar dataset usado para treino.

Verificar:

1. O treino usa dataset global?
2. O treino usa dataset segregado por profile?
3. Existe dataset por:
   - source_layer
   - profile_name
   - strategy_skill
   - market_regime
   - symbol
   - model_type
   - run_id?
4. Existe risco de misturar trades de profiles diferentes?
5. Existe risco de misturar L1, L2 e L3 sem marcação clara?
6. Existe risco de treinar em dados rejected e approved sem outcome real?
7. Existe risco de duplicidade de trades?
8. Existe dedup por trade_id/symbol/entry_time/profile?
9. Existe versionamento de feature schema?
10. Existe controle de null rate por feature?
11. Existe controle de staleness de indicadores?
12. Existe separação temporal correta?
13. Existe vazamento de dados futuros?
14. Existem features proibidas entrando no treino?

Features proibidas ou suspeitas:

- outcome
- pnl_pct
- exit_price
- exit_time
- max_profit_pct pós-entrada
- max_drawdown_pct pós-entrada
- mfe_pct pós-entrada
- mae_pct pós-entrada
- delayed_tp
- price_after_1h
- price_after_2h
- price_after_4h
- price_after_12h
- price_after_24h
- future_return
- target_hit
- score gerado pelo próprio modelo
- direction se for derivado do futuro
- qualquer campo calculado após entrada

Verificar também:

- NaN está sendo preservado corretamente?
- NaN está virando zero?
- Indicadores ausentes estão gerando falsa informação?
- Existe flag macro_context_available?
- Existe staleness por indicador?
- Existe staleness por symbol?
- Existe quarentena para indicador vencido?

Entregar:

- mapa de features permitidas
- mapa de features proibidas
- mapa de features com risco
- null rate por feature
- staleness por feature
- cobertura por profile
- cobertura por regime
- recomendação de feature store segura

---

# 9. Fase 5 — Auditoria do Labeling

Verifique como o sistema define WIN/LOSS.

Contexto desejado:

- WIN: trade atinge aproximadamente +1%
- LOSS: trade atinge aproximadamente -1%
- O objetivo é prever direção favorável para +1% e evitar -1%
- Spread_pct não deve dominar o modelo como proxy indevido
- O sistema não deve aprender simplesmente o score antigo ou a decisão antiga

Auditar:

1. Qual label atual é usado?
2. O label é binary?
3. Existe multi-class?
4. Existe time-to-target?
5. Existe timeout?
6. Existe SL antes de TP?
7. Existe TP antes de SL?
8. Existe ordem correta de eventos?
9. Existe candle-level path ou apenas preço final?
10. Existe label por profile?
11. Existe label por regime?
12. Existe label por skill?
13. Existe diferença entre simulado e real?
14. O Shadow Portfolio usa a mesma definição do trainer?
15. O Auto-Pilot usa a mesma definição do Profile Intelligence?

Entregar:

- definição atual do label
- inconsistências
- riscos
- proposta de label canônico
- proposta de tabela de outcome auditável

---

# 10. Fase 6 — Auditoria do Auto-Pilot

Auditar o Auto-Pilot em profundidade.

Verificar:

1. O Auto-Pilot apenas sugere ou aplica automaticamente?
2. Quais profiles ele pode alterar?
3. Quais campos ele pode alterar?
4. Ele altera:
   - signals?
   - block_rules?
   - scoring?
   - entry_triggers?
   - minimum_score?
   - thresholds?
   - weights?
5. Existe allowlist de campos editáveis?
6. Existe denylist de campos proibidos?
7. Existe validação antes de aplicar?
8. Existe simulação antes de aplicar?
9. Existe rollback?
10. Existe audit log?
11. Existe diff antes/depois?
12. Existe reason_code?
13. Existe source_model?
14. Existe source_run_id?
15. Existe confidence?
16. Existe expected_impact?
17. Existe evidence_count?
18. Existe janela temporal usada?
19. Existe status:
    - suggested
    - approved
    - applied
    - rejected
    - reverted
    - expired?
20. Existe cooldown para evitar mudança excessiva?
21. Existe proteção contra overfitting de regra?
22. Existe proteção contra regra muito restritiva?
23. Existe proteção contra vetos absolutos ruins?
24. Existe separação entre bloqueios de risco extremo e filtros estratégicos?

Regras desejadas:

- Vetos duros apenas para risco extremo.
- Critérios estratégicos devem ser adaptativos por regime/skill.
- Auto-Pilot não deve criar regra como RSI < 45 globalmente sem contexto.
- Auto-Pilot deve identificar se a oportunidade é:
  - Mean Reversion
  - Trend Following
  - Breakout
  - Scalping
  - Swing
  - AI Adaptive
- Cada skill pode ter lógica diferente.
- Uma regra boa para Mean Reversion pode ser ruim para Breakout.
- Uma regra boa para mercado lateral pode bloquear pumps em mercado tendencial.

Entregar:

- mapa completo do fluxo Auto-Pilot
- riscos críticos
- regras sem auditoria
- campos editáveis perigosos
- proposta de governança
- proposta de audit log
- proposta de rollback
- proposta de skill-aware autopilot

---

# 11. Fase 7 — Auditoria do Profile Intelligence

Auditar o recurso Profile Intelligence.

Verificar se o Overview apresenta:

- Profiles Analisados
- Trades Fechados
- Win Rate Base
- Melhor Profile
- Melhor Combinação
- Combinações
- Sugestões Pendentes
- Alta Confiança
- Total de Runs
- Status

Se não apresentar, identificar:

- backend não envia?
- frontend não renderiza?
- query não calcula?
- dados não existem?
- migration incompleta?
- endpoint errado?
- cache stale?
- erro de nomenclatura?

Auditar tabela “Indicadores com Melhor Performance”.

Ela deve informar:

- nome do indicador
- métrica
- direção da regra
- threshold/range
- win rate
- lift
- quantidade de trades
- confidence
- profile associado
- todos os profiles onde aparece
- camada onde deve ser aplicado:
  - signals
  - block_rules
  - scoring
  - entry_triggers
- status da sugestão
- botão de aplicar/ajustar em profile

Se um indicador aparece em múltiplos profiles, a UI deve listar todos, por exemplo:

```text
Indicador: RSI 45–60

Profiles associados:
- L3_META_CONTROLLED_BOUNCE_V1
- L3_TREND_FOLLOWING_V1
- L3_BREAKOUT_V1
```

Auditar se o botão de aplicar:

- sabe qual profile alterar
- sabe qual campo alterar
- mostra diff antes/depois
- exige confirmação
- registra audit log
- permite rollback
- impede alteração em produção sem aprovação
- evita duplicar regra já existente
- evita conflito com regra oposta
- informa origem da sugestão

---

# 12. Fase 8 — Auditoria de Dynamic Combinations

Auditar Dynamic Combinations.

Verificar:

1. Como combinações são geradas?
2. São combinações de buckets top-winners?
3. Existe mínimo de trades?
4. Existe lift mínimo?
5. Existe validação fora da amostra?
6. Existe penalização por múltiplos testes?
7. Existe risco de data snooping?
8. Existe risco de encontrar combinações espúrias?
9. Existe separação discovery/validation?
10. Existe estabilidade temporal?
11. Existe estabilidade por profile?
12. Existe estabilidade por regime?
13. Existe estabilidade por symbol cluster?
14. Existe rank por expected PnL e não apenas win rate?
15. Existe comparação contra win rate base?
16. Existe intervalo de confiança?
17. Existe controle de suporte mínimo?

Entregar:

- combinações confiáveis
- combinações suspeitas
- combinações com baixo suporte
- recomendação de filtros mínimos

Critérios mínimos sugeridos:

- n_trades mínimo por combinação
- validação temporal
- lift mínimo consistente
- não depender de apenas 1 símbolo
- não depender de apenas 1 dia
- não depender de apenas 1 regime
- não promover regra se reduzir demais o volume operacional

---

# 13. Fase 9 — Auditoria de Association Rules

Auditar Association Rules.

Verificar:

1. O que é considerado item?
2. O que é antecedente?
3. O que é consequente?
4. Consequente é WIN?
5. Consequente é LOSS?
6. Existe suporte mínimo?
7. Existe confidence?
8. Existe lift?
9. Existe conviction/leverage?
10. Existe separação WIN vs LOSS?
11. Existe validação temporal?
12. Existe risco de regras óbvias ou redundantes?
13. Existe risco de regras não acionáveis?
14. Existe mapeamento para ação:
    - adicionar signal
    - adicionar block
    - ajustar score
    - não agir?
15. Regras com co-ocorrência são diferenciadas de causalidade?

Entregar:

- regras úteis
- regras perigosas
- regras não acionáveis
- proposta de classificação

---

# 14. Fase 10 — Auditoria de Anthropic AI Explanations

Auditar Anthropic AI Explanations.

Verificar:

1. O recurso apenas explica ou também decide?
2. A IA externa tem acesso a dados sensíveis?
3. A IA externa recebe amostras suficientes?
4. A IA externa recebe dados sem vazamento futuro?
5. A IA externa pode criar sugestão aplicável?
6. Existe human approval?
7. Existe audit log?
8. Existe prompt version?
9. Existe resposta estruturada?
10. Existe reason_code?
11. Existe validação estatística da explicação?
12. Existe risco de a IA justificar correlação espúria?
13. Existe fallback quando quota acaba?
14. Existe rate limit?
15. Existe custo estimado?

Regra desejada:

Anthropic AI pode explicar e resumir, mas não deve ser fonte única de alteração automática de profile.

Entregar:

- papel atual da Anthropic AI
- risco
- proposta de limites
- prompt recomendado
- formato de output seguro

---

# 15. Fase 11 — Auditoria de Optuna Search

Auditar Optuna Search.

Verificar:

1. O que Optuna otimiza?
2. Quais parâmetros são otimizados?
3. Otimiza threshold?
4. Otimiza features?
5. Otimiza hiperparâmetros?
6. Otimiza regras?
7. Otimiza por AUC, F1, precision, expected PnL ou outra métrica?
8. Existe validação em holdout?
9. Existe nested validation?
10. Existe risco de overfitting no validation set?
11. Existe limite de trials?
12. Existe seed?
13. Existe reproducibilidade?
14. Existe registro de study_name/trial_id?
15. Existe promoção automática baseada em Optuna?
16. Existe bloqueio se val_auc for alto demais e test ruim?
17. Existe comparação walk-forward?

Recomendação:

Optuna não deve promover modelo apenas por AUC.

Para trading, priorizar:

- expected PnL
- precision no threshold
- FPR dos perdedores
- drawdown evitado
- estabilidade temporal
- volume operacional suficiente

---

# 16. Fase 12 — Auditoria de Métricas Operacionais

Auditar se o sistema usa métricas adequadas.

Métricas obrigatórias:

- base_win_rate
- model_win_rate
- precision
- recall
- F1
- AUC
- FPR
- TPR
- expected PnL
- average pnl
- median pnl
- MAE
- MFE
- drawdown
- max adverse excursion
- max favorable excursion
- win rate por profile
- win rate por regime
- win rate por symbol cluster
- approval rate
- reject rate
- false positive rate
- false negative rate
- trade count
- confidence interval
- lift vs base
- estabilidade walk-forward
- performance discovery vs validation
- performance live/shadow após sugestão

Auditar se a UI mostra apenas métricas bonitas ou se mostra risco real.

---

# 17. Fase 13 — Auditoria da UI/UX

Auditar a UI do Profile Intelligence e Auto-Pilot.

Verificar:

1. A UI deixa claro se LightGBM/CatBoost são:
   - motores de decisão
   - challengers
   - benchmarks
   - apenas explicações?
2. A UI permite ligar dois modelos sem explicar risco?
3. A UI mostra qual modelo está ativo?
4. A UI mostra qual modelo gerou cada sugestão?
5. A UI mostra run_id?
6. A UI mostra dataset usado?
7. A UI mostra período analisado?
8. A UI mostra amostra?
9. A UI mostra profile?
10. A UI mostra impacto esperado?
11. A UI mostra diff da alteração?
12. A UI mostra botão de rollback?
13. A UI diferencia:
    - sugestão pendente
    - aplicada
    - rejeitada
    - revertida
    - expirada?
14. A UI mostra warnings para combinações com baixa amostra?
15. A UI mostra warning quando biblioteca LightGBM/CatBoost não está instalada?
16. A UI mostra warning quando Anthropic quota acabou?
17. A UI mostra warning quando Optuna é pesado e aumenta tempo?

Propor melhorias visuais e funcionais.

---

# 18. Fase 14 — Consultas SQL de Auditoria

Crie e execute, quando possível, queries SQL para responder:

1. Quantos profiles existem?
2. Quais profiles têm scoring_rules?
3. Quais profiles têm block_rules?
4. Quais profiles têm entry_triggers?
5. Quais profiles foram alterados pelo Auto-Pilot?
6. Quais sugestões estão pendentes?
7. Quais sugestões foram aplicadas?
8. Quais sugestões foram revertidas?
9. Qual model_type gerou cada sugestão?
10. Existem sugestões sem model_type?
11. Existem sugestões sem run_id?
12. Existem sugestões sem profile_name?
13. Existem sugestões sem confidence?
14. Existem sugestões sem evidence_count?
15. Existem sugestões sem expected_impact?
16. Existem runs com LightGBM?
17. Existem runs com CatBoost?
18. Existem runs com XGBoost?
19. Existem dois champions ativos ao mesmo tempo?
20. Existem champions sem profile?
21. Existem trades sem profile_name?
22. Existem trades sem source_layer?
23. Existem trades sem outcome?
24. Existem trades duplicados?
25. Existem features com null rate alto?
26. Existem features com staleness alto?
27. Existem features proibidas no treino?
28. Existem datasets misturando profiles?
29. Existe score usado como feature?
30. Existe direction usado como feature?

Para cada query, entregar:

- SQL
- objetivo
- resultado
- interpretação
- risco

---

# 19. Fase 15 — Testes de Segurança e Consistência

Criar testes para garantir:

1. Apenas um champion ativo por profile/regime.
2. Challenger não altera regra.
3. Benchmark não altera decisão.
4. Sugestão precisa de source_model.
5. Sugestão precisa de run_id.
6. Sugestão precisa de profile_name.
7. Sugestão precisa de evidence_count.
8. Sugestão precisa de diff antes/depois.
9. Auto-Pilot não aplica alteração sem audit log.
10. Auto-Pilot não aplica alteração em campo proibido.
11. Auto-Pilot não cria regra duplicada.
12. Auto-Pilot não cria regra conflitante.
13. Profile Intelligence não exibe recomendação sem amostra mínima.
14. Dynamic Combination sem validação não pode virar regra.
15. Association Rule sem lift mínimo não pode virar regra.
16. Anthropic AI não pode aplicar regra sozinha.
17. Optuna não pode promover modelo sem holdout.
18. Features proibidas não entram no treino.
19. NaN não é convertido indevidamente em zero.
20. Dataset por profile não mistura dados de outro profile.

---

# 20. Fase 16 — Classificação de Riscos

Classifique cada achado como:

## CRÍTICO

- pode causar decisão operacional errada
- pode causar Auto-Pilot alterar produção incorretamente
- pode misturar modelos sem governança
- pode causar leakage
- pode causar overfitting não detectado
- pode promover modelo ruim
- pode corromper profile

## ALTO

- gera recomendação sem rastreabilidade
- UI induz usuário a erro
- métrica incompleta
- ausência de rollback
- ausência de diff
- ausência de source_model/source_run

## MÉDIO

- funcionalidade parcial
- falta de status
- falta de indicador visual
- falta de validação de amostra
- falta de filtro de baixa confiança

## BAIXO

- melhoria de nomenclatura
- melhoria de layout
- melhoria de tooltip
- melhoria de agrupamento

## OK

- funcionamento correto, validado com evidência

---

# 21. Fase 17 — Plano de Correção

Depois da auditoria, proponha plano de correção dividido em:

1. Correções críticas imediatas
2. Correções de governança ML
3. Correções do Auto-Pilot
4. Correções do Profile Intelligence
5. Correções de UI/UX
6. Correções de banco/migrations
7. Correções de testes
8. Melhorias futuras

Para cada item, informar:

- problema
- risco
- arquivo/tabela afetado
- solução proposta
- prioridade
- esforço estimado
- impacto esperado
- se precisa migration
- se precisa retrain
- se precisa invalidar cache
- se precisa rollback

---

# 22. Arquitetura Recomendada a Validar

Validar se o sistema deveria ser reorganizado para este modelo.

## 22.1 Model Registry

Tabela ou estrutura contendo:

- model_id
- model_type: xgboost/lightgbm/catboost
- model_version
- profile_name
- strategy_skill
- market_regime
- dataset_version
- feature_schema_version
- train_start
- train_end
- validation_start
- validation_end
- test_start
- test_end
- metrics_json
- threshold
- status: candidate/challenger/champion/rejected/archived
- promoted_at
- promoted_by
- rejection_reason
- artifact_path

## 22.2 Production Champion Control

Tabela ou estrutura contendo:

- profile_name
- market_regime
- strategy_skill
- active_model_id
- active_model_type
- active_threshold
- activated_at
- activated_by
- previous_model_id
- rollback_available

Regra:

Não pode haver dois `active_model_id` para o mesmo `profile_name + market_regime + strategy_skill`.

## 22.3 Suggestion Registry

Cada sugestão precisa conter:

- suggestion_id
- source_type: profile_intelligence/autopilot/ml/association/dynamic_combination/anthropic/manual
- source_model_type
- source_model_id
- source_run_id
- profile_name
- target_section: signals/block_rules/scoring/entry_triggers/minimum_score
- target_field
- current_value
- proposed_value
- diff_json
- confidence
- lift
- evidence_count
- expected_impact
- risk_level
- validation_status
- status: pending/applied/rejected/reverted/expired
- created_at
- applied_at
- reverted_at
- reason
- rollback_payload

## 22.4 Auto-Pilot Audit Log

Cada ação precisa conter:

- action_id
- profile_name
- action_type
- target_section
- before_json
- after_json
- diff_json
- source_suggestion_id
- source_model_id
- source_run_id
- reason_code
- confidence
- applied_by
- applied_at
- rollback_status

## 22.5 Profile Intelligence Overview

A API deve retornar:

- profiles_analyzed
- closed_trades
- base_win_rate
- best_profile
- best_combination
- combinations_count
- pending_suggestions
- high_confidence_suggestions
- total_runs
- latest_run_status
- latest_run_started_at
- latest_run_finished_at
- models_used
- active_champion
- challenger_results

---

# 23. Resultado Final Esperado

Entregar um relatório final em Markdown com esta estrutura:

```markdown
# Auditoria ML + Auto-Pilot + Profile Intelligence — Scalpyn

## 1. Sumário Executivo
- Veredito geral
- Principais riscos
- Se LightGBM/CatBoost estão corretos ou perigosos
- Se Auto-Pilot está seguro ou não
- Se Profile Intelligence está confiável ou incompleto

## 2. Achados Críticos
Tabela:
- ID
- Severidade
- Área
- Problema
- Evidência
- Impacto
- Correção recomendada

## 3. Inventário Técnico
Tabela completa de arquivos, funções, endpoints, tabelas e jobs.

## 4. Auditoria dos Modelos
- XGBoost
- LightGBM
- CatBoost
- Champion/Challenger
- Risco de conflito
- Risco de decisão múltipla

## 5. Auditoria do Dataset
- origem dos dados
- segregação por profile
- risco de mistura
- leakage
- NaN
- staleness
- cobertura

## 6. Auditoria do Labeling
- definição atual
- inconsistências
- proposta canônica

## 7. Auditoria do Auto-Pilot
- fluxo atual
- pontos seguros
- pontos perigosos
- ausência de audit log
- ausência de rollback
- plano de correção

## 8. Auditoria do Profile Intelligence
- Overview
- Indicadores com Melhor Performance
- Combinações
- Association Rules
- Anthropic AI
- Optuna
- LightGBM/CatBoost
- problemas de UI/API

## 9. Queries SQL Executadas
Para cada query:
- SQL
- resultado
- interpretação

## 10. Testes Recomendados
- testes unitários
- testes de integração
- testes de banco
- testes de UI
- testes de ML governance

## 11. Plano de Correção
Separar em:
- imediato
- curto prazo
- médio prazo
- futuro

## 12. Veredito Final
Responder objetivamente:
- É erro ter LightGBM e CatBoost no mesmo sistema?
- É erro permitir ambos ligados ao mesmo tempo?
- O sistema atual tem governança suficiente?
- O Auto-Pilot pode aplicar regras com segurança?
- O Profile Intelligence está pronto para orientar produção?
- O que deve ser bloqueado imediatamente?
- O que deve ser mantido?
- O que deve ser refeito?
```

---

# 24. Veredito Esperado Sobre LightGBM e CatBoost

Use este princípio como referência:

Não é erro ter XGBoost, LightGBM e CatBoost no mesmo sistema.

É erro permitir que múltiplos modelos influenciem decisão operacional, Auto-Pilot ou alteração de profile ao mesmo tempo sem:

- champion explícito
- challenger explícito
- source_model
- source_run_id
- dataset_version
- feature_schema_version
- threshold
- métricas operacionais
- audit log
- diff
- rollback
- validação fora da amostra

A recomendação provável é:

- Manter XGBoost como champion atual se for o modelo ativo.
- Permitir LightGBM como challenger principal para dados numéricos.
- Deixar CatBoost como challenger específico para fase com forte uso de variáveis categóricas como profile_name, regime, skill, symbol_cluster e session.
- Substituir toggles independentes por um controle de modo:

```text
ML Engine Mode:
- XGBoost only
- LightGBM only
- CatBoost only
- Benchmark all

Production Champion:
- XGBoost
- LightGBM
- CatBoost
```

Com trava:

```text
Apenas um modelo pode ser production_decision_engine por profile/regime/skill.
```

---

# 25. Entregáveis Finais

Ao final, gere:

1. Relatório técnico
2. Checklist de correção
3. Lista de migrations necessárias
4. Lista de testes necessários
5. Proposta de nova arquitetura segura
6. Recomendação objetiva do que desligar imediatamente

---

# 26. Instrução Final de Execução

Execute a auditoria em modo **read-only** primeiro.

Não implemente correções no mesmo ciclo.

Depois da entrega do relatório, aguarde autorização para criar um segundo plano/prompt de correção controlada.
