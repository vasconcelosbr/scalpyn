# Auditoria ML + Auto-Pilot + Profile Intelligence — Scalpyn

Data da auditoria: 2026-06-19  
Modo: read-only sobre código, PostgreSQL e Railway.  
Escopo de escrita desta execução: somente este relatório e o arquivo SQL de auditoria.

## 1. Sumário Executivo

### Veredito geral

O Profile Intelligence está operacional e executa análises estatísticas, combinações dinâmicas, association rules, Optuna e explicações Anthropic. A hipótese histórica sobre LightGBM e CatBoost foi confirmada: as flags estão ligadas no banco, mas os dois modelos não têm caminho real de treino, inferência, persistência, sugestão ou decisão.

O risco atual de LightGBM/CatBoost não é conflito entre modelos. É falsa sensação de funcionalidade e ausência de rastreabilidade. O risco futuro de conflito permanece, porque o registry atual não possui `model_type` nem constraint de unicidade de champion.

O sistema possui dois Auto-Pilots distintos:

1. Auto-Pilot legado, que altera configurações existentes.
2. Auto-Pilot global do Profile Intelligence, que cria candidatos em shadow e pode promover automaticamente um profile para live.

O Auto-Pilot legado está configurado em produção com escrita real (`dry_run_mode=false`) e autoridade ampla. Entretanto, sua janela de performance é calculada sem `profile_id` e a chamada principal não passa `user_id`. Assim, uma métrica agregada global pode orientar mutações atribuídas a um profile específico. Este é o achado mais grave.

O Auto-Pilot do Profile Intelligence está habilitado e possui candidatos em shadow. Ele mantém auditoria append-only e rollback de associação, mas seu código permite ativação live automática sem aprovação humana por candidato e sem `source_model`/`source_run_id` de ML.

### Respostas objetivas sobre LightGBM e CatBoost

| Pergunta | Resposta |
|---|---|
| As flags têm efeito real? | Não. São campos persistidos e renderizados, sem consumidor de execução. |
| Existe import LightGBM? | Apenas teste de disponibilidade no endpoint de overview. |
| Existe import CatBoost? | Não. |
| Existem modelos persistidos LightGBM/CatBoost? | Não: `0` LightGBM e `0` CatBoost `[query]`. |
| Existem sugestões originadas por eles? | Não há campo de origem de modelo nas sugestões; eventos atribuídos aos dois = `0` `[query]`. |
| Podem conflitar hoje? | Não, porque não executam. |
| A UI induz funcionalidade? | Sim. Os toggles são editáveis e o texto apenas diz “requer pacote instalado”. |
| Devem ficar habilitados? | Não. Bloquear/desabilitar e marcar “não implementado”. |

### Estado resumido

| Recurso | Estado | Veredito |
|---|---|---|
| Profile Intelligence | Operacional | Confiável como motor exploratório, não como autorização autônoma de produção |
| XGBoost | Ativo | Único modelo operacional encontrado |
| LightGBM | Órfão | Contribuição zero |
| CatBoost | Órfão | Contribuição zero |
| Champion/Challenger ML | Parcial | Existe promoção por métricas, mas sem constraint e sem `model_type` |
| Auto-Pilot legado | Ativo com escrita | Inseguro enquanto usar performance global |
| PI Auto-Pilot | Ativo em shadow | Governança melhor, mas promoção live automática exige bloqueio/revisão |
| Dataset global | Mistura profiles por design | Não está claramente separado do dataset scoped |
| Dataset por profile | Implementado no trainer | Ainda não usado pelo caminho operacional de inferência |
| UI de indicadores | Incompleta | Não recebe profiles associados e não oferece aplicação segura por indicador |

## 2. Achados Críticos

