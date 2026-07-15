# PROMPT ÚNICO — Auditoria L3: LightGBM/CatBoost, Profile Intelligence, Shadow Portfolio e Watchlists Automáticas

Você é um auditor técnico sênior especializado em trading quantitativo, ML Engineering, FastAPI/Python, PostgreSQL, React/Next.js, Shadow Portfolio, Profile Intelligence, LightGBM, CatBoost, Auto-Pilot, model registry, audit trail e sistemas de calibração automática de estratégias.

Realize uma **auditoria ampla, read-only e orientada à causa raiz** para validar o que está de fato implementado no código sobre os modelos **LightGBM** e **CatBoost** dentro do módulo **Profile Intelligence**, além de auditar a baixa performance das watchlists automáticas, os status `Aguardando`, o erro `503 Database error` ao abrir watchlists e a integração completa com o Shadow Portfolio.

Esta auditoria deve acontecer **antes de qualquer correção**.

---

## 1. Missão real do LightGBM e CatBoost

A missão do **LightGBM** e do **CatBoost** no Scalpyn **não é comparar com XGBoost** e **não é validar L1**.

A missão correta é:

```text
LightGBM e CatBoost devem validar todos os sinais dos ativos da L3.
```

Eles devem atuar no contexto de:

```text
L3
L3_LAB
L3_SIMULATED
L3_REJECTED
watchlists criadas pelo Profile Intelligence
profiles clonados/versionados
candidatos do Auto-Pilot
resultados do Shadow Portfolio
```

O objetivo desses modelos é:

```text
identificar sinais vencedores
identificar sinais perdedores
separar padrões bons e ruins por profile/watchlist
ajudar a ajustar indicadores, ranges e scores
criar watchlists vencedoras com alta taxa de win rate
alimentar o Profile Intelligence para auto calibrações
usar resultados reais/simulados do Shadow Portfolio como base de validação
```

---

## 2. Premissas proibidas

Não assumir:

```text
que LightGBM/CatBoost devem usar o mesmo dataset do XGBoost
que LightGBM/CatBoost devem competir com XGBoost
que XGBoost precisa entrar no fluxo L3
que L1_SPECTRUM é a fonte correta para LightGBM/CatBoost
que AUC global é suficiente para avaliar sucesso
que métrica global de todas as fontes L3 basta
que misturar L3/L3_LAB/L3_SIMULATED/L3_REJECTED sem segmentação é correto
que o modelo está funcionando só porque aparece na UI
que o modelo está funcionando só porque há dependência instalada
que o modelo está funcionando só porque há script de treino
```

Nesta auditoria:

```text
não comparar XGBoost com LightGBM/CatBoost
não usar XGBoost como referência obrigatória
não exigir que LightGBM/CatBoost usem dataset do XGBoost
não avaliar promoção cruzada entre XGBoost e modelos L3
```

---

## 3. Problemas observados

### 3.1 Auto-Pilot aparenta não atualizar último ciclo

Na interface aparece:

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

Auditar:

- se o botão chama o endpoint correto;
- se o endpoint recebe o request;
- se o backend inicia o ciclo;
- se o ciclo é bloqueado por validação, lock, amostra, status ou erro;
- se o job é enfileirado;
- se o worker processa;
- se o run é persistido;
- se a UI usa cache/stale state;
- se o timestamp do último ciclo é atualizado;
- se há erro silencioso;
- se o scheduler está travado.

### 3.2 Shadow Portfolio não apresenta trades simulados de todas as watchlists automáticas

Auditar se:

- as watchlists automáticas foram criadas corretamente;
- cada watchlist tem `profile_id`;
- cada profile criado tem associação com watchlist;
- o Shadow Portfolio está gerando trades;
- os trades são gravados em `shadow_trades`;
- os trades têm `source`, `profile_id`, `watchlist_id`, `profile_name`, `strategy_skill` e `origin`;
- a UI filtra incorretamente;
- jobs de coleta/simulação olham apenas watchlists manuais;
- o pipeline ignora watchlists criadas pelo Profile Intelligence;
- existem trades sem `profile_id`;
- existem trades com `profile_id`, mas fora do filtro da tela.

