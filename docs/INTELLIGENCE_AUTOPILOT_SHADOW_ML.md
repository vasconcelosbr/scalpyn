# PROMPT — Auditoria Ampla do Profile Intelligence, Auto-Pilot, Shadow Portfolio e Módulos ML

Você é um auditor técnico sênior especialista em sistemas de trading quantitativo, FastAPI/Python, PostgreSQL, React/Next.js, jobs assíncronos, workers, Railway, Shadow Portfolio, ML tabular, XGBoost, LightGBM, CatBoost, Optuna, Association Rules, Anthropic AI, Auto-Pilot e trilhas de auditoria imutáveis.

Preciso que você realize uma **auditoria ampla, detalhada e read-only** no sistema Scalpyn, com foco no módulo **Profile Intelligence** e seus recursos relacionados.

O objetivo é encontrar falhas, bugs, inconsistências, erros de log, problemas de integração, falhas de execução e causas raiz para os comportamentos atuais.

---

# 1. Contexto atual

O módulo **Profile Intelligence** foi implementado com os seguintes recursos:

- **Dynamic Combinations**: gera combinações dinâmicas de buckets top-winners.
- **Association Rules**: usa `mlxtend apriori` para encontrar co-ocorrências.
- **Anthropic AI Explanations**: gera explicações e recomendações assistidas por IA.
- **Optuna Search**: busca thresholds e combinações otimizadas.
- **LightGBM**: deve estar implementado como modelo/challenger funcional.
- **CatBoost**: deve estar implementado como modelo/challenger funcional.
- **Auto-Pilot**: deve calibrar clones versionados, testar candidatos em Shadow, promover somente com evidência válida e executar rollback por degradação.

Além disso, existem recursos relacionados:

- Shadow Portfolio;
- Watchlists criadas automaticamente pelo Profile Intelligence;
- Strategy Profiles;
- Audit Trail Log imutável;
- criação automática de profiles;
- ajuste automático de indicadores;
- recomendações aplicáveis;
- aprovação/promoção/rollback de candidatos;
- coleta e exibição de trades simulados.

---

# 2. Problemas observados

## 2.1 Auto-Pilot aparenta não executar novo ciclo

Na interface aparece a mensagem:

```text
Auto-Pilot global ligado
Calibra clones versionados, testa candidatos em Shadow, promove somente com evidência válida e executa rollback por degradação.
Profiles originais não são alterados.
Último ciclo: 18/06/26, 23:52 · COMPLETED
```

Mesmo após clicar em **Executar ciclo**, o último ciclo continua aparecendo como:

```text
18/06/26, 23:52 · COMPLETED
```

Auditar se:

- o botão está chamando o endpoint correto;
- o endpoint está recebendo o request;
- o backend está iniciando o ciclo;
- o ciclo está sendo bloqueado por alguma condição;
- o job está sendo enfileirado;
- o worker está processando;
- o run está sendo persistido;
- a UI está usando cache/stale state;
- o timestamp do último ciclo não está sendo atualizado;
- a resposta do endpoint está sendo ignorada;
- existe erro silencioso;
- o scheduler está travado;
- o Auto-Pilot global está apenas “ligado” visualmente, mas não executando.

---

## 2.2 Shadow Portfolio não apresenta trades simulados de todas as watchlists criadas automaticamente

O Shadow Portfolio não apresenta os trades simulados de todas as watchlists/profiles criados automaticamente pelo modelo Profile Intelligence.

Auditar se:

- as watchlists automáticas foram criadas corretamente;
- cada watchlist tem `profile_id`;
- cada profile criado tem associação com watchlist;
- o Shadow Portfolio está gerando trades para essas watchlists;
- os trades estão sendo gravados em `shadow_trades`;
- os trades têm `source`, `profile_id`, `watchlist_id`, `profile_name`, `strategy_skill` e `origin`;
- a UI está filtrando incorretamente;
- os jobs de coleta/simulação estão olhando apenas watchlists manuais;
- o pipeline ignora watchlists criadas pelo Profile Intelligence;
- existem trades sem `profile_id`;
- existem trades com `profile_id`, mas fora do filtro da tela;
- existem status `RUNNING`, `CLOSED`, `TIMEOUT`, `TP_HIT`, `SL_HIT`;
- o endpoint do Shadow Portfolio retorna todos os profiles/watchlists;
- há diferença entre dados no banco e dados exibidos.