| ID | Severidade | Área | Problema | Evidência | Impacto | Correção recomendada |
|---|---|---|---|---|---|---|
| C-01 | CRÍTICO | Auto-Pilot legado | Performance não é segregada por profile; ciclo chama a função sem `user_id` | `compute_performance_window(..., user_id=None)` em `backend/app/services/autopilot_engine.py:234`; filtro só existe se `user_id` for informado em `:267`; chamada sem user/profile em `:1714` | Métrica global pode alterar config atribuída a um profile específico | Exigir `user_id` e `profile_id`; abortar se ausentes; filtrar todas as queries |
| C-02 | CRÍTICO | Auto-Pilot legado | Produção permite escrita real e autoridade ampla | Config literal `[config: autopilot_guardrails]`: `"dry_run_mode": false`, `"autopilot_full_authority": true`, `autopilot_can_adjust=[scoring_rules, minimum_score, block_rules, entry_triggers]`; `6` eventos `MUTATED` `[query]` | Alterações automáticas podem afetar regras globais usando evidência agregada incorreta | Acionar kill-switch ou voltar a dry-run até C-01 ser corrigido e validado |
| C-03 | CRÍTICO | PI / combinações | Dynamic Combinations não usa validation e trata feature ausente como regra aprovada | `_evaluate_rules` declara “Missing features skip the rule” em `counterfactual_combination_service.py:186`; Dynamic usa apenas discovery em `:598`; `2.152` combinações dinâmicas, `0` com validação `[query]` | Combinações podem parecer fortes por dados incompletos/data snooping e alimentar sugestões/Auto-Pilot | Feature ausente deve falhar ou invalidar amostra; exigir holdout temporal antes de elegibilidade |
| C-04 | CRÍTICO | PI Auto-Pilot | Pode promover automaticamente para live | Troca `live_watchlist.profile_id` em `profile_intelligence_autopilot_service.py:1335` e define `live_trading_enabled=True` em `:1340`; serviço habilitado `[query]` | Hipótese estatística pode virar configuração live sem aprovação humana específica | Exigir aprovação humana para `LIVE_ACTIVATED`; manter somente shadow até aprovação |
| C-05 | CRÍTICO | Dataset ML/API | Endpoint `/api/ml/train` lê shadow trades sem `user_id`, `profile_id` ou escopo | Query em `backend/app/api/ml.py:131`; trainer global filtra apenas `source` em `ml_trainer/job.py:582-583` | Mistura profiles e, em ambiente multiusuário, mistura tenants | Tornar `user_id` obrigatório; escolher explicitamente `global`, `profile` ou `profile+regime+skill`; registrar escopo |
| C-06 | CRÍTICO | Cadeia PI → live | Sugestões não registram origem de modelo/técnica em campos normalizados | `ProfileSuggestion` não possui `source_type`, `source_model_type`, `source_model_id`; `81/81` sugestões sem `source_profiles` `[query]` | Não é possível provar qual engine e população sustentaram uma ativação | Adicionar registry de sugestões e impedir promoção sem atribuição completa |

## 3. Achados Altos e Médios

| ID | Severidade | Área | Achado |
|---|---|---|---|
| H-01 | ALTO | LightGBM/CatBoost | Flags ligadas no banco, mas ausentes das dependências, imagens, testes e fluxo de execução |
| H-02 | ALTO | UI | Toggles editáveis sugerem capacidade real; execução manual não envia LightGBM/CatBoost |
| H-03 | ALTO | Model registry | Não existe constraint que impeça dois modelos `active` no mesmo escopo |
| H-04 | ALTO | Inferência scoped | Loader aceita modelo por profile, mas callers operacionais não passam `profile_id` |
| H-05 | ALTO | Inferência scoped | Se `profile_id` passar a ser usado, threshold/model_id continuam buscados por query global não scoped |
| H-06 | ALTO | Indicadores | `456/456` stats sem `source_profiles` e `81/81` sugestões sem `source_profiles` `[query]` |
| H-07 | ALTO | Optuna PI | Recebe janela de validation, mas não a usa; otimiza e salva resultados de discovery |
| H-08 | ALTO | Association Rules | Não há validação temporal; regras LOSS e WIN são persistidas no mesmo tipo sem actionability explícita |
| H-09 | ALTO | Auto-Pilot legado | `9/20` logs sem before e `9/20` sem after `[query]`; trilha não é completa para todas as ações |
| H-10 | ALTO | Testes | Fluxo `create_profile_from_suggestion` tem `18` testes falhando `[test]`, incluindo mocks incompatíveis com o código atual |
| M-01 | MÉDIO | Labeling | Label requer `holding_seconds`; testes legados sem esse campo agora viram label `0`, gerando inconsistência teste/código |
| M-02 | MÉDIO | Cobertura | `atr_pct` e `vwap_distance_pct` aparecem em parcela muito menor dos snapshots que RSI/ADX `[query]` |
| M-03 | MÉDIO | HTTP | Amostra HTTP: `264` respostas 200 e `36` respostas 401 `[query]`; sem 5xx na amostra, mas há requests muito lentos |
| M-04 | MÉDIO | Código morto | `dataset_builder.py` está deprecated, ainda contém `fillna(0)` e direction encoding; não deve voltar ao fluxo |

## 4. Inventário Técnico