### 3.3 Watchlists automáticas apresentam win rate medíocre e P&L negativo

Exemplos observados:

```text
L3_MEAN_REVERSION_CONTROLADO_V3        Total 227 | Win Rate 50.0% | P&L +$128.54 | P&L Médio +0.08%
L3_TREND_FORTE_V3                      Win Rate 41.9% | P&L -$32.89
L3_META_CONTROLLED_BOUNCE_V1           Win Rate 41.7% | P&L -$80.90
L3_VOLATILIDADE_MODERADA_V3            Win Rate 41.7% | P&L -$68.36
L3_ANTI_EXAUSTAO_V3                    Win Rate 39.1% | P&L -$355.38
L3_TREND_CONSERVADOR_V3                Win Rate 37.4% | P&L -$465.13
L3_EARLY_PULLBACK_V3                   Win Rate 36.8% | P&L -$454.03
L3_ML_PRIORITY_V4                      Win Rate 28.3% | P&L -$630.00
L3_BREAKOUT_V3                         Win Rate 22.9% | P&L -$147.80
L3_HIGH_LIQUIDITY_V3                   Win Rate 22.0% | P&L -$783.86
```

Também há watchlists de combinações com poucos trades e resultado ruim:

```text
rsi_gte_72_AND_bb_0_050_0_080_AND_depth_gte_20k                         Total 1 | Win Rate 0% | P&L Médio -1.00%
rsi_gte_72_AND_ema50_gt_ema200_false_AND_depth_gte_20k                  Total 5 | Win Rate 0% | P&L Médio -1.00%
rsi_gte_72_AND_macd_hist_lte_0_AND_adx_acc_gt_0                         Total 2 | Win Rate 0% | P&L Médio -1.00%
```

Auditar por que o Profile Intelligence está criando watchlists que resultam em performance ruim.

### 3.4 Watchlists automáticas com status `Aguardando`

Exemplos:

```text
L3_ANTI_EXAUSTAO_V3 · Auto-Pilot v1
L3_BREAKOUT_V3 · Auto-Pilot v1
L3_EARLY_PULLBACK_V3 · Auto-Pilot v1
L3_HIGH_LIQUIDITY_V3 · Auto-Pilot v1
L3_MEAN_REVERSION_CONTROLADO_V3 · Auto-Pilot v1
L3_META_CONTROLLED_BOUNCE_V1 · Auto-Pilot v1
L3_TREND_CONSERVADOR_V3 · Auto-Pilot v1
L3_TREND_FORTE_V3 · Auto-Pilot v1
L3_VOLATILIDADE_MODERADA_V3 · Auto-Pilot v1
```

Verificar se estão aguardando por:

```text
ausência de symbols
ausência de profile_id
sem vínculo com Shadow Portfolio
primeira coleta ainda não executada
trades ainda não fechados
sem eligible symbols
erro de job
fora do scheduler
watchlist criada mas não ativada
candidato ainda não aprovado
símbolos sem passagem nos filtros
source incorreto
filtro errado na UI
```

### 3.5 Erro `503 Database error` ao abrir watchlist

Exemplo observado:

```text
AP · rsi_gte_72_AND_vol_spike_gt_2_5_AND_obp_lt_0_20
Refresh error: 503 Database error
Total: 0
Approved: 0
Rejected: 0
Top Indicator: —
Nenhum ativo aprovado para os filtros atuais.
```

Auditar:

```text
endpoint chamado
payload enviado
query SQL executada
parâmetros enviados
profile_id
watchlist_id
source
stage
status
indicator filter
erro real no backend
stack trace
timeout
erro de conexão
erro de schema
coluna inexistente
join inválido
watchlist_id nulo
profile_id nulo
nome/slug quebrando query
erro ao buscar symbols
erro ao buscar approved/rejected
erro ao buscar top_indicator
```

Entregar:

```text
URL/endpoint chamado
payload enviado
query SQL aproximada
stack trace ou log do backend
causa raiz
correção recomendada
```

### 3.6 Audit Trail Log imutável insuficiente

O Audit Trail Log deve detalhar:

```text
criação de profiles
criação de watchlists
criação de candidatos shadow
ajustes de indicadores
ajustes em scoring
ajustes em block rules
ajustes em signals
ajustes em entry triggers
valor antes
valor depois
diff
nome do profile criado/modificado
origem da recomendação
módulo gerador
run_id
model_id
source_type
validation_status
actionability_status
aprovação humana
ativação live
rollback
motivo do rollback
degradação detectada
ator/usuário
timestamp
payload completo
```

Auditar se há granularidade suficiente ou apenas eventos genéricos.

---

## 4. Objetivo principal da auditoria

Validar se o código atual implementa corretamente:

```text
LightGBM/CatBoost como validadores dos sinais L3 e auxiliares do Profile Intelligence.
```

Responder:

1. LightGBM está realmente implementado?
2. CatBoost está realmente implementado?
3. Eles leem dados da L3?
4. Eles leem todas as watchlists relevantes da L3?
5. Eles diferenciam L3, L3_LAB, L3_SIMULATED e L3_REJECTED?
6. Eles preservam `profile_id`, `profile_name`, `watchlist_id`, `candidate_id` e `source_layer`?
7. Eles usam os resultados do Shadow Portfolio?
8. Eles aprendem com WIN/LOSS reais ou simulados?
9. Eles identificam sinais vencedores?
10. Eles identificam sinais perdedores?
11. Eles geram explicações por indicador/range/score?
12. Eles geram sugestões aplicáveis para Profile Intelligence?
13. Eles alimentam criação de watchlists vencedoras?
14. Eles ajudam o Auto-Pilot a calibrar clones?
15. Eles gravam resultados auditáveis?
16. Eles aparecem corretamente na UI?
17. Eles possuem métricas segmentadas por profile/watchlist/source?
18. Eles estão apenas parcialmente implementados?
19. Há scripts soltos fora do pipeline oficial?
20. Há falsa sensação de funcionalidade?
21. Por que as watchlists automáticas têm win rate tão ruim?
22. Por que nenhuma watchlist automática parece consistentemente vencedora?
23. Por que existem watchlists com status `Aguardando`?
24. Por que abrir algumas watchlists gera `503 Database error`?
25. As regras perdedoras estão sendo convertidas em watchlists de entrada?
26. O Shadow Portfolio está medindo corretamente?
27. A UI está exibindo corretamente?
28. Qual correção deve ser feita primeiro?

---

## 5. Regras da auditoria

Executar em modo **read-only**.

Não fazer nesta etapa:

```text
não corrigir código
não implementar nada
não alterar banco
não executar migrations
não reprocessar histórico
não promover candidato para live
não alterar profiles
não alterar watchlists
não alterar thresholds
não re-treinar modelos
não mudar configurações do Auto-Pilot
não limpar banco
```

Somente:

```text
auditar
provar
classificar
explicar
mapear causa raiz
propor o prompt correto de correção
```

Se algo não está implementado, dizer que não está. Se está parcial, dizer que está parcial. Se existe apenas na UI, marcar como `UI_ONLY`.

---

## 6. Escopo técnico obrigatório

Auditar código, banco, jobs, logs, UI e testes relacionados a:

```text
LightGBM
CatBoost
Profile Intelligence
Shadow Portfolio
watchlists automáticas
profiles clonados
Auto-Pilot candidates
Dynamic Combinations
Association Rules
Optuna
Anthropic explanations
suggestions
audit trail
model registry
feature store
dataset builder
training scripts
inference scripts
```

Buscar no repositório:

```text
lightgbm
LightGBM
catboost
CatBoost
L3
L3_LAB
L3_SIMULATED
L3_REJECTED
watchlist_id
profile_id
profile_name
candidate_id
source_layer
source_filter
shadow_trades
model_registry
ml_models
feature_columns_json
dataset_contract
suggestion
calibration
auto_calibration
score_adjustment
block_rules
signals
entry_triggers
Aguardando
Database error
503
top_indicator
created_by_module
```

---

## 7. Auditoria do dataset L3 usado por LightGBM/CatBoost

Validar se existe um dataset builder próprio ou compartilhado para L3.