---

## 2.3 Audit Trail Log imutável insuficiente

O Audit Trail Log imutável de eventos do Profile Intelligence Engine precisa apresentar detalhamento das ações realizadas pelo Auto-Pilot.

Ele deve mostrar:

- criação de profiles;
- criação de watchlists;
- criação de candidatos shadow;
- ajustes de indicadores;
- ajustes em scoring;
- ajustes em block rules;
- ajustes em signals;
- ajustes em entry triggers;
- valor antes da alteração;
- valor depois da alteração;
- diff;
- nome do profile criado;
- nome do profile modificado;
- origem da recomendação;
- módulo que gerou a recomendação;
- run_id;
- model_id, quando aplicável;
- source_type;
- validation_status;
- actionability_status;
- aprovação humana;
- ativação live;
- rollback;
- motivo do rollback;
- degradação detectada;
- usuário/ator responsável;
- timestamp;
- payload completo.

Auditar se o audit log atual possui granularidade suficiente ou se está registrando apenas eventos genéricos.

---

## 2.4 Garantia de funcionalidade real dos módulos

Preciso ter garantia de que estes recursos estão realmente funcionais e adequados:

```text
Dynamic Combinations
Association Rules
Anthropic AI Explanations
Optuna Search
LightGBM
CatBoost
Auto-Pilot
Shadow Portfolio
Audit Trail
Profile/Watchlist creation
Suggestion application
Rollback
```

Auditar se cada recurso está:

- instalado;
- configurado;
- com dependências corretas;
- integrado ao backend;
- integrado aos jobs/workers;
- integrado ao banco;
- integrado à UI;
- gerando dados;
- gerando logs;
- gerando eventos de auditoria;
- gerando outputs reais;
- sendo testado;
- sendo exibido corretamente;
- sem código morto;
- sem flags órfãs;
- sem falso positivo visual;
- sem execução parcial.

---

# 3. Regras da auditoria

Executar primeiro em modo **read-only**.

Não fazer neste primeiro ciclo:

- não alterar produção;
- não aplicar migrations;
- não apagar dados;
- não reprocessar histórico em massa;
- não promover candidato para live;
- não alterar profiles;
- não alterar watchlists;
- não alterar thresholds;
- não re-treinar modelos;
- não executar correções automáticas;
- não mudar configurações de Auto-Pilot;
- não limpar banco;
- não modificar código sem autorização.

Este prompt é para **diagnóstico e relatório de causa raiz**.

Somente depois do relatório será criado um segundo prompt de correção controlada.

---

# 4. Objetivos principais da auditoria

Responder objetivamente:

1. O botão **Executar ciclo** do Auto-Pilot está chamando o endpoint correto?
2. O endpoint executa o ciclo ou apenas retorna estado anterior?
3. O Auto-Pilot global está realmente rodando?
4. O ciclo está sendo bloqueado por validação, amostra insuficiente, aprovação pendente, status, lock ou erro?
5. O timestamp `Último ciclo` está vindo do banco, cache, estado local ou endpoint errado?
6. Existe job/worker processando ciclos do Auto-Pilot?
7. Existe fila travada?
8. Existem logs de erro no Railway/backend/worker?
9. Existem eventos de auditoria para o clique em Executar ciclo?
10. Existem runs novos após o clique?
11. As watchlists automáticas estão sendo criadas?
12. As watchlists automáticas estão sendo usadas pelo Shadow Portfolio?
13. Existem trades simulados para cada watchlist automática?
14. Se existem no banco, por que não aparecem na UI?
15. Se não existem no banco, por que o simulador não está criando?
16. O Audit Trail registra ações detalhadas do Auto-Pilot?
17. O Audit Trail registra before/after/diff?
18. O Audit Trail registra nome do profile criado/modificado?
19. O Audit Trail registra origem da recomendação?
20. Dynamic Combinations está funcional de ponta a ponta?
21. Association Rules está funcional de ponta a ponta?
22. Anthropic AI está funcional de ponta a ponta?
23. Optuna está funcional de ponta a ponta?
24. LightGBM está funcional de ponta a ponta?
25. CatBoost está funcional de ponta a ponta?
26. Auto-Pilot está funcional de ponta a ponta?
27. Existe algum recurso apenas visual, sem execução real?
28. Existem falhas de integração entre Profile Intelligence e Shadow Portfolio?
29. Existem falhas de integração entre Profile Intelligence e Strategy Profiles?
30. Existem falhas de integração entre Profile Intelligence e Watchlists?