| Componente | Arquivo | Função/Classe | Responsabilidade | Produção? | Apenas UI? | Morto/órfão? | Risco |
|---|---|---|---|---|---|---|---|
| PI API | `backend/app/api/profile_intelligence.py` | endpoints `/overview`, `/run`, `/settings`, suggestions, audit | Orquestra API/UI | Sim | Não | Não | Flags órfãs e overview superficial de disponibilidade |
| PI Engine | `backend/app/services/profile_intelligence_service.py` | `ProfileIntelligenceService.run` | Executa analyzers e gera sugestões | Sim | Não | Não | Não registra source engine normalizado |
| Indicator Lift | `backend/app/services/indicator_lift_service.py` | `IndicatorLiftAnalyzer` | Buckets/lift | Sim | Não | Não | Agrega globalmente por usuário, sem profile attribution |
| Counterfactual seeds | `counterfactual_combination_service.py` | `CounterfactualCombinationMiner` | Discovery + validation de seeds | Sim | Não | Não | Missing feature passa |
| Dynamic combinations | mesmo arquivo | `DynamicCombinationGenerator` | Combina top buckets | Sim | Não | Não | Sem validation |
| Association rules | `association_rules_service.py` | `AssociationRulesEngine` | Apriori/fallback | Sim, opcional | Não | Não | Sem validation/action mapping |
| Optuna PI | `optuna_profile_search_service.py` | `OptunaProfileSearchService` | Otimiza thresholds de regras | Sim, opcional | Não | Não | Validation args não usados |
| Anthropic | `profile_ai_explanation_service.py` | `ProfileAIExplanationService` | Explica sugestões | Sim, opcional | Não | Não | Papel explicativo está separado |
| Suggestion generator | `profile_suggestion_service.py` | `ProfileSuggestionService` | Combinação → sugestão | Sim | Não | Não | Falta source registry |
| Profile creation | `profile_create_service.py` | `create_from_suggestion` | Sugestão → profile shadow | Sim | Não | Não | Test suite quebrada |
| PI Auto-Pilot | `profile_intelligence_autopilot_service.py` | `run_cycle` | Candidatos, promoção, rollback | Sim | Não | Não | Promoção live automática |
| Auto-Pilot legado | `autopilot_engine.py` | `run_autopilot_cycle` | Mutação de regras/config | Sim | Não | Não | Métrica global e escrita real |
| ML features | `backend/app/ml/feature_extractor.py` | `FEATURE_COLUMNS`, `build_training_dataframe` | Features/labels/split | Sim | Não | Não | Label e escopo exigem governança |
| ML trainer | `backend/app/ml/trainer.py` | `WinFastTrainer` | XGBoost + Optuna | Sim | Não | Não | Bom tratamento de NaN; promoção incompleta |
| Trainer job | `ml_trainer/job.py` | `main`, `_train_for_profile` | Dataset e persistência | Sim | Não | Não | Global mistura profiles; scoped não usado runtime |
| Model loader | `gcs_model_loader.py` | `GCSModelLoader` | Carrega blob active | Sim | Não | Não | Sem unicidade DB |
| Prediction | `prediction_service.py` | `WinFastPredictor` | Inferência e threshold | Sim | Não | Não | Modelo scoped/threshold global podem divergir |
| LightGBM | API settings/overview | nenhum trainer/predictor | Somente flag e import probe | Não | Parcial | Órfão | UI enganosa |
| CatBoost | schema/settings/UI | nenhum import/trainer/predictor | Somente flag | Não | Sim | Órfão | UI enganosa |

## 5. Auditoria dos Modelos

### 5.1 XGBoost

XGBoost é o único engine real:

- Declarado em `backend/requirements.txt` e `ml_trainer/requirements_trainer.txt`.
- Treinado por `WinFastTrainer`.
- Persistido em `ml_models.model_blob`.
- Carregado pelo `GCSModelLoader`.
- Pode atuar como gate L3 quando habilitado.

Produção possui `11` registros em `ml_models` `[query]`: `1` active, `9` retired e `1` rejected `[calc: lista literal da query]`. Não há duplicidade active no snapshot consultado.

O modelo active é global e não possui `feature_schema_version`, `feature_count`, `dataset_hash` ou `query_hash` preenchidos `[query]`. O modelo rejected mais recente possui schema/feature count, mas também não possui dataset/query hash.

### 5.2 LightGBM

Evidências:

- Flag no schema: `backend/app/schemas/profile_intelligence.py:41`.
- Default na API: `backend/app/api/profile_intelligence.py:48`.
- Probe de import: `backend/app/api/profile_intelligence.py:134`.
- Toggle na UI: `frontend/app/profile-intelligence/page.tsx:1563`.
- Ausente de requirements e Dockerfiles.
- Sem trainer, predictor, artifact, teste, evento ou sugestão.
- Modelos no DB: `0` `[query]`.

Veredito: não funcional, não integrado, contribuição zero.

### 5.3 CatBoost

Evidências:

- Flag no schema: `backend/app/schemas/profile_intelligence.py:42`.
- Default na API: `backend/app/api/profile_intelligence.py:49`.
- Toggle na UI: `frontend/app/profile-intelligence/page.tsx:1564`.
- Nem sequer há probe de import equivalente ao LightGBM.
- Ausente de requirements, Dockerfiles, trainer, predictor, artifact e testes.
- Modelos no DB: `0` `[query]`.

Veredito: não funcional, não integrado, contribuição zero.

### 5.4 Champion/Challenger

Pontos positivos:

- Trainer global compara AUC, precision, F1 e FPR com o modelo active.
- Challenger rejeitado não aposenta o champion.
- O snapshot do DB não contém champions duplicados.