O dataset de LightGBM/CatBoost deve conter:

```text
trade_id
symbol
entry_time
exit_time
source_layer
watchlist_id
watchlist_name
profile_id
profile_name
candidate_id
strategy_skill
market_regime
features_snapshot
entry_indicators
outcome
pnl_pct
is_win
is_loss
tp_hit
sl_hit
holding_seconds
shadow_run_id
profile_intelligence_run_id
```

Auditar se inclui corretamente:

```text
L3
L3_LAB
L3_SIMULATED
L3_REJECTED
watchlists criadas pelo Profile Intelligence
watchlists criadas pelo Auto-Pilot
profiles clonados/versionados
candidatos shadow
```

Misturar fontes L3 pode ser correto, mas somente se cada linha preservar:

```text
source_layer
watchlist_id
profile_id
candidate_id
created_by_module
```

Se ausentes, classificar como:

```text
CRÍTICO — dataset L3 sem attribution suficiente
```

---

## 8. Auditoria de labels e outcomes

Validar como LightGBM/CatBoost aprendem WIN/LOSS.

Responder:

1. Qual label é usado?
2. O label vem do Shadow Portfolio?
3. O label usa TP/SL correto?
4. O label diferencia `TP_HIT`, `SL_HIT`, `TIMEOUT`, `RUNNING`?
5. `RUNNING` entra no treino indevidamente?
6. `TIMEOUT` é perda, neutro ou excluído?
7. Há leakage de futuro?
8. `pnl_pct`, `exit_price`, `max_profit_after_entry`, `price_after_*` entram como feature?
9. O label é igual para L3/L3_LAB/L3_SIMULATED/L3_REJECTED?
10. O label é versionado?

Features proibidas no treino:

```text
outcome
exit_price
exit_time
pnl_pct
mfe pós-entrada
mae pós-entrada
price_after_1h
price_after_2h
price_after_4h
price_after_12h
price_after_24h
target_hit
future_return
```

---

## 9. Auditoria de função operacional dos modelos

Classificar o status real de LightGBM e CatBoost:

```text
NOT_IMPLEMENTED
PARTIAL
SCRIPT_ONLY
TRAINING_ONLY
INFERENCE_ONLY
CONFIGURED_BUT_NOT_RUNNING
RUNNING_WITH_ERRORS
RUNNING_OK
UI_ONLY
BROKEN_INTEGRATION
```

Para cada modelo, responder:

```text
dependência instalada?
import funciona?
trainer existe?
predictor existe?
usa dataset L3?
usa shadow_trades?
preserva source_layer?
preserva profile_id?
preserva watchlist_id?
preserva candidate_id?
treina?
salva artifact?
registra model_id?
registra métricas?
gera importância de features?
gera sugestões?
gera ajustes de score/range?
integra com Profile Intelligence?
integra com Auto-Pilot?
integra com UI?
gera audit trail?
possui testes?
```

Para CatBoost, responder também:

```text
trata categóricas?
usa profile_name/source_layer/watchlist_id como categóricas?
```

---

## 10. Auditoria de métricas segmentadas

LightGBM/CatBoost não devem ser avaliados apenas por métrica global.

Auditar se há métricas por:

```text
source_layer
watchlist_id
watchlist_name
profile_id
profile_name
candidate_id
strategy_skill
market_regime
symbol
```

Métricas obrigatórias por segmento:

```text
trade_count
win_count
loss_count
win_rate
base_win_rate
lift
precision
recall
FPR
expected_pnl
average_pnl
median_pnl
drawdown
confidence
feature_importance_top
top_positive_signals
top_negative_signals
```

Se só houver métrica global:

```text
ALTO — métrica global insuficiente para missão L3
```

---

## 11. Auditoria de geração de recomendações

Validar se LightGBM/CatBoost geram recomendações úteis.

Recomendações devem apontar:

```text
indicador vencedor
indicador perdedor
range vencedor
range perdedor
score adjustment
block rule candidate
signal candidate
entry trigger candidate
watchlist winner
watchlist loser
profile clone improvement
candidate beats incumbent
```

Cada recomendação deve conter:

```text
source_model_type
source_model_id
source_run_id
source_layer
watchlist_id
profile_id
profile_name
indicator_name
current_value
recommended_value
before_json
after_json
diff_json
evidence_count
validation_status
actionability_status
expected_impact
risk_level
rollback_payload
```

Auditar se existem de fato ou se o modelo apenas treina e mostra métrica.

---

## 12. Auditoria de criação e qualidade das watchlists automáticas

Para cada watchlist automática, levantar:

```text
watchlist_id
watchlist_name
profile_id
profile_name
created_by_module
source_type
source_run_id
source_model_type
source_model_id
source_combination_id
source_rule_id
validation_status
actionability_status
blocked_reason
evidence_count
discovery_trade_count
validation_trade_count
discovery_win_rate
validation_win_rate
base_win_rate
lift
expected_pnl
created_at
shadow_status
shadow_trade_count
closed_trade_count
win_rate
pnl_total
pnl_avg
```

Classificar:

```text
VALIDATED_WINNER
VALIDATED_LOSER
INSUFFICIENT_SAMPLE
AWAITING_SHADOW
BROKEN_PIPELINE
BAD_RULE_GENERATION
OVERFITTED_DISCOVERY
NO_VALIDATION
NO_SYMBOLS
NO_TRADES
```

---

## 13. Auditoria: por que nenhuma watchlist automática teve números positivos?

Responder objetivamente:

1. As watchlists ruins foram criadas com validation aprovada?
2. Tinham amostra mínima suficiente?
3. Tinham expected PnL positivo antes de entrar no Shadow?
4. Tinham lift real no validation ou só no discovery?
5. Tinham concentração em poucos símbolos?
6. Tinham concentração em poucos dias?
7. Tinham regras de exaustão disfarçadas de oportunidade?
8. O Profile Intelligence está usando WIN/LOSS corretamente?
9. O Shadow Portfolio aplica TP/SL corretamente?
10. O P&L considera fee/slippage corretamente?
11. O win rate é calculado sobre trades fechados ou inclui abertos?
12. O P&L total inclui trades em aberto?
13. As watchlists entram tarde, depois do movimento?
14. Há lookahead/data leakage na criação das regras?
15. As regras criadas são contrárias ao objetivo?
16. `rsi_gte_72` está gerando sinais de exaustão?
17. `macd_hist_lte_0` está gerando viés baixista?
18. `ema50_gt_ema200_false` está selecionando tendência estrutural ruim?
19. `obp_lt_0_20` está selecionando pressão compradora fraca?
20. O Auto-Pilot está criando watchlists de bloqueio como se fossem watchlists de entrada?

---

## 14. Auditoria de inversão semântica: regra LOSS virou entrada?

Verificar se o Profile Intelligence confunde:

```text
indicador associado a LOSS
```

com:

```text
indicador bom para entrada
```

Exemplo de risco:

```text
RSI >= 72
MACD histogram <= 0
EMA50 < EMA200
Orderbook pressure < 0.20
```

Essas condições podem significar:

```text
exaustão
fraqueza
reversão negativa
continuação de queda
baixa pressão compradora
```

Auditar se deveriam virar:

```text
block_rule
risk_warning
negative_score
avoid_entry
```

em vez de:

```text
entry_watchlist
positive_signal
buy_candidate
```

Responder:

```text
Quantas watchlists automáticas vieram de padrões associados a perdas?
Quantas deveriam ser block_rules em vez de watchlists de entrada?
Existe consequent = WIN/LOSS sendo respeitado?
Existe actionability_status correto?
Existe inversão de sinal?
```

---

## 15. Auditoria de Shadow Portfolio por status

Separar métricas por:

```text
RUNNING
CLOSED
TP_HIT
SL_HIT
TIMEOUT
EARLY_EXIT
CANCELLED
PENDING
AWAITING
ERROR
```

Para cada profile/watchlist:

```text
total_trades
open_trades
closed_trades
win_rate_closed_only
win_rate_including_open
pnl_closed
pnl_open_unrealized
pnl_total
avg_pnl_closed
avg_holding_seconds
tp_hit_count
sl_hit_count
timeout_count
error_count
```