---

# 5. Escopo técnico obrigatório

Auditar no mínimo:

## 5.1 Backend

Arquivos e serviços relacionados a:

```text
profile_intelligence
profile_intelligence_autopilot
profile_suggestion
profile_create
counterfactual_combination
association_rules
optuna_profile_search
anthropic explanations
lightgbm
catboost
shadow portfolio
watchlist
strategy profiles
audit logs
workers
schedulers
```

Buscar arquivos como:

```text
backend/app/api/profile_intelligence.py
backend/app/services/profile_intelligence_service.py
backend/app/services/profile_intelligence_autopilot_service.py
backend/app/services/profile_suggestion_service.py
backend/app/services/profile_create_service.py
backend/app/services/counterfactual_combination_service.py
backend/app/services/association_rules_service.py
backend/app/services/optuna_profile_search_service.py
backend/app/services/profile_ai_explanation_service.py
backend/app/services/shadow_portfolio*
backend/app/services/watchlist*
backend/app/models/profile_intelligence*
backend/app/models/shadow*
backend/app/models/watchlist*
backend/app/tasks/*
backend/app/workers/*
```

## 5.2 Frontend

Auditar:

```text
frontend/app/profile-intelligence/page.tsx
frontend/app/shadow-portfolio/*
frontend/app/profiles/*
frontend/components/*
```

Foco:

- botão Executar ciclo;
- chamada de API;
- loading state;
- toast/error;
- refresh de dados;
- cache;
- query key;
- timestamp do último ciclo;
- lista de candidatos;
- lista de watchlists automáticas;
- exibição do Shadow Portfolio;
- filtros aplicados;
- audit trail;
- status dos módulos.

## 5.3 Banco de Dados

Auditar tabelas relacionadas a:

```text
profile_intelligence_runs
profile_intelligence_events
profile_intelligence_audit
profile_intelligence_suggestions
profile_intelligence_candidates
profile_intelligence_combinations
profile_intelligence_indicator_stats
profile_intelligence_association_rules
profile_intelligence_optuna_results
profile_intelligence_autopilot_candidates
profile_intelligence_autopilot_events
config_profiles
strategy_profiles
watchlists
watchlist_symbols
shadow_trades
shadow_portfolio
ml_models
model_registry
audit_logs
autopilot_audit
```

Se o nome real das tabelas for diferente, mapear corretamente.

## 5.4 Jobs, Workers e Scheduler

Auditar:

- jobs de Profile Intelligence;
- jobs de Auto-Pilot;
- jobs de Shadow Portfolio;
- jobs de coleta de indicadores;
- jobs de fechamento de trades simulados;
- jobs de criação de watchlists;
- filas;
- locks;
- cron;
- Celery/RQ/worker equivalente;
- Railway logs;
- Cloud Run logs, se aplicável.

---

# 6. Auditoria específica do Auto-Pilot

## 6.1 Botão Executar ciclo

Verificar no frontend:

- função chamada ao clicar;
- endpoint chamado;
- método HTTP;
- payload enviado;
- tratamento da resposta;
- invalidação/refetch da query;
- atualização do timestamp;
- cache;
- erros ocultos;
- toast;
- loading state.

Verificar no backend:

- endpoint existe?
- endpoint exige autenticação?
- endpoint recebe `user_id`?
- endpoint executa síncrono ou agenda job?
- retorna run_id?
- retorna status?
- retorna erro?
- registra audit event?
- atualiza `last_cycle_at`?
- atualiza `last_run_status`?

## 6.2 Causa raiz possível do timestamp travado

Investigar hipóteses:

```text
endpoint não chamado
endpoint chamado mas retorna cache
endpoint chamado mas job não inicia
job inicia mas falha antes de persistir run
job executa mas não atualiza latest cycle
UI consulta endpoint errado
UI usa estado antigo
UI não refaz fetch após clique
backend usa data do último completed e ignora running/failed
timezone/format inválido
autopilot está lockado por ciclo anterior
status COMPLETED antigo está sendo reaproveitado
worker não está rodando
fila sem consumidor
erro silencioso
```

## 6.3 Evidências obrigatórias

Entregar:

- request do botão;
- endpoint chamado;
- resposta do backend;
- run_id gerado ou ausência dele;
- query que busca último ciclo;
- logs do backend;
- logs do worker;
- eventos de audit;
- registros no banco antes/depois do clique, se possível.

---

# 7. Auditoria específica do Shadow Portfolio

Verificar por que os trades simulados de todas as watchlists automáticas não aparecem.

## 7.1 Mapear watchlists automáticas

Listar:

```text
watchlist_id
watchlist_name
created_by
created_by_module
source
profile_id
profile_name
candidate_id
origin_run_id
created_at
status
symbols_count
```

## 7.2 Verificar se geram shadow trades

Para cada watchlist automática:

```text
watchlist_id
profile_id
total_shadow_trades
running_trades
closed_trades
tp_hit
sl_hit
timeout
last_trade_at
last_closed_at
```

## 7.3 Verificar filtros da UI

Auditar se a UI filtra por:

```text
source = L3
source = L3_SIMULATED
source = L3_LAB
profile_id IS NOT NULL
watchlist_id
status
created_by
is_active
only_manual
only_live
only_default
```

## 7.4 Causas prováveis

Investigar:

```text
watchlist criada sem symbols
watchlist criada sem profile_id
watchlist não vinculada ao Shadow Portfolio
shadow job ignora watchlists auto-generated
shadow job filtra apenas watchlists live
shadow job filtra apenas profiles originais
trades gravados sem watchlist_id
trades gravados sem profile_id
UI endpoint não retorna watchlists automáticas
UI cache não atualiza
status da watchlist impede simulação
profile clone em shadow não está elegível
```

---

# 8. Auditoria específica do Audit Trail

O Audit Trail Log imutável precisa detalhar ações do Profile Intelligence Engine e Auto-Pilot.

Verificar se registra:

## 8.1 Criação de profiles

Campos obrigatórios:

```text
event_type = PROFILE_CREATED
profile_id
profile_name
source_run_id
source_suggestion_id
source_combination_id
created_by_module
before_json = null
after_json
diff_json
reason
timestamp
```

## 8.2 Criação de watchlists

Campos obrigatórios:

```text
event_type = WATCHLIST_CREATED
watchlist_id
watchlist_name
profile_id
profile_name
symbols_count
symbols
source_run_id
created_by_module
after_json
timestamp
```

## 8.3 Ajustes em indicadores/regras

Campos obrigatórios:

```text
event_type = PROFILE_RULE_UPDATED
profile_id
profile_name
target_section
target_field
indicator_name
old_value
new_value
before_json
after_json
diff_json
source_type
source_run_id
validation_status
actionability_status
confidence
evidence_count
expected_impact
timestamp
```

## 8.4 Aprovação, ativação e rollback

Campos obrigatórios:

```text
CANDIDATE_CREATED
PENDING_HUMAN_APPROVAL
APPROVED_FOR_LIVE
LIVE_ACTIVATED
CANDIDATE_ROLLED_BACK
ROLLBACK_TRIGGERED_BY_DEGRADATION
```

Cada evento deve conter:

```text
candidate_id
incumbent_profile_id
candidate_profile_id
approved_by
approved_at
rollback_payload
before_json
after_json
diff_json
mutation_applied
reason
```

## 8.5 Diagnóstico

Responder:

- existe audit trail imutável real?
- ou apenas logs operacionais?
- há trigger append-only?
- eventos podem ser editados?
- há before/after/diff?
- há profile_name?
- há source_run_id?
- há actor?
- há missing fields?
- eventos estão sendo gravados para todas as ações?
- quais ações não são auditadas?

---

# 9. Auditoria de funcionalidade dos módulos

Para cada módulo abaixo, entregar status:

```text
NOT_IMPLEMENTED
PARTIAL
CONFIGURED_BUT_NOT_RUNNING
RUNNING_WITH_ERRORS
RUNNING_OK
UI_ONLY
BACKEND_ONLY
BROKEN_INTEGRATION
```

## 9.1 Dynamic Combinations

Verificar:

- gera combinações?
- usa top-winners buckets?
- usa discovery/validation?
- persiste resultados?
- gera sugestões?
- audit trail registra?
- UI mostra?
- bloqueia sem validation?
- há erros?

## 9.2 Association Rules

Verificar:

- `mlxtend` está instalado?
- apriori está sendo chamado?
- fallback existe?
- regras são geradas?
- regras WIN/LOSS são separadas?
- regras são validadas?
- regras aparecem na UI?
- regras geram sugestões?
- audit trail registra?

## 9.3 Anthropic AI Explanations

Verificar:

- API key existe?
- chamadas são feitas?
- há rate limit?
- há fallback?
- prompts são versionados?
- respostas são persistidas?
- erros são auditados?
- custo é registrado?
- explicação influencia sugestão ou apenas descreve?

## 9.4 Optuna Search

Verificar:

- Optuna está instalado?
- TPE está sendo usado?
- trials são executados?
- discovery/validation são separados?
- resultados são persistidos?
- thresholds gerados aparecem na UI?
- resultados podem virar sugestão?
- overfit é bloqueado?

## 9.5 LightGBM

Verificar:

- dependência instalada?
- import funciona?
- trainer existe?
- predictor existe?
- lê dataset?
- treina?
- salva artifact?
- registra model_id?
- gera métricas?
- aparece no registry?
- gera sugestões?
- influencia Auto-Pilot?
- aparece na UI como funcional?
- há logs?

## 9.6 CatBoost

Verificar:

- dependência instalada?
- import funciona?
- trainer existe?
- predictor existe?
- lê dataset?
- treina?
- salva artifact?
- registra model_id?
- gera métricas?
- aparece no registry?
- gera sugestões?
- influencia Auto-Pilot?
- aparece na UI como funcional?
- há logs?

## 9.7 Auto-Pilot

Verificar:

- global está realmente ligado?
- ciclo manual executa?
- ciclo agendado executa?
- cria candidatos?
- testa em shadow?
- promove apenas com aprovação?
- rollback por degradação funciona?
- audit trail registra tudo?
- profiles originais realmente não são alterados?
- clones versionados são criados?
- há lock impedindo execução?
- há erro silencioso?

---

# 10. Consultas SQL obrigatórias

Criar SQL read-only para responder:

## 10.1 Auto-Pilot cycles

```sql
-- listar últimos ciclos/runs
-- status, started_at, finished_at, error, triggered_by, run_id
```

## 10.2 Eventos de clique manual

```sql
-- eventos relacionados a execução manual
-- API trigger, cycle started, cycle completed, cycle failed, blocked
```

## 10.3 Candidatos

```sql
-- candidatos por estado
-- SHADOW_COLLECTING, SHADOW_READY, PENDING_HUMAN_APPROVAL,
-- APPROVED_FOR_LIVE, LIVE_ACTIVATED, REJECTED, ROLLED_BACK
```

## 10.4 Watchlists automáticas

```sql
-- watchlists criadas por Profile Intelligence / Auto-Pilot
-- profile_id, symbols_count, status
```

## 10.5 Shadow trades por watchlist

```sql
-- total de shadow_trades por watchlist/profile
-- running/closed/tp/sl/timeout
```

## 10.6 Profiles criados

```sql
-- profiles criados pelo Profile Intelligence
-- clones, versões, origem, candidato, run
```

## 10.7 Audit trail coverage

```sql
-- eventos sem before_json
-- eventos sem after_json
-- eventos sem diff_json
-- eventos sem profile_id
-- eventos sem profile_name
-- eventos sem source_run_id
-- eventos sem actor
```

## 10.8 Módulos

```sql
-- Dynamic Combinations
-- Association Rules
-- Optuna
-- Anthropic
-- LightGBM
-- CatBoost
-- sugestões por source_type
-- runs por module
-- errors por module
```

---

# 11. Logs obrigatórios

Buscar nos logs:

```text
profile intelligence
autopilot cycle
execute cycle
manual cycle
shadow
candidate
watchlist
lightgbm
catboost
optuna
association
anthropic
apriori
rollback
audit
error
exception
failed
timeout
blocked
lock
queue
worker
```

Entregar:

- erros encontrados;
- stack traces;
- warnings;
- endpoints chamados;
- jobs enfileirados;
- jobs ignorados;
- locks ativos;
- ciclos bloqueados;
- falhas de permissão;
- falhas de validação;
- falhas de import;
- falhas de dependência.

---

# 12. Resultado final esperado

Entregar relatório em Markdown com esta estrutura:

```markdown
# Auditoria Ampla — Profile Intelligence, Auto-Pilot, Shadow Portfolio e Módulos ML

## 1. Sumário Executivo
- Veredito geral
- Causa raiz provável
- Riscos críticos
- Recursos funcionais
- Recursos parciais
- Recursos quebrados
- Prioridade de correção

## 2. Causa Raiz do Auto-Pilot não atualizar Último Ciclo
- Frontend
- Backend
- Worker
- Banco
- Logs
- Evidências
- Correção recomendada

## 3. Causa Raiz do Shadow Portfolio não exibir trades das watchlists automáticas
- Watchlists encontradas
- Profiles associados
- Trades encontrados
- Filtros da UI
- Jobs de simulação
- Evidências
- Correção recomendada

## 4. Audit Trail Log
- Eventos existentes
- Eventos ausentes
- Campos ausentes
- before/after/diff
- profile_name
- source_run_id
- actor
- imutabilidade
- correção recomendada

## 5. Status dos Módulos
| Módulo | Status | Evidência | Falha | Correção |
|---|---|---|---|---|
| Dynamic Combinations | | | | |
| Association Rules | | | | |
| Anthropic AI | | | | |
| Optuna | | | | |
| LightGBM | | | | |
| CatBoost | | | | |
| Auto-Pilot | | | | |
| Shadow Portfolio | | | | |

## 6. Queries SQL Executadas
Para cada query:
- objetivo
- SQL
- resultado
- interpretação

## 7. Logs Encontrados
- backend
- worker
- scheduler
- frontend/API
- erros
- warnings

## 8. Bugs e Inconsistências
Tabela:
| ID | Severidade | Área | Problema | Evidência | Impacto | Correção recomendada |
|---|---|---|---|---|---|---|

## 9. Testes Recomendados
- unitários
- integração
- e2e
- banco
- worker
- UI

## 10. Plano de Correção
Separar em:
- P0 imediato
- P1 curto prazo
- P2 estrutural
- P3 melhoria

## 11. Veredito Final
Responder:
- Auto-Pilot está executando de verdade?
- Por que o último ciclo não atualiza?
- Shadow Portfolio está recebendo todas as watchlists automáticas?
- Audit Trail está completo?
- Dynamic Combinations está funcional?
- Association Rules está funcional?
- Anthropic está funcional?
- Optuna está funcional?
- LightGBM está funcional?
- CatBoost está funcional?
- Auto-Pilot está funcional?
- O sistema está pronto para produção?
```

---

# 13. Classificação de severidade

Classificar achados como:

## CRÍTICO

- botão executa, mas nada roda;
- Auto-Pilot não gera ciclo;
- shadow não gera trades;
- módulo aparece funcional mas não executa;
- audit trail não registra mutações;
- ações são aplicadas sem before/after/diff;
- LightGBM/CatBoost aparecem funcionais mas não treinam/inferem;
- watchlists automáticas não entram no Shadow Portfolio.

## ALTO

- UI mostra estado stale;
- logs insuficientes;
- eventos sem profile_name;
- eventos sem source_run_id;
- sugestões sem origem;
- worker com falha intermitente;
- filtros escondem dados existentes.

## MÉDIO

- tooltip incompleto;
- métrica não exibida;
- status pouco claro;
- falta de teste e2e.

## BAIXO

- layout;
- nomenclatura;
- agrupamento visual.

---

# 14. Entregáveis adicionais

Além do relatório, entregar:

1. Checklist de verificação funcional por módulo.
2. SQL read-only usado na auditoria.
3. Lista de endpoints testados.
4. Lista de logs analisados.
5. Mapa de fluxo real:

```text
UI button
→ API endpoint
→ backend service
→ job/worker
→ database run/event
→ shadow/candidate/watchlist
→ UI refresh
```

6. Plano de correção separado em prompts futuros.

---

# 15. Veredito esperado

O relatório deve deixar claro:

```text
O que está realmente funcionando.
O que está parcialmente implementado.
O que é apenas visual.
O que está quebrado.
O que não executa.
O que executa mas não aparece.
O que aparece mas não existe no banco.
O que existe no banco mas a UI filtra.
O que o Auto-Pilot fez de fato.
O que o Auto-Pilot deveria ter feito.
Por que o ciclo não atualiza.
Por que o Shadow Portfolio não mostra todos os trades.
Quais módulos ML realmente leem dados, treinam, inferem e geram outputs.
```

Não fazer correções neste ciclo. Apenas auditar, provar, classificar e recomendar.