Lacunas:

- `status='active'` é a única representação de champion.
- Não há `model_type`.
- Não há constraint parcial única para champion global ou por profile.
- Não há escopo por regime/skill.
- A migração cria somente índice normal em `(model_scope, profile_id, status)` (`078_ml_models_profile.py:58-59`).
- O caminho operacional não passa `profile_id`, logo models scoped não são usados.
- `_get_threshold` busca qualquer active sem escopo (`prediction_service.py:27`).

### 5.5 Risco de múltipla decisão

Hoje: baixo para LightGBM/CatBoost porque não executam.  
Futuro: crítico se forem adicionados sem registry e constraint.

Arquitetura mínima:

```text
model_registry
  model_type: xgboost | lightgbm | catboost
  scope: global | profile | profile_regime_skill
  status: candidate | challenger | champion | rejected | archived

production_champion
  profile_id
  regime
  skill
  active_model_id
  UNIQUE(profile_id, regime, skill)
```

## 6. Auditoria do Dataset

### Origem e segregação

O pipeline atual usa `shadow_trades.features_snapshot`.

- Trainer profile-scoped filtra `source='L3' AND profile_id=:profile_id` e possui assert anti-mixing.
- Trainer global filtra somente por `source`.
- Endpoint `/api/ml/train` não filtra user/profile.
- O dataset global e o dataset por profile coexistem, mas a escolha não é visível na UI nem registrada em toda inferência.

Produção:

- Total de shadows no primeiro snapshot: `6.891` `[query]`.
- Sem `profile_id`: `4.821` `[query]`.
- Por source:
  - L3: `2.134`, com `1.856` sem profile `[query]`.
  - L3_LAB: `1.872`, com `80` sem profile `[query]`.
  - L1_SPECTRUM: `1.758`, todos sem profile `[query]`.
  - L3_SIMULATED: `613`, todos sem profile `[query]`.
  - L3_REJECTED: `514`, todos sem profile `[query]`.

Conclusão: dataset global é de fato global. Treino por profile só é possível para subconjuntos com attribution completa.

### Leakage

Pontos positivos:

- `score`, derivados de score e `signal_direction` estão em `ML_EXCLUDED_FIELDS` (`feature_extractor.py:97`).
- O filtro existe em treino e inferência.
- `outcome`, `pnl_pct`, `exit_price` e `max_profit` não foram encontrados dentro de `features_snapshot` `[query]`.

Ponto de atenção:

- `score` existe em `1.010` snapshots `[query]`, mas é removido pelo extractor. A defesa está funcionando; qualquer consumidor alternativo continua sendo risco.

### NaN

Pipeline atual:

- Preserva NaN.
- Descarta linhas acima do limite de missingness.
- XGBoost usa `missing=NaN`.

Código deprecated:

- `dataset_builder.py` usa `fillna(0)` e direction encoding.
- O próprio módulo emite `DeprecationWarning`.
- Deve ser removido ou tornado impossível de importar em produção.

### Cobertura literal

Snapshot consultado:

| Feature | Presença |
|---|---:|
| total snapshots não nulos | `6.893` `[query]` |
| RSI | `6.685` `[query]` |
| ADX | `6.685` `[query]` |
| taker_ratio | `6.577` `[query]` |
| volume_delta | `6.577` `[query]` |
| ATR% | `1.725` `[query]` |
| VWAP distance | `1.724` `[query]` |

Não foi calculada significância por feature nesta auditoria. Portanto, qualquer conclusão de valor preditivo individual é `NÃO DISPONÍVEL`.

## 7. Auditoria do Labeling

Definição atual:

```text
WIN_FAST = outcome == TP_HIT
           AND holding_seconds <= ml_win_fast_threshold_seconds
```

Fallback sem outcome:

```text
net_return_pct > MIN_WIN_PNL_PCT
AND holding_seconds dentro da janela
```

Produção:

- Fechados: `5.710` `[query]`.
- Fechados sem holding: `0` `[query]`.
- TP_HIT com PnL não positivo: `0` `[query]`.
- SL_HIT com PnL não negativo: `0` `[query]`.
- TIMEOUT: `69` `[query]`.

Conclusão: a consistência literal TP/SL está boa no snapshot. A definição trata slow wins como negativos por design. Essa decisão é defensável, mas deve ser versionada como `label_version` e comparada com um target econômico alternativo.

Inconsistência de testes:

- Dois testes esperam WIN sem fornecer `holding_seconds`.
- O código atual exige holding e retorna label `0`.
- Deve-se decidir se ausência de holding significa “dropar row” ou “negative”; hoje significa “negative”, o que pode introduzir viés silencioso em dados incompletos.

Proposta canônica:

```text
label_version
entry_timestamp
tp_first_at
sl_first_at
timeout_at
first_barrier
holding_seconds
gross_return_pct
fee_pct
net_return_pct
source_simulation
profile_id
regime
skill
```