Verificar se a tela mistura:

```text
trades abertos
trades fechados
watchlists aguardando
watchlists com erro
watchlists sem trades
```

---

## 16. Auditoria do pipeline Profile Intelligence → Watchlist → Shadow

Mapear o fluxo real:

```text
Profile Intelligence run
↓
Dynamic Combination / Association / Optuna / ML
↓
Suggestion
↓
Watchlist candidate
↓
Watchlist criada
↓
Symbols adicionados
↓
Shadow Portfolio registra watchlist
↓
Shadow job cria trades
↓
Trades fecham
↓
Métricas calculadas
↓
Profile Intelligence lê resultado
↓
Auto-Pilot calibra
```

Para cada etapa, classificar:

```text
OK
PARTIAL
MISSING
BROKEN
WAITING
ERROR
```

Identificar o primeiro ponto onde a watchlist ruim ou aguardando quebra.

---

## 17. Auditoria da auto calibração

Validar se o ciclo existe ponta a ponta:

```text
Shadow Portfolio gera resultados
↓
LightGBM/CatBoost analisam sinais L3
↓
Modelos identificam winners/losers
↓
Profile Intelligence gera recomendações
↓
Auto-Pilot cria clone/versionamento
↓
Clone é testado em Shadow
↓
Resultado é comparado contra incumbent
↓
Sugestão é aprovada ou bloqueada
↓
Audit Trail registra tudo
```

Classificar cada etapa:

```text
OK
PARTIAL
MISSING
BROKEN
UI_ONLY
```

---

## 18. Audit Trail obrigatório

Auditar se existem eventos:

```text
MODEL_TRAINED
MODEL_EVALUATED
SIGNAL_WINNER_IDENTIFIED
SIGNAL_LOSER_IDENTIFIED
WATCHLIST_CANDIDATE_CREATED
WATCHLIST_CREATED
PROFILE_CLONE_CREATED
PROFILE_RULE_RECOMMENDED
PROFILE_RULE_UPDATED
SCORE_ADJUSTMENT_RECOMMENDED
BLOCK_RULE_RECOMMENDED
SHADOW_TEST_STARTED
SHADOW_TEST_COMPLETED
AUTO_CALIBRATION_SUGGESTED
AUTO_CALIBRATION_APPLIED
ROLLBACK_TRIGGERED
```

Cada evento deve conter:

```text
model_type
model_id
model_run_id
source_layer
watchlist_id
profile_id
profile_name
before_json
after_json
diff_json
reason
evidence_count
validation_status
actor
timestamp
```

---

## 19. Banco de dados — queries read-only obrigatórias

Criar SQL para responder:

### 19.1 Modelos LightGBM/CatBoost

```sql
-- modelos registrados por type
-- status, created_at, artifact_path, model_run_id, source_filter, dataset_scope
```

### 19.2 Runs dos modelos

```sql
-- runs de treino/inferência LightGBM/CatBoost
-- dataset size, source_layer, feature_count, train_from, train_to
```

### 19.3 Dataset L3

```sql
-- contagem de trades por source_layer/profile_id/watchlist_id
-- apenas registros elegíveis para treino
```

### 19.4 Watchlists vencedoras

```sql
-- watchlists criadas por Profile Intelligence ou modelos
-- source_model_id, source_run_id, symbols_count, shadow_trade_count
```

### 19.5 Recomendações

```sql
-- recomendações geradas por LightGBM/CatBoost
-- target_section, indicator, before/after/diff, validation_status
```

### 19.6 Shadow Portfolio

```sql
-- shadow trades por watchlist/profile/source
-- win_rate, trade_count, closed_trades
```

### 19.7 Audit Trail

```sql
-- eventos com model_type in ('lightgbm', 'catboost')
-- eventos sem profile_name, sem before/after/diff, sem source_run_id
```

### 19.8 Ranking das watchlists automáticas

```sql
-- listar watchlists criadas pelo PI/Auto-Pilot
-- com trade_count, closed_count, win_rate, pnl_total, pnl_avg
```

### 19.9 Watchlists aguardando