## 8. Auditoria do Auto-Pilot

### 8.1 Auto-Pilot legado

Fluxo:

```text
profile habilitado
  → performance window
  → regime/skill
  → rule insights
  → ajustes de score/minimum_score/block_rules/entry_triggers
  → possível geração Anthropic de config
  → snapshot + audit
  → persistência se dry_run=false
```

Pontos seguros:

- Defaults são dry-run.
- Kill-switch e scope_profile_id existem.
- Há clamps e allowlist.
- Há snapshots e rollback manual.
- Testes de conectividade/clamp: `21 passed` `[test]`.

Pontos perigosos:

- Produção sobrescreve defaults seguros: escrita real + full authority.
- Performance não filtra profile.
- Chamada não passa user.
- `scope_profile_id` controla onde escreve, mas não qual dataset mede.
- Várias dimensões ajustam `config_profiles` globais do usuário, não necessariamente `profile.config`.
- Audit trail é parcial para ações analíticas.

Produção:

- Audit rows: `20` `[query]`.
- `MUTATED`: `6` `[query]`.
- `ROLLED_BACK`: `1` `[query]`.
- Sem before: `9` `[query]`.
- Sem after: `9` `[query]`.
- Sem version: `11` `[query]`.
- Profiles com flag `auto_pilot_enabled`: `0` `[query]`.

O contraste entre `0` profiles habilitados e ações MUTATED deve ser investigado: os logs podem ser históricos, testes ou execução por caminho diferente.

### 8.2 Auto-Pilot de Profile Intelligence

Pontos seguros:

- Settings por usuário.
- Ciclos idempotentes.
- Candidatos isolados em shadow.
- Deduplicação semântica.
- Cooldown de famílias perdedoras.
- Audit append-only por trigger SQL.
- Rollback troca a associação para o profile anterior.
- Não edita a config do incumbent.

Risco:

- Quando gates e métricas aprovam, o código muda a watchlist e define live automaticamente.
- Não exige aprovação humana por candidato.
- Não há source model normalizado.
- Thresholds de promoção podem ser atualizados, mas não são versionados em um registry de política.

Produção:

- Habilitado: `true` `[query]`.
- Candidatos `SHADOW_COLLECTING`: `29` `[query]`.
- Promoções até o snapshot: `0` `[query]`.
- Eventos `CANDIDATE_CREATED`: `29` `[query]`.

Recomendação imediata: manter a coleta shadow, mas bloquear transição para `LIVE` até revisão humana e correção de atribuição/validation.

## 9. Auditoria do Profile Intelligence

### Overview

A API retorna os campos principais pedidos:

- profiles analisados
- trades fechados
- base win rate
- melhor profile
- melhor combinação
- combinações
- sugestões pendentes
- alta confiança
- total de runs
- status

Produção:

- Runs concluídos em 30 dias: `8` `[query]`.
- Último run: status `completed`, profiles `25`, shadows `5.055`, fechados `3.877` `[query]`.
- Eventos do último run incluem todas as fases e não incluem evento de erro `[query]`.

### Indicadores com Melhor Performance

O modelo possui coluna `source_profiles`, e a UI tenta renderizá-la. Porém:

- A query do analyzer não seleciona `profile_id`/`profile_name`.
- O insert não preenche `source_profiles`.
- `456/456` registros estão sem source profiles `[query]`.

Logo, a UI não consegue listar todos os profiles associados.

Não existe botão seguro por indicador para escolher:

- profile
- target section
- before/after
- confirmação
- rollback

O botão existente cria um profile inteiro a partir de uma sugestão, em modo shadow.

### Dynamic Combinations

- Gera combinações de 2, 3 e 4 buckets.
- Usa top 20 buckets.
- Mínimo de apenas 5 matches para persistir.
- Não valida em holdout.
- Missing feature é tratada como pass.
- `2.152` combinações persistidas; `0` com validation `[query]`.

Veredito: exploratório. Não elegível para aplicação ou promoção automática.

### Association Rules

- Suporte, confidence, lift, leverage e conviction são suportados.
- Consequentes incluem WIN, LOSS, SL_HIT, TP_15M, TP_30M.
- Não há validation temporal.
- Não há profile/regime/symbol attribution.
- `20` regras persistidas, `0` com validation `[query]`.

Veredito: exploratório; não acionável automaticamente.

### Anthropic

O papel atual é predominantemente explicativo:

- Recebe dados já calculados.
- Atualiza `ai_explanation`.
- Registra provider/model no audit.
- Eventos com provider/model: `55/142` `[query]`.

Não deve se tornar fonte única de mutação. No Auto-Pilot legado, `preset_ia_service` participa da geração da nova config; isso exige validação estrutural e diff obrigatório.

### Optuna Profile Search

- Otimiza thresholds de regras, não modelos LightGBM/CatBoost.
- Usa TPE com seed.
- Search em discovery.
- Argumentos `validation_start` e `validation_end` não são consumidos.
- Resultados não recebem validation metrics.
- Resultados Optuna não entram diretamente no suggestion generator porque não preenchem todos os campos de elegibilidade.

Veredito: benchmark exploratório parcial.

### Sugestões

Produção:

- Total: `81` `[query]`.
- Pending: `80` `[query]`.
- Created: `1` `[query]`.
- Missing run_id: `0` `[query]`.
- Missing source_combination_id: `0` `[query]`.
- Missing source_profiles: `81` `[query]`.
- Missing confidence: `0` `[query]`.
- Missing evidence: `0` `[query]`.

O fluxo de criação força SHADOW_ONLY no endpoint manual, o que é positivo. Porém, os testes desse serviço estão desatualizados/quebrados.

## 10. UI/UX

### Problemas confirmados

- LightGBM e CatBoost aparecem como toggles normais.
- A dica “Requer pacote instalado no worker” não informa “não implementado”.
- O payload manual contém Association Rules/Optuna/AI/Dynamic, mas não contém LightGBM/CatBoost.
- Não mostra champion ativo por profile/regime/skill.
- Sugestões não mostram source model, dataset version ou feature schema.
- Indicadores não recebem profiles associados.
- Não há ação segura por indicador.
- Não há aviso de “flag ligada, backend sem implementação”.

### Proposta

Substituir toggles por:

```text
ML Engine Mode
  XGBoost only
  Benchmark all (offline)

Production Champion
  model + version + scope + threshold

Unavailable challengers
  LightGBM — not installed / not implemented
  CatBoost — not installed / not implemented
```

Em cada sugestão mostrar:

```text
source_type
source_run_id
source_model_type/id/version
dataset_version
profile population
discovery window
validation window
N
confidence interval
expected impact
before/after
rollback
```

## 11. Queries SQL Executadas

O arquivo reproduzível é:

`docs/ML_AUTOPILOT_PROFILE_INTELLIGENCE_AUDIT_READONLY_2026-06-19.sql`

Todas as consultas foram executadas dentro de transação read-only. Nenhuma migration ou DML foi executada.

### Matriz das perguntas solicitadas

| # | Pergunta | Resultado |
|---:|---|---|
| 1 | Quantos profiles? | `46` `[query]` |
| 2 | Profiles com scoring? | `46` `[query]` |
| 3 | Profiles com block_rules? | `46` `[query]` |
| 4 | Profiles com entry_triggers? | `46` `[query]` |
| 5 | Alterados pelo Auto-Pilot? | `6` ações MUTATED; profiles distintos `NÃO DISPONÍVEL` |
| 6 | Sugestões pendentes? | `80` `[query]` |
| 7 | Aplicadas/criadas? | `1` com status created `[query]` |
| 8 | Revertidas? | Status de sugestão reverted não encontrado; Auto-Pilot legado `1` rollback |
| 9 | model_type por sugestão? | Campo não existe |
| 10 | Sugestões sem model_type? | Todas, por ausência de campo |
| 11 | Sugestões sem run_id? | `0` `[query]` |
| 12 | Sugestões sem profile_name? | Campo suggested_profile_name é non-null; `0` esperado por schema |
| 13 | Sem confidence? | `0` `[query]` |
| 14 | Sem evidence_count? | Campo não existe; evidence JSON existe em todas |
| 15 | Sem expected_impact? | Campo não existe |
| 16 | Runs LightGBM? | `0` evidências/modelos/eventos `[query]` |
| 17 | Runs CatBoost? | `0` evidências/modelos/eventos `[query]` |
| 18 | Runs XGBoost? | `11` model rows compatíveis com XGBoost/blob `[query]` |
| 19 | Dois champions ativos? | `0` grupos duplicados no snapshot `[query]` |
| 20 | Champions sem profile? | Champion global tem profile_id null por design |
| 21 | Trades sem profile_name/id? | `4.821` sem profile_id no primeiro snapshot `[query]` |
| 22 | Trades sem source? | `0` `[query]` |
| 23 | Trades sem outcome? | `1.181` `[query]`, majoritariamente abertos |
| 24 | Trades duplicados? | `675` grupos pela chave exploratória profile/symbol/source/hour `[query]`; não prova duplicação semântica |
| 25 | Features com null alto? | ATR/VWAP têm cobertura baixa relativa `[query]` |
| 26 | Staleness? | `NÃO DISPONÍVEL`; timestamps por feature não existem |
| 27 | Features proibidas? | score em `1.010` snapshots, mas extractor o remove `[query+code]` |
| 28 | Datasets misturando profiles? | Trainer global mistura por design; trainer scoped possui assert |
| 29 | score usado como feature? | Não no pipeline atual; guardrail explícito |
| 30 | direction usada como feature? | Não no pipeline atual; `signal_direction` excluída |

## 12. Testes Executados e Lacunas

### Resultados