```sql
-- listar watchlists com status aguardando/pending
-- verificar symbols_count, profile_id, created_at, last_shadow_trade_at
```

### 19.10 Watchlists com erro 503

```sql
-- identificar watchlist_id/profile_id da tela que gera 503
-- validar se existem symbols, trades e profile associado
```

### 19.11 Regras que originaram watchlists negativas

```sql
-- associar watchlist -> suggestion -> source_combination/rule/model
-- verificar se origem era WIN, LOSS, block, risk_warning ou positive_signal
```

### 19.12 Indicadores mais presentes nas watchlists perdedoras

```sql
-- extrair indicadores/ranges das watchlists perdedoras
-- contar frequência e pnl médio por indicador/range
```

### 19.13 Comparação com profiles originais

```sql
-- comparar clone/watchlist automática vs profile original/incumbent
```

---

## 20. Logs obrigatórios

Buscar logs por:

```text
lightgbm
catboost
l3 validator
watchlist validator
profile candidate evaluator
model trained
model evaluated
feature importance
winner signal
loser signal
watchlist created
shadow portfolio
auto calibration
profile clone
recommendation
score adjustment
block rule
503
Database error
watchlist
refresh error
L3_LAB
Aguardando
pending
profile_id
watchlist_id
approved
rejected
top_indicator
shadow summary
auto-pilot watchlist
created_by_module
```

Entregar:

```text
logs encontrados
erros
warnings
ausência de logs esperados
jobs que não rodaram
imports que falharam
scripts que rodam fora do pipeline
stack trace
endpoint
query
parâmetros
tempo de execução
erro SQL
erro de conexão
erro de timeout
erro de campo nulo
erro de join
```

---

## 21. Resultado esperado da auditoria

Entregar relatório com esta estrutura:

```markdown
# Auditoria — Missão Real de LightGBM/CatBoost como Validadores L3 + Shadow Portfolio

## 1. Sumário Executivo
- Veredito geral
- O que está alinhado com a missão L3
- O que está desalinhado
- O que está parcialmente implementado
- O que é apenas visual
- O que precisa de correção
- Causa raiz do baixo win rate das watchlists automáticas
- Causa raiz das watchlists em Aguardando
- Causa raiz do erro 503

## 2. Missão Correta dos Modelos
- LightGBM
- CatBoost
- O que eles devem fazer
- O que eles não devem fazer
- Por que XGBoost/L1 não faz parte deste processo

## 3. Estado Real no Código
| Item | LightGBM | CatBoost | Evidência |
|---|---|---|---|
| Dependência instalada | | | |
| Trainer | | | |
| Predictor | | | |
| Dataset L3 | | | |
| Shadow Portfolio | | | |
| Feature importance | | | |
| Recommendations | | | |
| Watchlist creation | | | |
| Audit Trail | | | |

## 4. Dataset L3
- Fontes incluídas
- Campos presentes
- Campos ausentes
- Attribution por profile/watchlist/source
- Risco de mistura
- Label/outcome usado

## 5. Métricas Segmentadas
- Por source_layer
- Por profile
- Por watchlist
- Por candidate
- Por regime
- Problemas encontrados

## 6. Recomendações e Auto Calibração
- Sinais vencedores
- Sinais perdedores
- Ajustes de indicadores/ranges
- Ajustes de score
- Criação de watchlists vencedoras
- Integração com Auto-Pilot
- Integração com Shadow Portfolio

## 7. Causa Raiz — Win Rates Medíocres das Watchlists Automáticas

### Diagnóstico
- As watchlists são ruins por lógica estratégica?
- São ruins por inversão semântica WIN/LOSS?
- São ruins por baixa amostra?
- São ruins por falta de validation?
- São ruins por bug de Shadow Portfolio?
- São ruins por erro de cálculo?
- São ruins porque ainda estão aguardando trades?

### Evidências
- ranking de watchlists por P&L
- ranking por win rate
- origem de cada watchlist
- validation_status de cada origem
- actionability_status de cada origem
- indicadores mais frequentes nas perdedoras

### Causa raiz
Descrever a causa primária e causas secundárias.

### Correções recomendadas
Separar em:
- P0 — erro 503 / watchlists aguardando
- P1 — bloquear inversão de regra LOSS como entrada
- P2 — recalibrar geração de watchlists
- P3 — melhorar UI e métricas

## 8. Causa Raiz — Watchlists com Status Aguardando

### Diagnóstico
- aguardando por ausência de symbols?
- aguardando por ausência de shadow trades?
- aguardando por status pending?
- aguardando por erro de job?
- aguardando por filtro da UI?

### Evidências
- watchlist_id
- profile_id
- symbols_count
- shadow_trade_count
- last_job_run
- last_error

### Correção recomendada

## 9. Causa Raiz — 503 Database Error ao Abrir Watchlist

### Diagnóstico
- endpoint
- payload
- query
- stack trace
- causa raiz

### Correção recomendada

## 10. Shadow Portfolio por Status
- RUNNING
- CLOSED
- TP_HIT
- SL_HIT
- TIMEOUT
- AWAITING
- ERROR

## 11. Audit Trail
- Eventos existentes
- Eventos ausentes
- Campos ausentes
- before/after/diff
- profile_name
- model_run_id
- source_run_id

## 12. Bugs e Inconsistências
| ID | Severidade | Área | Problema | Evidência | Impacto | Correção recomendada |
|---|---|---|---|---|---|---|

## 13. Veredito Final
Responder:
- LightGBM cumpre a missão L3?
- CatBoost cumpre a missão L3?
- Eles leem todos os sinais L3?
- Eles identificam winners/losers?
- Eles geram recomendações cirúrgicas?
- Eles criam watchlists vencedoras?
- Eles alimentam auto calibração?
- O Shadow Portfolio valida essas watchlists?
- O Audit Trail registra tudo?
- Existe comparação indevida com XGBoost?
- Por que as watchlists automáticas têm win rate tão ruim?
- Por que nenhuma watchlist automática parece consistentemente vencedora?
- Por que existem watchlists com status Aguardando?
- Por que abrir algumas watchlists gera 503 Database error?
- As regras perdedoras estão sendo convertidas em watchlists de entrada?
- O Shadow Portfolio está medindo corretamente?
- A UI está exibindo corretamente?
- Qual deve ser o prompt de correção correto?

## 14. Prompt de Correção Recomendado
Criar um prompt de correção específico, baseado nos achados reais da auditoria.
```

---

## 22. Classificação de severidade

Classificar achados como:

### CRÍTICO

```text
LightGBM/CatBoost não implementados apesar de aparecerem funcionais
não leem L3
não usam Shadow Portfolio
não preservam profile_id/watchlist_id
não geram recomendações
watchlists vencedoras não entram no Shadow
auto calibração não usa resultados reais
watchlists automáticas não entram no Shadow Portfolio
erro 503 ao abrir watchlist
regra de perda vira watchlist de entrada
```

### ALTO

```text
métricas apenas globais
sem segmentação por profile/watchlist
sem audit trail completo
scripts fora do pipeline
metadata ausente
dependências ausentes
watchlists com status Aguardando sem causa clara
UI escondendo trades existentes
```

### MÉDIO

```text
UI confusa
status incompleto
logs insuficientes
testes ausentes
win rate exibido misturando abertos e fechados
```

### BAIXO

```text
nomenclatura
tooltip
layout
agrupamento visual
```

---

## 23. Critério de conclusão

A auditoria só estará completa se responder:

```text
Por que as watchlists automáticas têm win rate tão ruim?
Por que nenhuma watchlist automática parece consistentemente vencedora?
Por que existem watchlists com status aguardando?
Por que abrir algumas watchlists gera 503 Database error?
As regras perdedoras estão sendo convertidas em watchlists de entrada?
O Shadow Portfolio está medindo corretamente?
A UI está exibindo corretamente?
Qual correção deve ser feita primeiro?
LightGBM/CatBoost cumprem a missão L3?
Eles leem todos os sinais L3?
Eles geram recomendações cirúrgicas?
Eles alimentam auto calibração?
```

Não fazer correções nesta etapa. Apenas auditar, provar, classificar e recomendar.