| Conjunto | Resultado |
|---|---|
| Auto-Pilot connectivity/clamps | `21 passed` `[test]` |
| PI indicator/autopilot/create-profile | `28 passed`, `18 failed` `[test]` |
| Strategy Lab/features/labeling | `36 passed`, `5 failed` `[test]` |
| TypeScript `npx tsc --noEmit` | passou `[test]` |

Três dos cinco failures ML iniciais eram dependências ausentes no venv temporário; após instalar joblib/psycopg2, dois testes de loader passaram e um continuou bloqueado por `httpx` ausente. Os dois failures de labeling são inconsistência real entre teste e semântica atual de `holding_seconds`.

### Testes obrigatórios a adicionar

1. `compute_performance_window` falha sem `user_id` e `profile_id`.
2. Auto-Pilot nunca altera profile A usando dados de profile B.
3. Dry-run é requisito para qualquer ambiente sem aprovação explícita.
4. Apenas um champion ativo por escopo.
5. Modelo scoped usa threshold/model_id do mesmo row.
6. Caller operacional passa `profile_id`.
7. Sugestão exige source_type, source_run_id e population.
8. Dynamic combination exige validation.
9. Missing feature não conta como regra satisfeita.
10. Association Rule LOSS não vira signal positivo.
11. Optuna usa holdout temporal.
12. PI Auto-Pilot não ativa live sem aprovação humana.
13. Testes do `ProfileCreateService` alinhados aos imports lazy atuais.
14. Row sem `holding_seconds` é descartada ou explicitamente rotulada por política versionada.
15. LightGBM/CatBoost toggle não pode ser salvo como enabled enquanto indisponível.

## 13. Plano de Correção

### Imediato

| Item | Problema | Ação | Migration | Retrain | Rollback |
|---|---|---|---|---|---|
| P0-1 | Auto-Pilot legado usa métrica global | Kill-switch ou dry-run; corrigir filtros | Não | Não | Config |
| P0-2 | PI Auto-Pilot pode promover live | Bloquear `LIVE_ACTIVATED` sem aprovação | Possível status/approval fields | Não | Associação existente |
| P0-3 | Dynamic sem validation | Tornar inelegível para sugestão/Auto-Pilot | Não | Não | Não |
| P0-4 | Toggles órfãos | Desabilitar UI/API e warning explícito | Não | Não | Não |

### Curto prazo

| Item | Solução |
|---|---|
| P1-1 | Adicionar source registry à sugestão |
| P1-2 | Preencher source_profiles nos stats/combinações |
| P1-3 | Constraint parcial única para champion |
| P1-4 | Model/threshold lookup atômico e scoped |
| P1-5 | Separar dataset global de profile/regime/skill |
| P1-6 | Reparar testes de ProfileCreateService |

### Médio prazo

- Model registry completo.
- Champion por profile/regime/skill.
- Walk-forward validation.
- Confidence interval e multiple-testing correction.
- Versionamento de policy/threshold/dataset/label.
- Painel de forward scoring por model_id.

### Futuro

- LightGBM como challenger numérico.
- CatBoost somente quando categóricas forem parte explícita do schema.
- Benchmark all estritamente offline.
- Promoção manual ou policy-gated com rollback comprovado.

## 14. Migrations Necessárias

1. `ml_models.model_type`.
2. `ml_models.status` normalizado para candidate/challenger/champion/rejected/archived.
3. Unique parcial global:

```sql
CREATE UNIQUE INDEX ... ON ml_models ((1))
WHERE status='champion' AND model_scope='global';
```

4. Unique parcial scoped:

```sql
CREATE UNIQUE INDEX ... ON ml_models (profile_id, market_regime, strategy_skill)
WHERE status='champion';
```

5. `profile_suggestions`: source_type, source_model_type/id/version, dataset_version, feature_schema_version, source_run_id, target_section, target_field, diff_json, expected_impact, evidence_count, rollback_payload.
6. `profile_intelligence_autopilot_candidates`: approval_status, approved_by, approved_at.
7. Audit append-only com before/after/diff para promoção e rollback.
8. Feature freshness metadata, se staleness precisar ser auditável.

Nenhuma migration foi executada nesta auditoria.

## 15. Checklist de Correção

- [ ] Colocar Auto-Pilot legado em dry-run/kill-switch.
- [ ] Exigir user_id/profile_id nas métricas.
- [ ] Bloquear promoção live automática do PI Auto-Pilot.
- [ ] Desabilitar toggles LightGBM/CatBoost.
- [ ] Exibir warning de backend/dependência ausente.
- [ ] Preencher source_profiles.
- [ ] Adicionar source registry às sugestões.
- [ ] Validar Dynamic/Association/Optuna fora da amostra.
- [ ] Corrigir missing-feature-as-pass.
- [ ] Adicionar constraint de champion único.
- [ ] Unificar lookup de model + threshold + model_id.
- [ ] Passar profile_id na inferência.
- [ ] Corrigir testes quebrados.
- [ ] Versionar label e política de holding ausente.
- [ ] Validar forward scoring antes de usar ML como gate.

## 16. Recomendação Objetiva do que Desligar

Desligar/bloquear imediatamente:

1. Escrita real do Auto-Pilot legado até corrigir segregação.
2. Promoção automática do PI Auto-Pilot para live.
3. Toggles LightGBM e CatBoost.
4. Elegibilidade automática de Dynamic Combinations sem validation.

Manter:

1. Profile Intelligence em modo analítico.
2. Geração de candidatos shadow.
3. Anthropic como camada explicativa.
4. XGBoost champion global atual, sem ampliar seu escopo, até validação de forward scoring.
5. Counterfactual seeds com discovery+validation, ainda sujeitos a revisão.

## 17. Veredito Final

É erro ter XGBoost, LightGBM e CatBoost no mesmo sistema? Não.

É erro permitir ambos ligados ao mesmo tempo? No estado atual, sim do ponto de vista de UX/configuração, porque as flags não representam funcionalidade. Em uma implementação futura, “benchmark all” pode existir, mas challengers não podem decidir.

O sistema atual tem governança suficiente? Não. O trainer XGBoost possui parte da governança, mas faltam constraint, model type, escopo completo e atribuição operacional.

O Auto-Pilot pode aplicar regras com segurança? O legado não, enquanto usar métricas globais com escrita real. O PI Auto-Pilot pode continuar em shadow, mas não deve promover live automaticamente.

O Profile Intelligence está pronto para orientar produção? Está pronto para exploração e geração de hipóteses. Não está pronto para autorizar produção sem revisão.

O que deve ser bloqueado imediatamente? Escrita do Auto-Pilot legado, promoção live automática e toggles órfãos.

O que deve ser mantido? Runs analíticos, candidates shadow, audit append-only, rollback e XGBoost champion atual.

O que deve ser refeito? Segregação de dados, suggestion registry, champion registry, validação fora da amostra e a ponte entre model scoped e inferência.

## 18. Ledger de Evidências Numéricas

| Número reportado | Origem | Valor literal |
|---|---|---|
| PI runs 30d = 8 | `[query]` | `status=completed, count=8` |
| Último PI run profiles = 25 | `[query]` | `total_profiles=25` |
| Último PI run shadows = 5.055 | `[query]` | `total_shadow_trades=5055` |
| Último PI run fechados = 3.877 | `[query]` | `total_closed_trades=3877` |
| Sugestões = 81 | `[query]` | `total=81` |
| Sugestões sem source_profiles = 81 | `[query]` | `missing_source_profiles=81` |
| Indicator stats sem source_profiles = 456 | `[query]` | `total=456, with_source_profiles=0` |
| Dynamic combinations = 2.152 | `[query]` | `counterfactual_dynamic count=2152` |
| Dynamic com validation = 0 | `[query]` | `with_validation=0` |
| Association rules = 20 | `[query]` | `association_rule count=20` |
| ML models = 11 | `[query]` | `total=11` |
| LightGBM models = 0 | `[query]` | `lightgbm=0` |
| CatBoost models = 0 | `[query]` | `catboost=0` |
| Active duplicate groups = 0 | `[query]` | empty result |
| Legacy audit rows = 20 | `[query]` | `total=20` |
| Legacy MUTATED = 6 | `[query]` | `action=MUTATED, count=6` |
| Legacy missing before = 9 | `[query]` | `missing_before=9` |
| Legacy missing after = 9 | `[query]` | `missing_after=9` |
| PI Auto-Pilot candidates shadow = 29 | `[query]` | `state=SHADOW_COLLECTING, count=29` |
| Profiles = 46 | `[query]` | `profiles=46` |
| Profiles Auto-Pilot enabled = 0 | `[query]` | `autopilot_enabled=0` |
| Shadows = 6.891 | `[query]` | `total=6891` no primeiro snapshot |
| Shadows sem profile = 4.821 | `[query]` | `missing_profile_id=4821` |
| Closed labels = 5.710 | `[query]` | `closed=5710` |
| Closed sem holding = 0 | `[query]` | `closed_missing_holding=0` |
| TP com PnL não positivo = 0 | `[query]` | `tp_non_positive_pnl=0` |
| SL com PnL não negativo = 0 | `[query]` | `sl_non_negative_pnl=0` |
| Snapshots com score = 1.010 | `[query]` | `with_score=1010` |
| HTTP 200 = 264 | `[query]` | `Name=200 Count=264` |
| HTTP 401 = 36 | `[query]` | `Name=401 Count=36` |
| Auto-Pilot tests = 21 passed | `[test]` | `21 passed in 0.50s` |
| PI tests = 28 passed, 18 failed | `[test]` | pytest summary |
| ML/label tests = 36 passed, 5 failed | `[test]` | pytest summary |

