# Auditoria Técnica Ampla — Scalpyn (POOL → L1 → L3 → Shadow → ML → Profile Intelligence → Auto-Pilot)

**Data:** 2026-06-24
**Tipo:** Auditoria read-only (código + banco Postgres de produção via proxy Railway). Nenhuma alteração foi feita em código, banco, migrations ou configuração.
**Método:** 3 sub-agentes de leitura de código (read-only, sem escrita) + queries SQL diretas (`SELECT`/`information_schema`/`pg_constraint`/`pg_indexes`) executadas pelo auditor principal contra o Postgres de produção (`railway` db, host `zephyr.proxy.rlwy.net:23422`, sessão em `SET TRANSACTION READ ONLY`).
**Convenção de evidência:** todo número vem de uma query colada ou de um trecho de código citado com caminho+linha. Onde não há evidência, está escrito "não confirmado". Onde uma relação esperada não existe nos dados, está escrito "RELAÇÃO AUSENTE".

---

## 1. Resumo executivo

O Scalpyn está, hoje, operando **inteiramente em modo shadow/simulado**: nenhum profile tem `live_trading_enabled=true` nem `auto_pilot_enabled=true` (109/109 profiles, `[query]` seção 9.1), e o ML Gate de produção está desligado por padrão (`ML_GATE_ENABLED` default `false`, `[arquivo] pipeline_scan.py:2878`). Isso é consistente com a política "nenhum modelo promove automaticamente para ALLOW/BLOCK" documentada no projeto.

Os números reais do Shadow Portfolio (11.190 trades, `[query]`) mostram um sistema com **win rate de 38,13%** (TP vs. todos os fechados) e **P&L total de -15.612,59 USDT** desde 2026-06-09. A causa central não é "sorte" — é uma combinação de quatro problemas estruturais, todos confirmados por dado ou código:

1. **Os dois modelos ML atualmente ativos (v44 CatBoost/L3_PROFILE, v46 LightGBM/L1_SPECTRUM) são anti-preditivos no holdout de teste** (ROC AUC de teste = 0,4260 e 0,4546, ambos < 0,5 — piores que aleatório), apesar de AUC de validação razoável (0,69 e 0,61). Isso é overfitting confirmado por dado, não suspeita. `[query] metrics_json`.
2. **Não existe, na prática, nenhuma ligação viva entre score de ML e resultado do Shadow.** `ml_probability` é NULL em 100% das 11.109 linhas de `shadow_trades`; `ml_model_id` é NULL em 100%; `final_priority_score`/`orchestrator_payload` só existem em 300 linhas (2,7%), todas de um único backfill manual executado uma vez em 2026-06-20/21 e nunca repetido. `[query] watchlist_link`.
3. **O label `is_tp_4h_v1` mistura "vitória rápida" com "vitória lenta" como se fossem a mesma coisa que perda.** De 3.782 trades com `outcome='TP_HIT'`, 848 (22,4%) levaram mais de 4h e são rotulados como label=0 — exatamente igual a um SL_HIT. `[query] tp_hit_holding_vs_4h`.
4. **Todo profile nomeado no banco está com P&L negativo.** O único grupo com P&L próximo de zero (-5,03 USDT) é justamente o conjunto de trades **sem profile_id** (5.107 de 9.919 fechados, 51,5%). Todos os 38 profiles nomeados auditados têm `pnl_total` negativo, de -10,00 a -1.380,54 USDT. `[query] by_profile_perf`.

Além disso, o Auto-Pilot "clássico" (Sistema A, `autopilot_engine.py`) está configurado em produção com `dry_run_mode=false` e `autopilot_full_authority=true` — ou seja, tem autorização plena para alterar profiles reais sem simulação — mas não fez nenhuma mutação real desde 2026-06-15 (9 dias antes desta auditoria), porque nenhum profile tem `auto_pilot_enabled=true`. O sistema está "armado" mas inerte. `[query] autopilot_guardrails_config`, `[query] autopilot_audit_action_counts`.

A infraestrutura para a arquitetura desejada pelo usuário (POOL → L1/ML ranking → watchlist ML de alta pontuação → L3 → Shadow → feedback → versionamento → promoção) **já existe em parte**: há Decision Orchestrator com fórmula de combinação de scores, há sistema de candidatos versionados no Profile Intelligence Autopilot (Sistema B), há audit trail detalhado. Mas as peças não estão conectadas: o Orchestrator não alimenta a decisão ao vivo, a watchlist de alta pontuação ML não existe, e nenhuma sugestão do Profile Intelligence jamais passou por validação completa (101/101 sugestões = `validation_status='blocked_no_validation'`).

Não existe módulo "Auto-Calibrator" em lugar nenhum do código — confirmado por busca exaustiva por dois agentes independentes.

---

## 2. Diagnóstico central

O problema não é "o ML é ruim" isoladamente — é que **o sistema nunca fechou o loop de feedback**. Hoje:

- Shadow gera 11 mil trades reais, com indicadores de entrada/saída bem capturados (cobertura de RSI/ADX ~96,7%, `[query] feature_snapshot_key_coverage`).
- Mas o ML treinado a partir desses trades usa um label (`is_tp_4h_v1`) que penaliza trades lentos-mas-vencedores e não considera MAE, fees ou tempo-até-TP como variável contínua.
- Os modelos resultantes (v44/v46) são ativados mesmo com teste anti-preditivo, porque não há gate de promoção que bloqueie ativação quando `test_roc_auc < 0.5` (não confirmado a existência de tal gate no código lido).
- O score desses modelos não volta para a decisão L3 (ML Gate off) nem para o ranking de watchlist (não existe watchlist ML).
- O Profile Intelligence analisa o Shadow e gera sugestões, mas 100% delas são bloqueadas no próprio gate de validação interno (`validation_status='blocked_no_validation'`), e as 2 exceções que viraram profile real (`status='applied'`) o fizeram sem que o campo de auditoria `applied_at` fosse preenchido — e os dois profiles resultantes (`rsi_gte_72_AND_vol_spike_gt_2_5_AND_ema50_gt_ema200_false`, `rsi_gte_72_AND_vol_spike_gte_1_2_AND_depth_gte_20k`) estão hoje com win rate de 0%-11,43% e P&L negativo. `[query] ps_applied_rows`, `[query] by_profile_perf`.
- O Auto-Pilot (ambos os sistemas) está tecnicamente pronto para agir mas não tem nenhum profile habilitado a sofrer ação automática.

Em outras palavras: **o sistema está coletando dados de boa qualidade, mas não está usando esses dados para se corrigir.** O loop existe em pedaços desconectados.

---

## 3. Mapa real da arquitetura atual

### 3.1 POOL
- `backend/app/services/pool_service.py::apply_structural_pool_filter` (linhas 241-266) — filtro **puramente estrutural** (volume/spread/depth de `market_metadata`), sem indicadores preditivos. Confirma a exigência arquitetural do projeto.
- `backend/app/services/pool_selection.py::apply_pool_discovery_filters` (linha 81) — filtros adicionais de volume/market_cap a partir de thresholds do profile.
- Lê: `pool_coins`, `market_metadata`. Não escreve nada nesses dois arquivos.
- Risco: helpers deprecados (`get_approved_pool_symbols`, linha 187) mantidos como shim sem data de remoção confirmada.

### 3.2 L1
- `backend/app/tasks/pipeline_scan.py`, bloco L1/L2/POOL (linhas 2650-2753): `apply_profile_filters=False` é passado para `_apply_level_filter` (linha 816) — em teoria, sem filtro rígido.
- **Ressalva confirmada por código:** `evaluate_rejections` (`pipeline_rejections.py:704`) ainda roda antes e usa `profile_config.filters.conditions` reais via `RuleEngine` — ou seja, se a watchlist L1 tiver `conditions` configuradas, elas filtram antes mesmo de chegar ao trecho "sem filtro". O comportamento real depende de configuração de banco por watchlist, não apenas do código.
- Cria shadows via `create_l1_spectrum_shadows` (linha 2730-2751), fonte `L1_SPECTRUM`.
- Escreve: `pipeline_watchlist_assets`, `shadow_trades`. Lê: `pipeline_watchlists`, `market_metadata`, `indicators`, `alpha_scores`.

### 3.3 L3
- `backend/app/tasks/pipeline_scan.py::_evaluate_l3_decisions` (1248), `_evaluate_l3_signals` (935); `backend/app/services/profile_engine.py::ProfileEngine` (137).
- **Risco confirmado:** se o profile não tem `entry_triggers`/`signals` configurados, `_evaluate_l3_signals` aprova **todos** os assets filtrados, ordenados por score (linhas 982-1002) — um "vazamento" de aprovação mascarado de comportamento intencional.
- ML Gate (CatBoost/LightGBM via `WinFastPredictor`) é opcional, gated por `ML_GATE_ENABLED` (env, default `false`) ou `pool_config.new_arch_l3_uses_ml_score` (config de banco).
- Decisão final: regra de profile primeiro; ML Gate pode rebaixar ALLOW→BLOCK, nunca o inverso (linhas 2959-2967).
- Escreve: `decisions_log`, `pipeline_watchlist_assets`, `opportunity_snapshots`, `shadow_trades`.

### 3.4 Shadow Portfolio
- Criação: `backend/app/services/shadow_trade_service.py::_create_from_decision` (620), com 6 fontes confirmadas em produção: `L3`, `L3_REJECTED`, `L1_SPECTRUM`, `L3_SIMULATED`, `L3_LAB`, `WATCHLIST_SPOT` (a 7ª constante de código, `WATCHLIST_SPOT`, não aparece nos dados atuais — `[query] by_source` só mostra 5 fontes ativas; **não confirmado** se `WATCHLIST_SPOT` está em uso hoje).
- Fechamento: `backend/app/tasks/shadow_trade_monitor.py::_advance_shadow` (757), `_finalize_outcome` (384). Convenção `SL_FIRST`: se TP e SL forem tocados na mesma candle, SL vence (pior caso) — gravado em `intrabar_convention`.
- TP/SL: modo `ATR_DYNAMIC` (fórmula `SL% = clamp(ATR% × 1.5, 0.5, 3.0)`, defaults de fallback) ou `FIXED` (de `spot_engine` config). Timeout default 1440 candles de 1m = 24h.
- Captura de indicadores: entrada via `decisions_log.metrics["indicators_snapshot"]` → `_build_features_snapshot` → `shadow_trades.features_snapshot`; saída via `exit_metrics.py::build_exit_snapshot` → `shadow_trades.features_snapshot_exit`.
- **Risco confirmado por dado:** não existe constraint UNIQUE sobre `decision_id` em `shadow_trades` (apenas índice btree não-único — `[query] indexes_shadow_trades`). 38 `decision_id` têm linhas duplicadas (44 linhas extras), 17 desses 38 com **outcomes conflitantes** no mesmo `decision_id` (ex.: `decision_id=4225`, símbolo ZEC_USDT, 4 linhas: SL_HIT, SL_HIT, SL_HIT, TP_HIT). `[query] dup_decision_detail`, `[query] dup_decision_full_rows`.

### 3.5 ML (treino e inferência)
- Treino real: `backend/app/services/ml_challenger_service.py::train_challengers` (749), chamado por `backend/app/tasks/profile_intelligence_job.py::_run_ml_challengers_if_enabled` (143).
- Feature/label engineering: `backend/app/ml/feature_extractor.py` — `build_training_dataframe` (320), registro de nomes de label `_LABEL_THRESHOLD_REGISTRY` (125-128).
- Inferência em produção: `backend/app/ml/prediction_service.py::WinFastPredictor` (singleton `predictor`, linha 188) — usado por `api/ml.py`, `api/watchlists.py`, `pipeline_scan.py`, `decision_orchestrator.py`.
- **Código morto confirmado:** `backend/app/ml/predict_service.py::PredictService` — nenhum chamador encontrado fora do próprio arquivo.
- **Risco confirmado:** seleção do modelo ativo em `WinFastPredictor._get_threshold` não filtra por `model_lane` (`WHERE status='active' ORDER BY activated_at DESC LIMIT 1`) — com 2 modelos `active` simultâneos hoje (v44 e v46, lanes diferentes), isso é uma ambiguidade real de seleção, não hipotética.
- `ml_trainer/job.py` existe como pipeline separado, cujo papel exato em relação a `ml_challenger_service.py` **não foi confirmado** nesta auditoria (grep por `is_tp_4h_v1`/`label_version` neste arquivo não retornou resultados).

### 3.6 Decision Orchestrator
- `backend/app/services/decision_orchestrator.py` (557 linhas, lido na íntegra por um dos agentes).
- Fórmula confirmada: `final_priority_score = (l1_weight/(l1_weight+l3_weight)) × p_l1_win + (l3_weight/(l1_weight+l3_weight)) × p_l3_profile_win`, com fallback para `p_l1_win` puro se CatBoost indisponível ou `source` fora de `{"L3","L3_LAB"}`.
- Pesos default no código: `DEFAULT_L1_WEIGHT=0.60`, `DEFAULT_L3_WEIGHT=0.40`, carregados de `config_profiles(config_type='orchestrator_weights')`.
- **Confirmado por query:** essa linha de config **não existe** no banco (`[query] orchestrator_weights_config` → `[]`) — o Orchestrator está, na prática, sempre usando os defaults hardcoded do código, nunca uma configuração ajustável real.
- **Não participa do fluxo ALLOW/BLOCK ao vivo** — só roda via `backfill_orchestrator_scores` (dry_run=True por default), e só foi executado uma vez, numa janela de ~26h em 2026-06-20/21, afetando 300 das 11.109 linhas de `shadow_trades` (264 de `L3`, 34 de `L3_LAB`, 2 de `L1_SPECTRUM`). `[query] final_priority_nonnull_sample`.

### 3.7 Profile Intelligence
Dois subsistemas com nomenclatura sobreposta na UI:
- **Engine de descoberta/sugestão** (`profile_intelligence_service.py`, `indicator_lift_service.py`, `profile_suggestion_service.py`) — analisa `shadow_trades`, gera `profile_suggestions`.
- **PI Autopilot / Sistema B** (`profile_intelligence_autopilot_service.py`, 2502 linhas) — cria candidatos clonados (`is_shadow_only=True`), nunca edita o profile incumbente direto; só promove via `activate_approved_candidate` chamado por endpoint de API após aprovação humana.

### 3.8 Auto-Pilot
Dois sistemas distintos, ambos chamados "Auto-Pilot" na UI:
- **Sistema A — clássico** (`autopilot_engine.py`, 2439 linhas + `tasks/autopilot.py`): por profile individual, toggle `profiles.auto_pilot_enabled`; pode fazer **UPDATE direto em `profiles`** se `dry_run_mode=false`.
- **Sistema B — PI Autopilot** (`profile_intelligence_autopilot_service.py` + `tasks/profile_intelligence_job.py`): ciclo `MONITOR_LIVE → REVIEW_SHADOW → CALIBRATE_L3 → CREATE_DISCOVERED → BLOCK_LEGACY_WAITING_LIVE`.
- Frequências confirmadas (`celery_app.py:602-623`): Sistema A a cada 21.600s (6h); engine PI a cada 86.400s (24h); monitor do PI Autopilot a cada 300s (5min).
- **Auto-Calibrator: confirmado ausente** em todo `backend/` e `ml_trainer/` por dois agentes independentes (buscas por `calibrat`, `AutoTuner`, `auto_tuner`). A única menção textual está num documento de especificação antigo (`docs/PROMPT_UNICO_AUDITORIA_L3_LIGHTGBM_CATBOOST_SHADOW_WATCHLISTS.md`), não em código.

---

## 4. Mapa real das tabelas

62 tabelas no schema `public` (`[query] SELECT table_name FROM information_schema.tables`). Migration head: `104_ml_metrics_json` (`[query] SELECT version_num FROM alembic_version`), consistente com a cadeia documentada na memória do projeto.

| Tabela | Linhas | Papel | Observação |
|---|---|---|---|
| `shadow_trades` | 11.190 | Núcleo de simulação | 96 colunas — tabela mais rica do schema, contém lineage completa (ml_model_id, watchlist_id, orchestrator_payload) mas majoritariamente NULL nesses campos |
| `decisions_log` | 53.960 | Decisões ALLOW/BLOCK do L3 | 99,97% ALLOW |
| `profiles` | 109 | Profiles de trading | 0 com `live_trading_enabled=true` |
| `ml_models` | 47 | Registro de modelos | 2 `active`, 21 `candidate`, 12 `retired`, 12 `rejected` |
| `pipeline_watchlists` | 105 | Watchlists do pipeline | 102 L3, 1 L1, 1 L2, 1 POOL |
| `pipeline_watchlist_assets` | 410 | Cache de universo atual | não é histórico, é upsert |
| `profile_suggestions` | 101 | Sugestões do PI | 100% `validation_status='blocked_no_validation'` |
| `profile_intelligence_runs` | 32 | Execuções do PI engine | 23 completed, 9 failed (28% falha) |
| `profile_intelligence_autopilot_candidates` | 91 | Candidatos do PI Autopilot | 61 DISABLED, 30 SHADOW_COLLECTING, 0 aprovados/ativados |
| `autopilot_audit_logs` | ~21 | Auditoria do Auto-Pilot clássico | 6 mutações reais, todas num único profile, última em 2026-06-15 |
| `indicators` | 2.678.857 | Série temporal bruta | tabela de cache/histórico |
| `indicator_snapshots` | **0** | — | **tabela órfã, vazia** |
| `opportunity_snapshots` | 67.611 | Snapshots de oportunidade L3 | 2026-06-17 → 2026-06-24 |
| `alpha_scores` | 14.595 | Scores de alpha | — |
| `pool_coins` | 88 | Universo POOL | — |
| `config_profiles` | 14 | Configuração por tipo | 1 linha ativa por `config_type`, sem histórico de versão na própria tabela |

---

## 5. Mapa real dos fluxos de dados (relacionamentos)

```
decisions_log.id (53.960 linhas)
  → shadow_trades.decision_id
    RELAÇÃO PARCIAL: apenas 5.283/53.960 (9,79%) das decisões têm shadow trade
    correspondente. [query] decisions_to_shadow_link
    Causa provável (não confirmado por código nesta sessão): apenas a fonte
    "L3" usa decision_id; as outras 5 fontes de shadow (L1_SPECTRUM, L3_LAB,
    L3_SIMULATED, L3_REJECTED) não derivam 1:1 de decisions_log.

shadow_trades.profile_id (UUID)
  → profiles.id
    RELAÇÃO PARCIAL: fonte L3 tem 38,2% NULL (2.022/5.291); fontes
    L1_SPECTRUM, L3_SIMULATED, L3_REJECTED têm 100% NULL (por design, não
    usam conceito de profile); L3_LAB tem apenas 3,1% NULL.
    [query] missing_profile_id_by_source
    Nota: a memória do projeto registrava "L3 66,64% NULL profile_id" em
    sessão anterior — o valor atual medido (38,2%) é mais baixo. Não
    confirmado o motivo da divergência (memória desatualizada vs. mudança
    real); recomenda-se não usar o valor antigo da memória sem revalidar.

shadow_trades.watchlist_id / source_watchlist_id (UUID)
  → pipeline_watchlists.id
    RELAÇÃO QUASE AUSENTE: 9.070/11.109 (81,6%) das linhas têm watchlist_id
    NULL. [query] watchlist_link

shadow_trades.ml_model_id (UUID)
  → ml_models.id
    RELAÇÃO AUSENTE: 11.109/11.109 (100%) NULL. Nenhum shadow trade tem
    registro de qual modelo ML (se algum) influenciou sua seleção.
    [query] watchlist_link

shadow_trades.ml_probability
    RELAÇÃO AUSENTE: 100% NULL. [query] ml_probability_nonnull_sample → []

shadow_trades.final_priority_score / orchestrator_payload
  → decision_orchestrator.compute_trade_score (código)
    RELAÇÃO QUASE AUSENTE: populado em apenas 300/11.109 (2,7%) das linhas,
    todas criadas entre 2026-06-20 18:25 e 2026-06-21 20:13 — uma única
    execução manual de backfill, nunca repetida. [query] final_priority_nonnull_sample

profile_suggestions.run_id (UUID)
  → profile_intelligence_runs.id
    Coluna existe no schema; cobertura de preenchimento não quantificada
    nesta sessão — não confirmado.

profile_suggestions.created_profile_id (UUID)
  → profiles.id
    RELAÇÃO MÍNIMA: apenas 2/101 (2,0%) sugestões geraram profile real.
    [query] ps_applied_rows
    Achado adicional: nessas 2 linhas, status='applied' e created_profile_id
    preenchido, mas applied_at é NULL nas duas — gap de auditoria (campo
    existe no schema mas não é escrito no momento da aplicação).

profile_suggestions.validation_status / actionability_status
    100% das 101 linhas = 'blocked_no_validation' / 'exploratory_only'.
    [query] ps_validation_actionability
    Inclui as 2 linhas 'applied' — ou seja, os 2 profiles reais existentes
    foram criados a partir de sugestões que o próprio sistema classifica
    como não-validadas e puramente exploratórias.

profile_intelligence_autopilot_candidates.source_suggestion_id
  → profile_suggestions.id
    Ligação confirmada em código (profile_intelligence_autopilot_service.py:1540).
    Estado real dos 91 candidatos: 61 DISABLED, 30 SHADOW_COLLECTING, ZERO em
    PENDING_HUMAN_APPROVAL/APPROVED/LIVE_ACTIVATED. [query] pi_autopilot_candidates_state
    RELAÇÃO FUNCIONALMENTE ESTÉRIL: nenhum candidato jamais chegou a virar
    mudança real em produção.

autopilot_audit_logs.profile_id
  → profiles.id
    Das 21 linhas de auditoria, as 6 únicas mutações reais ('MUTATED') são
    todas no mesmo profile_id (15b2181b-...), entre 2026-06-12 e 2026-06-15.
    [query] autopilot_audit_recent

config_profiles(config_type='orchestrator_weights')
    RELAÇÃO AUSENTE: nenhuma linha existe. [query] orchestrator_weights_config → []
```

---

## 6. Estado real do Shadow Portfolio

**Volume total:** 11.190 trades (`[query] feature_snapshot_key_coverage`; a contagem inicial de 11.099 cresceu para 11.190 entre o início e o fim desta sessão de auditoria, pois o pipeline continua rodando em produção).

**Por status** (`[query] by_status`, base 11.099):
| Status | Count |
|---|---|
| COMPLETED | 9.919 |
| CANCELLED | 670 |
| RUNNING | 347 |
| PENDING | 163 |

**Por outcome** (`[query] by_outcome`):
| Outcome | Count |
|---|---|
| SL_HIT | 5.848 |
| TP_HIT | 3.782 |
| NULL | 1.180 |
| TIMEOUT | 289 |

**Por fonte (source)** (`[query] by_source`):
| Source | Total | Completed | TP | SL | Timeout |
|---|---|---|---|---|---|
| L3 | 5.289 | 4.884 | 1.674 | 3.033 | 177 |
| L3_LAB | 2.557 | 1.828 | 631 | 1.144 | 53 |
| L1_SPECTRUM | 1.934 | 1.913 | 948 | 942 | 23 |
| L3_SIMULATED | 805 | 780 | 316 | 441 | 23 |
| L3_REJECTED | 514 | 514 | 213 | 288 | 13 |

**Janela temporal:** 2026-06-09 18:20 → 2026-06-24 15:05 (15 dias de operação). `[query] date_range`

**Performance geral** (base: 9.919 fechados, `[query] overall_winrate`):
- Win rate (TP / (TP+SL)) = **39,27%**
- Win rate (TP / todos fechados) = **38,13%**
- P&L total = **-15.612,59 USDT**
- P&L % médio = **-0,1574%** por trade

> Nota sobre a divergência com o número do usuário (34,3%): o valor calculado nesta auditoria com a fórmula mais simples (TP/(TP+SL+TIMEOUT) ou TP/total-fechado) dá 38,13%-39,27%, não 34,3%. **Não confirmado** qual filtro/recorte gera exatamente 34,3% no frontend (pode ser um corte por data, por profile, ou por fonte específica). Recomenda-se citar a query exata do frontend que produz 34,3% antes de tratá-lo como o número oficial.

**P&L por outcome** (`[query] pnl_by_outcome`, `[query] holding_by_outcome_detail`):
| Outcome | N | Avg PnL% | Soma PnL USDT | Holding médio |
|---|---|---|---|---|
| SL_HIT | 5.848 | -1,0174% | -59.497,25 | 904,9 min (~15,1h) |
| TP_HIT | 3.782 | +1,1585% | +43.813,00 | 290,2 min (~4,83h) |
| TIMEOUT | 289 | +0,0248% | +71,66 | 2.914,2 min (~48,6h) |

Os perdedores (SL) ficam em média **3,1x mais tempo presos** do que os vencedores demoram para vencer — e a perda média por trade (-1,02%) é praticamente igual em magnitude ao ganho médio (+1,16%), mas há mais perdedores (5.848) que vencedores (3.782), o que sozinho já explica boa parte do P&L negativo, independente de qualquer problema de modelo.

**P&L por profile** (`[query] by_profile_perf`, top achados):
- Trades **sem profile_id** (n=5.107 fechados): win rate **47,87%**, P&L total **-5,03 USDT** (praticamente zero) — o melhor resultado do sistema.
- **Todos os 38 profiles nomeados** auditados têm P&L total **negativo**, variando de -10,00 USDT (`rsi_gte_72_AND_adx_gte_35_AND_bb_0_050_0_080`, n=6) a -1.380,54 USDT (`L3_TREND_CONSERVADOR_V3`, n=644, win rate 37,58%).
- Profiles "clássicos" nomeados (`L3_*_V3`) e profiles "gerados por combinação de regra" (`rsi_xx_AND_macd_xx...`) têm desempenho igualmente ruim — não há um grupo nitidamente melhor.

**Validação da métrica "TP 4h" (`is_tp_4h_v1`)** (`[query] label_is_tp_4h_v1_distribution`, `[query] tp_hit_holding_vs_4h`):
| Label | N | Avg PnL% | Avg MAE% | Avg MFE% |
|---|---|---|---|---|
| 1 (TP_HIT ≤4h) | 2.934 | +1,1247% | -0,1788% | +1,7660% |
| 0 (SL/TIMEOUT/TP&gt;4h) | 6.985 | -0,6960% | -2,1099% | +0,6622% |

Dos 3.782 trades com `outcome='TP_HIT'`, **848 (22,4%) levaram mais de 4h** e são tratados como label=0, idênticos a uma perda, apesar de terem fechado com lucro real. O label correlaciona razoavelmente bem com PnL médio (label=1 é claramente melhor que label=0), mas **descarta informação real de 848 trades vencedores** ao tratá-los como negativos — isso não é "enganoso" no sentido de inverter o sinal, mas é **subótimo**: mistura "perdeu" com "ganhou devagar" na mesma classe.

**Bugs encontrados (consolidado — tabela completa na seção 11/Fase 10):**
- 38 `decision_id` duplicados em `shadow_trades` (44 linhas extras), 17 com outcomes conflitantes — sem constraint UNIQUE em produção apesar do comentário de código alegar idempotência via `ON CONFLICT(decision_id) DO NOTHING`.
- 144 trades `COMPLETED` sem `features_snapshot_exit` (70 em L3, 49 em L1_SPECTRUM, 17 em L3_LAB, 5 em L3_SIMULATED, 3 em L3_REJECTED), violando o invariante documentado no código de que esse campo nunca deveria ser NULL para um trade completo.
- Nenhuma inconsistência estrutural status/outcome/completed_at (0 casos).
- Nenhum caso de TP_HIT com preço de saída fora da faixa do TP, nem SL_HIT fora da faixa do SL (0 casos cada).
- Nenhum caso de TP_HIT com PnL negativo nem SL_HIT com PnL positivo (0 casos cada) — a integridade do campo `outcome` em si é sólida.
- 357 trades `RUNNING`, todos com menos de 24h de idade — sem trades "presos" antigos no momento da auditoria.

---

## 7. Estado real dos ML Models

47 modelos registrados em `ml_models` (`[query] ml_models_status_count`):

| Status | Lane | Count |
|---|---|---|
| candidate | L1_SPECTRUM | 10 |
| candidate | L3_PROFILE | 9 |
| candidate | NULL (legado) | 2 |
| active | L1_SPECTRUM | 1 |
| active | L3_PROFILE | 1 |
| retired | NULL (legado) | 9 |
| retired | L1_SPECTRUM | 2 |
| retired | L3_PROFILE | 1 |
| rejected | NULL | 12 |

### 7.1 Modelos atualmente ativos

**v46 — LightGBM / L1_SPECTRUM** (`activated_at`: 2026-06-24 01:42):
```json
"validation": {"roc_auc": 0.6123, "precision": 0.4367, "recall": 0.5948, "f1": 0.5036, "fpr": 0.4518},
"test":       {"roc_auc": 0.4546, "precision": 0.2171, "recall": 0.4125, "f1": 0.2845, "fpr": 0.5107}
```
`[query] v44_v46_metrics_json`. label=`is_tp_4h_v1`, target_window=14.400s, train=938/val=313/test=313, feature_count=48.

**v44 — CatBoost / L3_PROFILE** (`activated_at`: 2026-06-24 01:55):
```json
"validation": {"roc_auc": 0.6912, "precision": 0.2372, "recall": 0.7286, "f1": 0.3579, "fpr": 0.4568},
"test":       {"roc_auc": 0.4260, "precision": 0.1933, "recall": 0.5200, "f1": 0.2818, "fpr": 0.6576}
```
label=`is_tp_4h_v1`, target_window=14.400s, train=1.288/val=429/test=430, feature_count=50.

**Ambos os modelos ativos têm ROC AUC de teste abaixo de 0,5** — pior que um classificador aleatório no holdout que simula produção. A queda de validação (0,61-0,69) para teste (0,43-0,45) é o padrão clássico de overfitting: o modelo aprendeu padrões específicos do período de validação que não generalizam. Apesar disso, ambos estão com `status='active'` desde 2026-06-24 01:42-01:55 — **horas antes do início desta auditoria**.

### 7.2 Candidatos correlatos (v41/v42, mencionados pelo usuário)
| Versão | Lane | Label | Val AUC | Test (via notes) | Status |
|---|---|---|---|---|---|
| v41 | L1_SPECTRUM | is_tp_4h_v1 | 0,6108 | não em metrics_json desta consulta | candidate |
| v42 | L3_PROFILE | is_tp_4h_v1 | 0,7070 | não em metrics_json desta consulta | candidate |

v41/v42 permanecem como `candidate` (nunca promovidos) — consistente com a memória do projeto que os marca como "test aleatório/pior que aleatório, NÃO promover".

### 7.3 Modelos anteriores (v19/v20, is_win_fast_v1)
Ambos `status='retired'`, sem `test_samples` (NULL) e `f1_score=0.0` — confirma a anotação da memória de que estes operavam apenas como `ranking_shadow_only`, sem avaliação de teste completa.

### 7.4 Respostas às perguntas da Fase 6

1. **Qual modelo está efetivamente em produção?** v44 (CatBoost/L3_PROFILE) e v46 (LightGBM/L1_SPECTRUM), ambos `status='active'`.
2. **Onde ele pontua?** Via `WinFastPredictor` (`prediction_service.py`), chamado por `pipeline_scan.py`, `api/ml.py`, `api/watchlists.py`. Mas o **ML Gate que consome esse score está desligado por padrão** (`ML_GATE_ENABLED=false`).
3. **A pontuação gera alguma watchlist?** Não confirmado/AUSENTE — nenhuma watchlist do tipo `ML_*` existe (ver seção 10).
4. **A pontuação influencia L3?** Apenas se `ML_GATE_ENABLED=true` ou `pool_config.new_arch_l3_uses_ml_score=true` — e mesmo assim, só pode rebaixar ALLOW→BLOCK, nunca promover.
5. **O Shadow sabe qual modelo selecionou o trade?** **Não.** `ml_model_id` é 100% NULL em `shadow_trades`.
6. **Existe `orchestrator_payload` com `p_l1_win`/`p_l3_profile_win`/`score`/`reason_codes`?** Existe a coluna e a lógica no código, mas só está populada em 2,7% das linhas (backfill único, não recorrente).
7. **Existe ligação entre ML prediction e resultado do Shadow?** RELAÇÃO AUSENTE — `ml_probability` é 100% NULL; não há tabela `ml_predictions` populada confirmada nesta sessão para os trades de shadow (não verificado volume de `ml_predictions` nesta auditoria — **não confirmado**).
8. **O modelo ativo está apto para operar?** Não, pelos próprios números: AUC de teste < 0,5 em ambos. Tecnicamente isso não importa hoje porque o ML Gate está desligado, mas se for ligado nesse estado, vai gerar decisões piores que aleatórias.
9. **Qual modelo deve ser bloqueado/mantido/aposentado/reavaliado?** v44 e v46 deveriam ser reavaliados antes de qualquer plano de ligar o ML Gate — não deveriam permanecer `active` com esse perfil de teste, mesmo estando hoje sem efeito prático.

---

## 8. Estado real do Profile Intelligence

**Execuções (`profile_intelligence_runs`):** 32 totais, 23 `completed` + 9 `failed` (taxa de falha ~28%), janela 2026-06-17 → 2026-06-23. `[query] profile_intelligence_runs_status`

**Sugestões (`profile_suggestions`):** 101 totais, janela 2026-06-17 → 2026-06-20 (sem novas sugestões nos últimos 4 dias de runs, apesar do engine ter continuado executando até 23/06 — **não confirmado** o motivo exato, mas é consistente com o gate `validation_status` bloqueando 100% e o engine eventualmente não tendo novidade para sugerir, ou com mudança de comportamento não investigada).
- `status`: `exploratory_only`=99, `applied`=2. `[query] profile_suggestions_status`
- `validation_status`/`actionability_status`: **100% das 101 linhas** = `blocked_no_validation`/`exploratory_only`, **incluindo as 2 marcadas `applied`**. `[query] ps_validation_actionability`
- Os 2 profiles criados a partir de sugestões "bloqueadas/exploratórias" (`88bdb40c-...` e `f86f47ae-...`) — o segundo aparece em `by_profile_perf` com win rate 11,43% e P&L -245,88 USDT.
- Campo `applied_at`: NULL nas 2 linhas `applied`, mesmo com `created_profile_id` preenchido — gap de auditoria (o evento de aplicação não grava o timestamp dedicado).

### 8.1 Respostas às 20 perguntas (evidência de código, agente dedicado)

1. **Lê resultados reais do Shadow?** Sim — `profile_intelligence_service.py:79,293,997-1007`, `indicator_lift_service.py:140-159`.
2. **Diferencia TP/SL/timeout/PnL/MAE/MFE/tempo preso?** Sim — `profile_intelligence_service.py:66-78` usa `COUNT(*) FILTER (WHERE outcome=...)`, `AVG(pnl_pct)`, `AVG(holding_seconds)`, `AVG(mae_pct)`, `AVG(mfe_pct)`.
3. **Sabe indicadores de entrada?** Sim — via `features_snapshot`, lido em `indicator_lift_service.py:149,179-186`.
4. **Sabe indicadores de saída?** **Não confirmado** — `features_snapshot_exit` existe no modelo, mas nenhum serviço do PI Engine auditado foi visto lendo esse campo.
5. **Identifica indicadores vencedores/perdedores?** Sim — `IndicatorLiftAnalyzer.analyze` (`indicator_lift_service.py:342-352`) classifica `winning_indicator`/`losing_indicator`/`low_sample`/`neutral` por lift de win rate.
6. **Identifica ranges bons/ruins?** Parcialmente — usa buckets fixos pré-definidos (`_get_indicator_buckets`, linha 26-103), não descoberta dinâmica de range ótimo.
7. **Gera sugestões reais (escreve em `profile_suggestions`)?** Sim, confirmado por dado (101 linhas).
8. **Sugestões carregam evidência estatística (sample, win rate antes/depois, p-value)?** Parcial — `total_cases`, `discovery.win_rate`, `validation.win_rate` confirmados (`profile_suggestion_service.py:349-376`). **P-value: ausente** — grep no módulo não encontrou nenhuma ocorrência de `p_value`/`pvalue`.
9. **Sugestões aprovadas geram UPDATE/INSERT real?** `create_from_suggestion` faz **INSERT de um novo profile**, não UPDATE no existente (`profile_create_service.py:633-649`). Confirmado por dado: 2 INSERTs reais.
10. **Audit trail detalhado (antes/depois, quem aprovou)?** Sim — `ProfileIntelligenceAuditLog` com `before_json`/`after_json`/`diff_json`/`actor_user_id`.
11. **Versionamento de profile é populado e usado?** Sim, mas no Sistema A (`autopilot_engine.py:848-869`), fora do PI Engine "core".
12. **Existe rollback?** Sim — 3 variantes confirmadas em código (`rollback_to_version`, `rollback_last_adjustment`, `_rollback_candidate`).
13. **Existe aprovação humana obrigatória?** Sim, para o Sistema B — endpoint bloqueado se `state != "PENDING_HUMAN_APPROVAL"`. Mas **nenhum candidato chegou a esse estado nos dados atuais** (seção 9).
14. **Proteção contra overfitting?** Sim — `detect_overfit_risk()` com 5 heurísticas (degradação >30%, amostra <20, win rate discovery >70% com <50 casos, >6 regras, discovery_wr > validation_wr×1.3).
15. **Amostra mínima exigida — valor literal?** Sim: `PI_MIN_DISCOVERY_TRADES=30`, `PI_MIN_VALIDATION_TRADES=20`, `min_closed_trades=30` (também confirmado em `config_profiles.profile_intelligence.min_closed_trades=30`, `[query] profile_intelligence_config`).
16. **Holdout temporal?** Sim — split discovery/validation 70/30 (`profile_intelligence_service.py:608-666`).
17. **Comparação profile antigo vs. novo?** Sim, no sentido discovery-vs-validation da mesma sugestão. A/B literal entre dois profiles concorrentes rodando em paralelo: não confirmado nos arquivos do PI Engine core.
18. **Controle de duplicidade de profiles?** Apenas warning não-bloqueante (`profile_create_service.py:589-604`), sem constraint UNIQUE em `profiles.name`.
19. **Explicação textual clara / reason_codes?** Sim — campos `quantitative_explanation`, `ai_explanation`, `risk_notes`, `blocked_reason`, `reason`.
20. **Auto-Pilot está alterando algo de fato?** O job em si roda (não é código morto), mas o efeito líquido em produção é quase nulo — ver seção 9.

---

## 9. Estado real do Auto-Pilot

**Config de guardrails atual** (`config_profiles.autopilot_guardrails`, `[query] autopilot_guardrails_config`):
```json
{
  "kill_switch": false,
  "dry_run_mode": false,
  "min_span_days": 3,
  "fpr_max_threshold": 0.65,
  "autopilot_can_adjust": ["scoring_rules", "minimum_score", "block_rules", "entry_triggers"],
  "ev_min_threshold_pct": 0.0,
  "autopilot_full_authority": true,
  "circuit_breaker_threshold": 3,
  "fee_limited_guard_enabled": false,
  "circuit_breaker_pause_hours": 168
}
```

Isso é uma configuração **ativa para produção real** (`dry_run_mode=false`, `autopilot_full_authority=true`, `fee_limited_guard_enabled=false`) — não é o default seguro do código (que é `dry_run_mode=true` quando a linha de config não existe). Esta linha existe e está sobrescrevendo o default.

**Mas, na prática, isso não está produzindo mutações** porque:
```
profiles_active_status: 60 profiles inativos+shadow, 33 ativos+shadow_only,
16 ativos+não-shadow — TODOS os 109 com auto_pilot_enabled=false.
```
`[query] profiles_active_status`. O job (`tasks/autopilot.py::run()`) itera profiles com `auto_pilot_enabled=True` — e não encontra nenhum.

**Histórico real de ações** (`autopilot_audit_logs`, `[query] autopilot_audit_action_counts`):
| Ação | Count | Janela |
|---|---|---|
| DRY_RUN_ANALYZED | 7 | 2026-06-10 → 2026-06-14 |
| MUTATED | 6 | 2026-06-12 → 2026-06-15 |
| DRY_RUN_MUTATED | 2 | 2026-06-12 |
| AUTOPILOT_SCOPE_BLOCKED | 2 | 2026-06-22 |
| ANALYZED | 1 | 2026-06-13 |
| ERROR | 1 | 2026-06-12 |
| ROLLED_BACK | 1 | 2026-06-14 |
| AUDIT_TEST_* | 2 | 2026-06-11 |

As 6 mutações reais (`MUTATED`) **aconteceram todas no mesmo `profile_id` (15b2181b-...)**, entre 12 e 15/06. Desde então — **9 dias antes desta auditoria** — zero mutações reais. As 2 últimas entradas (22/06) são `AUTOPILOT_SCOPE_BLOCKED` com `reason_code='no_closed_trades_for_scope'`, num profile diferente (`67fb437f-...`).

### 9.1 Respostas às 15 perguntas

1. **Toggle "Auto-Pilot Ligado" — flag ou lógica real?** Apenas seta flag (`profiles.py:885-912`); não dispara ciclo imediato.
2. **Job/celery task?** Sistema A: `tasks/autopilot.py::run()`. Sistema B: `tasks/profile_intelligence_job.py::run()`/`monitor()`.
3. **Frequência?** Sistema A: 21.600s (6h). PI engine: 86.400s (24h). Monitor PI Autopilot: 300s (5min).
4-5. **Tabelas lidas/escritas:** ver lista extensa na seção 3.8 / relatório do agente — inclui `profiles`, `shadow_trades`, `config_profiles`, `profile_versions`, `autopilot_audit_logs` e ~10 tabelas `profile_intelligence_autopilot_*`.
6. **Altera profile via UPDATE direto ou candidate/version?** Sistema A: UPDATE direto, com compare-and-swap (`WHERE id=... AND user_id=... AND config=:expected_config`). Sistema B: nunca edita o incumbente direto, só via `activate_approved_candidate` chamado por API após aprovação.
7. **Quais sugestões gera?** Lê `profile_suggestions` com `status IN ('pending_user_approval','draft')` — porém **nenhuma das 101 sugestões atuais está nesses status** (são `exploratory_only`/`applied`), então o Sistema B não tem matéria-prima ativa a consumir hoje (**não confirmado** se isso é a causa direta dos 30 candidatos em `SHADOW_COLLECTING` parados, mas é consistente).
8. **Guardrails no código?** `MIN_RECORDS_REQUIRED=30`, `MIN_SPAN_DAYS=5` (código) vs. `min_span_days=3` (config ativa — a config de banco *relaxa* o default do código), `MIN_HOURS_BETWEEN_MUTATIONS=48`, `circuit_breaker_threshold=3`, `circuit_breaker_pause_hours=168`.
9. **Modo dry-run?** Existe a flag; **está desligada em produção** (`dry_run_mode=false` na config ativa).
10. **Modo "pending human approval"?** Sim, no Sistema B (`PENDING_HUMAN_APPROVAL`) — mas 0 candidatos estão nesse estado hoje.
11. **Rollback?** Sim, 3 variantes (seção 8, pergunta 12).
12. **Mutation audit?** Sim — `autopilot_audit_logs` e `profile_intelligence_autopilot_audit`, ambos confirmados por esquema e por dado.
13-14. **Escopo por user/profile — risco de alteração global?** UPDATE crítico tem WHERE duplo (`id` + `user_id`) + compare-and-swap pelo config esperado. Não encontrado UPDATE/DELETE sem escopo nos arquivos lidos. **Não confirmado** nenhum caso de alteração indevida.
15. **Por que ligado mas sem efeito no win rate?** Evidência direta: (a) `auto_pilot_enabled=false` em 100% dos profiles — não há "combustível" para o motor rodar; (b) mesmo quando rodou (12-15/06), só afetou 1 profile; (c) `fee_limited_guard_enabled=false` e `behavioral_cb_enabled`/`performance_rollback_enabled` desligados por default reduzem ainda mais a chance de uma mutação acontecer mesmo que o profile fosse habilitado.

**Atenção:** conforme instrução desta auditoria, não se recomenda desligar o Auto-Pilot — a recomendação (seção 22) é tornar sua ativação condicionada a critérios verificáveis dentro do Profile Intelligence, não removê-lo.

---

## 10. Estado real das watchlists

`pipeline_watchlists`: 105 linhas totais. `[query] pipeline_watchlists_count_by_level`
| Level | Count |
|---|---|
| L3 | 102 |
| L1 | 1 |
| L2 | 1 |
| POOL | 1 |

As 102 watchlists L3 são, em sua quase totalidade, geradas pelo Auto-Pilot como combinações granulares de uma única regra (nome padrão `"AP · <regra>"`, ex.: `"AP · rsi_24_30_AND_macd_hist_lte_0_AND_ema50_gt_ema200_false"`), cada uma vinculada a um `profile_id` distinto — exatamente os mesmos profiles que aparecem com P&L negativo na seção 6.

`pipeline_watchlist_assets`: 410 linhas — é uma tabela de **cache do universo atual** (upsert), não um histórico append-only; 408 dessas linhas foram atualizadas nas últimas 24h.

**Busca literal pelas 5 watchlists esperadas pela arquitetura desejada** (`ML_OPPORTUNITY_RANKED`, `ML_HIGH_SCORE`, `ML_PUMP_CANDIDATES`, `ML_DUMP_RISK`, `ML_L3_EXEC_READY`): **todas AUSENTES**, confirmado por grep em todo o repositório (backend e frontend) por dois agentes independentes.

### 10.1 Estrutura proposta para a futura watchlist ML (não implementar agora)

Campos mínimos sugeridos (conforme especificação do usuário), avaliados contra o schema atual:

| Campo proposto | Existe hoje em algum lugar? | Onde |
|---|---|---|
| `symbol` | Sim | `pipeline_watchlist_assets.symbol`, `shadow_trades.symbol` |
| `scored_at` / `expires_at` | Não | — nenhuma tabela de watchlist tem expiração explícita; `pipeline_watchlists` não tem `status` nem TTL, só `last_scanned_at` |
| `source_layer` | Parcial | `pipeline_watchlists.level`, `shadow_trades.watchlist_level` |
| `model_id` / `model_version` | Existe a coluna em `shadow_trades.ml_model_id`, mas 100% NULL | `ml_models.id`/`version` |
| `label_used` | Existe em `ml_models.label_version` | — |
| `ml_opportunity_score`, `p_pump_30m`, `p_clean_pump_60m`, `p_tp_before_sl_4h`, `p_dump_30m` | Não existem — esses são os labels candidatos da seção 13 | — |
| `expected_return_60m`, `expected_drawdown_60m` | Não existem como campo de watchlist; existem como dado histórico (`mfe_pct`/`mae_pct`) só em `shadow_trades`, pós-fato | — |
| `confidence` | Existe conceito (`profile_suggestions.confidence_score`), não para watchlist de oportunidade | — |
| `opportunity_type`, `best_profile_id`, `reason_codes`, `risk_codes`, `status` | Não existem | — |

**Conclusão:** as tabelas atuais **não suportam** essa estrutura sem migração — seria necessária uma nova tabela (proposta na seção 21).

---

## 11. Problemas P0 (impedem funcionamento correto ou causam decisão errada)

### P0-1 — Modelos ativos são anti-preditivos no holdout de teste
- **Evidência:** v46 test ROC AUC=0,4546; v44 test ROC AUC=0,4260 (`[query] v44_v46_metrics_json`).
- **Arquivo/tabela:** `ml_models.metrics_json`; treino em `ml_challenger_service.py`.
- **Impacto:** se o ML Gate for ligado nesse estado, as decisões piorariam, não melhorariam.
- **Causa raiz provável:** dataset pequeno (train=938-1.288) combinado com label `is_tp_4h_v1` que não filtra ruído de regime; ausência de gate de promoção que bloqueie `status='active'` quando `test_roc_auc < 0.5`.
- **Correção recomendada:** não promover v44/v46 para uso real até reavaliação; implementar gate de promoção obrigatório no Profile Intelligence (ver seção 22).
- **Risco da correção:** baixo (são candidatos shadow-only hoje); risco real está em deixá-los `active` indefinidamente sem revisão.
- **Teste necessário:** reexecutar treino com holdout temporal mais estrito e comparar AUC de teste antes de qualquer promoção futura.

### P0-2 — Não existe ligação viva entre score ML e Shadow
- **Evidência:** `ml_model_id` e `ml_probability` 100% NULL em 11.109 linhas (`[query] watchlist_link`).
- **Impacto:** impossível hoje correlacionar "o modelo achou isso bom" com "isso realmente performou bem" de forma sistemática e em escala — a única tentativa (`final_priority_score`) cobre 2,7% dos dados e nunca foi repetida.
- **Causa raiz provável:** `backfill_orchestrator_scores` é uma função de backfill manual, não um job agendado; não há nenhuma chamada automática encontrada em `pipeline_scan.py`.
- **Correção recomendada:** agendar o backfill (ou gravar o score no momento da criação do shadow, não retroativamente) — ver especificação seção 23.
- **Risco:** baixo, é uma operação de gravação aditiva.

### P0-3 — `decision_id` duplicado em `shadow_trades` sem constraint
- **Evidência:** 38 `decision_id` com 44 linhas extras; 17 com outcomes conflitantes (ex.: `decision_id=4225`, ZEC_USDT, 3×SL_HIT + 1×TP_HIT) (`[query] dup_decision_detail`, `[query] total_dup_decision_ids`). Confirmado: não existe `UNIQUE(decision_id)` em `pg_constraint`/`pg_indexes` (`[query] unique_constraints_shadow`, `[query] indexes_shadow_trades`).
- **Impacto:** contaminação pequena mas real das estatísticas de win rate/P&L (44/11.190 = 0,39% das linhas, mas distorce a leitura "este símbolo ganhou ou perdeu" quando há outcomes conflitantes para a mesma decisão).
- **Causa raiz provável:** a idempotência citada em comentário de código (`ON CONFLICT(decision_id) DO NOTHING`, migration 047) não está em vigor — possivelmente porque a constraint real só existe condicionada a `status IN ('RUNNING','PENDING')` (`uq_shadow_lab_active_profile_symbol`, `ux_shadow_running_user_source`), o que não impede recriação após o trade original já ter completado.
- **Correção recomendada:** investigar e, se aprovado pelo usuário em etapa futura, adicionar `UNIQUE(decision_id)` real ou lógica de dedupe explícita.
- **Risco da correção:** médio — requer entender por que múltiplas criações acontecem para o mesmo `decision_id` antes de simplesmente bloquear (pode haver re-tentativas legítimas de scan).

---

## 12. Problemas P1 (degradam performance ou geram métrica falsa)

### P1-1 — 100% das sugestões do Profile Intelligence ficam bloqueadas na validação
- **Evidência:** `validation_status='blocked_no_validation'` em 101/101 linhas (`[query] ps_validation_actionability`).
- **Impacto:** o módulo projetado para fechar o loop de feedback nunca produz uma sugestão "pronta para aplicar" pelo caminho normal.
- **Correção recomendada:** investigar por que 100% bloqueiam (amostra insuficiente? holdout temporal nunca satisfeito?) antes de desenhar qualquer "Label Lab"/calibração nova — ver seção 22.

### P1-2 — 2 profiles reais foram criados a partir de sugestões "exploratórias/bloqueadas"
- **Evidência:** `ps_applied_rows` mostra `status='applied'` com `validation_status='blocked_no_validation'` simultaneamente; ambos os profiles resultantes têm performance ruim (`f86f47ae-...` win rate 11,43%, PnL -245,88 USDT).
- **Causa raiz provável:** existe um caminho de API (`create_suggestion_from_combination`, citado pelo agente de código) que cria profile direto a partir de uma combinação, contornando o pipeline de validação/aprovação padrão.
- **Correção recomendada:** unificar os caminhos de criação de profile a partir de sugestão, ou bloquear explicitamente a criação quando `validation_status != 'validated'`.

### P1-3 — Config divergente entre `config_type='ml'` e `config_type='profile_intelligence'` para `enable_catboost`/`enable_lightgbm`
- **Evidência:** `[query] ml_config_type` → `enable_catboost: true, enable_lightgbm: true`; `[query] profile_intelligence_config` → `enable_catboost: false, enable_lightgbm: false` — **valores opostos** nas duas linhas de config.
- **Impacto:** se qualquer código novo (ou um caminho não auditado) ler a flag de `config_type='ml'` em vez de `'profile_intelligence'`, o retreino de CatBoost/LightGBM — atualmente desativado de propósito após o incidente v41/v42 — poderia ser reativado sem intenção.
- **Causa raiz provável:** mesma classe de bug já documentada na memória do projeto para `ml_win_fast_threshold_seconds` (duas linhas de config, uma autoritativa e uma esquecida).
- **Correção recomendada:** consolidar essas flags numa única linha de config ou garantir, por código, que apenas uma fonte seja lida e a outra seja removida/sincronizada.

### P1-4 — `orchestrator_weights` não existe em `config_profiles`
- **Evidência:** `[query] orchestrator_weights_config` → `[]`.
- **Impacto:** o Decision Orchestrator sempre usa os pesos hardcoded do código (0,60/0,40) — a "configurabilidade" documentada no código não é real hoje.
- **Correção recomendada:** criar a linha de config com os pesos atuais como baseline explícito, ou documentar que são intencionalmente fixos.

### P1-5 — 144 trades `COMPLETED` sem `features_snapshot_exit`
- **Evidência:** `[query] missing_features_snapshot` — 70 (L3) + 49 (L1_SPECTRUM) + 17 (L3_LAB) + 5 (L3_SIMULATED) + 3 (L3_REJECTED) = 144.
- **Impacto:** quebra o invariante documentado no código ("nunca NULL, ou snapshot real ou marcador `_capture_failed`") e reduz a base utilizável para qualquer análise de indicadores de saída.
- **Causa raiz provável:** falha estrutural na sessão isolada de captura pós-commit, antes mesmo do código alcançar o ponto de gravar o marcador de falha (gap já documentado em comentário de código, segundo o agente de auditoria).

### P1-6 — Script `update_ml_label_to_tp_4h.sql` aponta para `config_type` errado
- **Evidência:** o script grava em `config_type='ml'`, mas o job de treino lê de `config_type='profile_intelligence'` (confirmado por leitura de `profile_intelligence_job.py:148-160`). Hoje os dois valores estão em sincronia (ambos 14.400) só porque alguém corrigiu manualmente a linha certa — o script em si, se reexecutado isoladamente num ambiente novo, reintroduziria a divergência.
- **Correção recomendada:** corrigir o script para gravar (ou também gravar) em `config_type='profile_intelligence'`.

### P1-7 — Apenas 9,79% das decisões L3 geram shadow trade
- **Evidência:** 5.283/53.960 (`[query] decisions_to_shadow_link`).
- **Impacto:** qualquer análise feita a partir de `decisions_log` isoladamente cobre uma amostra muito diferente da que está em `shadow_trades` — risco de conclusões inconsistentes entre os dois conjuntos.
- **Causa raiz provável:** não confirmado nesta sessão (pode ser política de reentry/sampling legítima, não necessariamente um bug) — recomenda-se investigação dedicada antes de tratar como defeito.

---

## 13. Problemas P2/P3 (auditabilidade, UX, melhorias menores)

| # | Problema | Gravidade | Evidência |
|---|---|---|---|
| P2-1 | `applied_at` NULL nas 2 sugestões `status='applied'` | P2 | `[query] ps_applied_rows` |
| P2-2 | Tabela `indicator_snapshots` existe no schema mas tem 0 linhas (órfã) | P2 | `[query] indicator_snapshots_count` |
| P2-3 | Código morto: `predict_service.py::PredictService` sem nenhum chamador | P2 | confirmado por agente de código (grep sem resultados) |
| P2-4 | Nome legado confuso: `gcs_model_loader.py` não usa mais GCS, lê do Postgres | P3 | docstring do próprio arquivo, citado pelo agente |
| P2-5 | Seleção do modelo ativo não filtra por `model_lane` (`WHERE status='active' ORDER BY activated_at DESC LIMIT 1`) — ambíguo com 2 modelos ativos simultâneos | P1/P2 | `prediction_service.py`, situação real confirmada (v44+v46 ambos ativos) |
| P2-6 | Componentes de frontend órfãos: `AutoPilotToggle.tsx`, `PoolAutoPilotSection.tsx` — não importados em nenhuma página, chamam endpoints diferentes dos realmente usados | P3 | confirmado por grep do agente de código |
| P2-7 | Dois sistemas "Auto-Pilot" com nomes sobrepostos na UI, sem indicador visual de dry-run no Sistema B (existe no Sistema A) | P2 | `frontend/app/autopilot/page.tsx:289-301` vs. aba dentro de `/profile-intelligence` |
| P2-8 | `profile_intelligence_runs` com ~28% de taxa de falha (9/32) | P2 | `[query] profile_intelligence_runs_status` |
| P3-1 | `min_span_days` da config ativa (3) é menor que o `MIN_SPAN_DAYS` default do código (5) — a config de produção relaxa a proteção do código | P2 | `[query] autopilot_guardrails_config` vs. citação de código |
| P3-2 | Ausência de `p_value`/teste estatístico formal nas sugestões do PI (usa apenas heurísticas de lift/sample) | P3 | grep sem resultados nos arquivos do PI Engine |
| P3-3 | Ausência de controle de duplicidade real (apenas warning) para nomes de profile | P3 | `profile_create_service.py:589-604` |

---

## 14. Labels atuais e problemas

### `is_tp_4h_v1` — análise completa

**Onde é calculado:** `backend/app/ml/feature_extractor.py::build_training_dataframe` (linhas 320-449); o nome vem de `_LABEL_THRESHOLD_REGISTRY` (125-128): `{1800.0: "is_win_fast_v1", 14400.0: "is_tp_4h_v1"}`. Não é uma função separada — é o mesmo código de `is_win_fast`, parametrizado por `win_fast_threshold_s=14400.0`.

**Fórmula (Tier 1, caminho dominante — quando `shadow_trades.outcome` não é NULL):**
```python
holding_ok = holding_s is not None and holding_s <= win_fast_threshold_s
features["is_win_fast"] = 1 if (sim_outcome == "TP_HIT" and holding_ok) else 0
```

Respostas às 15 perguntas:

1. **Como é calculado?** `outcome == 'TP_HIT' AND holding_seconds <= 14400`.
2. **Arquivo/função?** `feature_extractor.py::build_training_dataframe`, linhas 385-416.
3. **Usa TP fixo?** Não diretamente — herda do `outcome` já decidido pelo monitor, que por sua vez pode ser `FIXED` ou `ATR_DYNAMIC` dependendo de `shadow_trades.barrier_mode`. O label não diferencia os dois modos.
4. **Usa janela de 240 minutos?** Sim, confirmado: `14400s = 240min = 4h` (`[query] v44_v46_metrics_json` confirma `target_window_seconds: 14400` nos dois modelos ativos).
5. **Considera se SL foi atingido antes?** Não precisa — isso já é garantido a montante pela regra `SL_FIRST` do monitor (se ambos forem tocados na mesma candle, SL vence).
6. **Considera MAE antes do TP?** **Não.** Os campos `mae_pct`/`mfe_pct` não entram na fórmula do label.
7. **Considera tempo até o TP, ou só binário?** Considera tempo apenas como gate binário (`holding_seconds <= threshold`), não como variável contínua.
8. **Considera custo operacional/fees?** Apenas no Tier 2 (fallback, quando `outcome IS NULL`), e mesmo assim as chamadas reais passam `label_net_of_fees=False` explicitamente — **em produção, fees não entram no label dominante**.
9. **Considera spread?** Não confirmado nenhuma referência a spread na fórmula do label.
10. **Considera trade "preso" (TIMEOUT)?** Sim, implicitamente: `TIMEOUT` cai no `else` e recebe label=0, idêntico a `SL_HIT` — sem distinção entre "perdeu" e "ficou parado sem decisão".
11. **Pode classificar como positivo trades operacionalmente ruins?** Não no sentido contrário (não há falso-positivo óbvio: todo label=1 teve `outcome='TP_HIT'` real, confirmado por `pnl_incoherent`=0). O problema é o inverso: classifica como **negativo** trades que na realidade ganharam dinheiro (TP_HIT > 4h, 848 casos, 22,4% dos TP_HIT).
12. **Mistura pump/bounce/reversão/breakout/tendência no mesmo alvo?** Sim — o label não diferencia o tipo de setup, apenas o resultado de preço dentro da janela. Isso é uma limitação de design, não um bug.
13. **Risco de leakage?** Não identificado no cálculo do label propriamente. Existe um guardrail explícito no código (`ML_EXCLUDED_FIELDS.intersection(df.columns)` com `assert`), mas o conteúdo exato de `ML_EXCLUDED_FIELDS` **não foi lido nesta sessão** — recomenda-se confirmação dedicada antes de declarar ausência total de leakage.
14. **Label calculado igualmente para L1 e L3?** Sim, confirmado — `_build_dataset` (Lane 1) e `_build_l3_dataset` (Lane 2) chamam a mesma `build_training_dataframe` com o mesmo `win_fast_threshold_s`.
15. **Usa dados que só existiam depois da entrada?** O label em si usa apenas `outcome`/`holding_seconds`, que só existem após o fechamento — isso é esperado e correto para um **label** (target), não uma **feature**. O que importaria seria se esses campos vazassem para o **vetor de features (X)** — e a evidência do agente de código indica que não vazam (campos de saída só alimentam o y, nunca o X via `FEATURE_COLUMNS`).

### Risco de configuração relacionado (não é bug do label em si)
O script `backend/sql/update_ml_label_to_tp_4h.sql` grava o threshold em `config_type='ml'`, mas o job real lê de `config_type='profile_intelligence'` — ver P1-6.

---

## 15. Labels candidatos recomendados (especificação, não implementar)

| Label | Fórmula | Tabelas/colunas | Dados suficientes hoje? | Riscos | Métrica de avaliação | L1 ou L3 | Tipo |
|---|---|---|---|---|---|---|---|
| `pump_30m_v1` | `max_price_post_entry` (ou `price_after_*`) dentro de 30min ≥ entry×(1+X%) | `shadow_trades.entry_price`, `price_after_1h` (granularidade mínima é 1h — **não há campo nativo de 30min**, precisaria de `max_profit_first_30m`, que já existe na tabela) | Parcial — `max_profit_first_30m` existe como coluna (confirmado no schema), cobertura não auditada nesta sessão | Janela curta = mais ruído/menos amostra positiva | Precision@threshold, AUC | L1 | Classificação |
| `pump_60m_v1` | igual, com `max_profit_first_60m` (coluna já existe) | `shadow_trades.max_profit_first_60m` | Coluna existe; taxa de preenchimento não auditada — não confirmado | Mesmo de cima, janela maior reduz ruído | AUC, precision | L1 | Classificação |
| `clean_pump_60m_v1` | `max_profit_first_60m ≥ X% AND mae_pct ≥ -Y%` (sobe limpo, sem grande adversidade antes) | `max_profit_first_60m`, `mae_pct` | Ambas colunas existem | Definir X/Y exige análise de distribuição real (não feita nesta auditoria) | AUC + análise de calibração | L1/L3 | Classificação |
| `tp_before_sl_4h_v1` | Equivalente ao atual `is_tp_4h_v1`, mas **explícito sobre a ordem** (hoje já é garantido pela regra SL_FIRST do monitor — esse label seria redundante com o atual, a menos que se queira reformular o monitor) | `outcome`, `barrier_touched`, `intrabar_convention` | Sim | Baixo — é quase o que já existe | igual ao atual | L1/L3 | Classificação |
| `fast_tp_30m_v1` | `outcome='TP_HIT' AND holding_seconds <= 1800` | `outcome`, `holding_seconds` | Sim — é exatamente o `is_win_fast_v1` já existente e já testado (v19/v20, v37-v39) | Já testado e descartado por baixa generalização em produção anterior conforme memória do projeto | AUC teste | L1/L3 | Classificação |
| `dump_risk_30m_v1` | `min_price_post_entry` dentro de 30min ≤ entry×(1-X%) | `min_price_post_entry`, `entry_price` | Coluna existe, granularidade de 30min específica não confirmada | Definir X exige estudo de distribuição | Recall (é um detector de risco, falso-negativo é o pior erro) | L1 | Classificação |
| `early_reversal_15m_v1` | preço cruza contra a posição nos primeiros 15min (precisaria de granularidade de candle, não só snapshot agregado) | Não existe coluna de 15min nativa — precisaria de nova coluna ou de acesso a OHLCV por trade | **Não, dados insuficientes hoje** | Granularidade fina aumenta custo de armazenamento | Recall | L1/L3 | Classificação |
| `stuck_trade_60m_v1` | `holding_seconds > 3600 AND mfe_pct < X% AND outcome NOT IN ('TP_HIT')` (capital parado sem MFE suficiente) | `holding_seconds`, `mfe_pct`, `outcome` | Sim, colunas existem | Definir X exige estudo | Precision/Recall | L1/L3 | Classificação |
| `future_mfe_60m_v1` | regressão direta sobre `max_profit_first_60m` | `max_profit_first_60m` | Coluna existe | Regressão é mais sensível a outliers que classificação | RMSE, Spearman correlation com PnL real | L1 | Regressão/Ranking |
| `opportunity_score_60m_v1` | combinação ponderada de MFE, MAE, tempo, spread, custo — ex.: `(mfe_pct - λ₁×|mae_pct| - λ₂×holding_norm - fee_pct)` | `mfe_pct`, `mae_pct`, `holding_seconds`, `fee_roundtrip_pct_applied` | Todas as colunas existem | Pesos λ precisam de calibração e validação out-of-sample antes de qualquer uso real | Correlação com PnL real, backtesting out-of-sample | L1 | Ranking/Regressão |

**Observação importante:** para `pump_30m_v1`/`early_reversal_15m_v1`/granularidades sub-30min, a auditoria **não confirmou** se a captura de preço pós-entrada tem granularidade suficiente (os campos nativos são `price_after_1h/2h/4h/12h/24h` mais `max_profit_first_15m/30m/60m` — esses últimos cobririam pump_30m/60m, mas não confirmado se estão de fato preenchidos com boa cobertura; recomenda-se checar null rate desses campos antes de adotar qualquer label baseado neles).

---

## 16. Métricas que devem substituir ou complementar TP 4h

Com base na seção 6 (validação da métrica) e seção 14:

1. **Separar "perdeu" de "ficou preso" (TIMEOUT) na análise de profile** — hoje `is_tp_4h_v1` trata os dois como idênticos (label=0), mas operacionalmente são eventos diferentes: TIMEOUT tem holding médio de 2.914 min (~48,6h) e PnL médio próximo de zero (+0,0248%), enquanto SL_HIT tem PnL médio de -1,0174%. Misturá-los no mesmo "0" do label esconde que TIMEOUT não é necessariamente ruim financeiramente, só ineficiente em capital parado.
2. **Reportar holding-time-weighted win rate**, não só contagem — um TP_HIT que demora 12.966 minutos (o máximo observado) não é equivalente a um que demora 0,1 minuto; a métrica atual (`is_tp_4h_v1`) já faz um corte binário em 4h, mas perde a distribuição completa.
3. **MAE pré-TP como métrica complementar obrigatória** — hoje calculado (`mae_pct`) mas não usado em nenhum label nem em nenhuma sugestão do PI conforme evidência de código; um TP que passou por -8% de MAE antes de fechar com +1% é um sinal de risco que o `is_tp_4h_v1` não captura.
4. **P&L líquido de fees como padrão**, não apenas como fallback Tier 2 — hoje as chamadas reais de treino passam `label_net_of_fees=False`.

---

## 17. Dados ausentes

Coberturas medidas em `shadow_trades.features_snapshot` (n=11.190, `[query] feature_snapshot_key_coverage`, `[query] feature_snapshot_null_value_rate`):

| Indicador/chave | Linhas com a chave presente | Cobertura | Observação |
|---|---|---|---|
| `rsi` | 10.818 | 96,7% | boa |
| `adx` | 10.818 | 96,7% | boa |
| `taker_ratio` | 10.613 | 94,8% | boa |
| `macd_histogram_pct` | 1.909 | **17,1%** | baixa — provável corte de schema (chave adicionada depois de um certo deploy) |
| `vwap_distance_pct` | 1.908 | **17,1%** | baixa, mesmo padrão |
| `bb_width` | 1.909 | **17,1%** | baixa, mesmo padrão |
| `orderbook_depth_usdt` | 1.906 | **17,0%** | baixa, mesmo padrão |
| `btc_dominance` | 5.365 (chave) / **1.105 valor não-nulo** | 47,9% (chave) / **9,9% (valor utilizável)** | a chave existe mas o valor é frequentemente NULL mesmo quando presente |
| `fear_greed_index` | 5.365 | 47,9% | quando a chave existe, o valor é quase sempre utilizável |
| `vix_value`, `dxy_value`, `us10y_yield`, `sp500_change_1h` | 5.365 cada | 47,9% cada | mesmo padrão de `fear_greed_index` |
| `macro_context_available` | 0 (não encontrado na raiz do JSON) | **não confirmado** — pode estar aninhado em outro nível, não foi testado caminho aninhado nesta sessão |

**Indicadores ausentes do schema atual** (citados pelo usuário, confirmados ausentes ou deliberadamente excluídos por código, segundo o agente de auditoria de indicadores):
- `Taker buy/sell volume` separados, `Buy pressure`, `Orderbook pressure` — existem em módulos de mercado (`order_flow_service.py`, `market_data_service.py`) mas **deliberadamente excluídos** de `FEATURE_COLUMNS` por serem duplicatas (`taker_ratio` já cobre taker buy/sell; `bid_ask_imbalance` já cobre orderbook pressure) — comentário explícito no código, não uma lacuna acidental.
- `OBV` — desabilitado em produção por cobertura de 1,1% (citado pelo código, não re-medido nesta sessão diretamente no banco — **não confirmado por query própria**, apenas por trecho de código citado pelo agente).
- `PSAR` — presente em módulos de scoring mas não como feature numérica direta em `FEATURE_COLUMNS` (entra só via encoding categórico, conforme agente).
- `Market data confidence`, `Bid ask imbalance` — presentes em outros módulos, **não confirmado** se entram em `FEATURE_COLUMNS`.

---

## 18. Bugs encontrados (consolidado)

Ver tabela completa nas seções 11-13 (P0/P1/P2/P3). Resumo dos mais relevantes:
1. `decision_id` duplicado em `shadow_trades` sem constraint (P0-3).
2. `ml_model_id`/`ml_probability` 100% NULL — ausência total de rastreabilidade ML→Shadow (P0-2).
3. Modelos ativos v44/v46 com AUC de teste < 0,5 (P0-1).
4. Config divergente `enable_catboost`/`enable_lightgbm` entre `config_type='ml'` vs. `'profile_intelligence'` (P1-3).
5. 100% das sugestões do PI bloqueadas na validação, mas 2 viraram profile real de qualquer forma (P1-1, P1-2).
6. 144 trades completos sem `features_snapshot_exit`, violando invariante documentado (P1-5).
7. `applied_at` nunca preenchido nas sugestões aplicadas (P2-1).
8. Tabela `indicator_snapshots` órfã (0 linhas) (P2-2).
9. Código morto: `PredictService` (P2-3).
10. Ambiguidade de seleção de modelo ativo sem filtro por `model_lane` (P2-5).

---

## 19. Riscos de leakage

- **Não identificado leakage confirmado no pipeline ativo** (treino via `ml_challenger_service.py`/`feature_extractor.py`): os campos de saída (`outcome`, `holding_seconds`, `pnl_pct`, `mae_pct`, `mfe_pct`) alimentam apenas o **label (y)**, nunca o vetor de features (X) — confirmado por inspeção de `FEATURE_COLUMNS` (48-50 colunas, todas derivadas do snapshot de entrada).
- Existe um guardrail de código (`assert not ML_EXCLUDED_FIELDS.intersection(df.columns)`), mas o **conteúdo exato de `ML_EXCLUDED_FIELDS` não foi lido nesta sessão** — recomenda-se ler esse conjunto explicitamente antes de declarar ausência de leakage com 100% de confiança.
- **Pipeline legado não auditado:** `dataset_builder.py`/`train_model.py` (baseados em `TradeSimulation`, não em `shadow_trades`) não foram lidos quanto a leakage nesta sessão — **não confirmado**, está fora do caminho de produção identificado (`ml_challenger_service.py`), mas existe no repositório.
- **Risco indireto via `barrier_mode`:** o label não diferencia se o `TP_HIT` veio de um trade com `barrier_mode='FIXED'` ou `'ATR_DYNAMIC'` — isso não é leakage no sentido técnico (não usa dado futuro como feature), mas é uma mistura de regimes de barreira diferentes sob o mesmo label, o que pode confundir o que o modelo está realmente aprendendo.

---

## 20. Riscos de overfitting

- **Confirmado por dado, não suposição:** v44 e v46 têm AUC de validação (0,69/0,61) muito superior ao AUC de teste (0,43/0,45) — a assinatura clássica de overfitting (`[query] v44_v46_metrics_json`).
- **Tamanho de amostra pequeno:** train_samples de 938-1.288 para os modelos ativos; a memória do projeto já documentava essa preocupação para v41/v42 com números semelhantes.
- **Mistura de fontes no treino do CatBoost (Lane 2):** segundo a memória do projeto, há um gate (`MIXED_SOURCE_BLOCKED_REASON`) que bloqueia treino misturando `L3`+`L3_LAB` sem o devido cuidado — **confirmado existir em código** (`dataset_policy.py` e `ml_challenger_service.py._check_mixed_source_gate`), mas duplicado em dois arquivos, o que é um risco de manutenção (se um for atualizado e o outro não, a proteção pode divergir).
- **`profile_id`/`source_encoded` como features dominantes:** o Lane 2 (CatBoost) adiciona `source_encoded`/`profile_id_encoded` como features categóricas extras — risco de o modelo aprender a "memorizar" performance histórica por profile/source em vez de aprender sinal de mercado genuíno. **Não confirmado** nesta sessão a importância relativa dessas features nos modelos v44 (não foi extraída feature importance).
- **Profile Intelligence tem proteção formal contra overfitting** (`detect_overfit_risk`, seção 8.1, pergunta 14) — mas essa proteção é para sugestões de **profile**, não para os modelos de **ML**. Não há evidência de um gate equivalente (AUC mínimo de teste, degradação val→test máxima) bloqueando a promoção de modelos ML para `status='active'`.

---

## 21. Tabelas/colunas necessárias para a nova arquitetura

Princípio adotado (conforme instrução do usuário): **preferir adaptar tabelas existentes**; propor tabela nova **apenas onde indispensável**.

### 21.1 Adaptações em tabelas existentes (preferencial)

- **`shadow_trades`**: já tem `ml_model_id`, `ml_probability`, `final_priority_score`, `orchestrator_payload`, `watchlist_id`, `source_watchlist_id` — **não precisa de nova coluna**, precisa que o pipeline efetivamente **escreva** nesses campos no momento da criação (não só via backfill manual). Mudança é de **processo/job**, não de schema.
- **`ml_models`**: já tem `metrics_json`, `label_version`, `target_window_seconds`, `model_lane`, `dataset_contract_id` — suficiente para um gate de promoção (ver seção 22); não precisa de nova coluna para isso.
- **`profile_suggestions`**: já tem `validation_status`, `actionability_status`, `evidence_summary_json`, `applied_at`, `rollback_payload` — o problema não é schema, é que `applied_at` não está sendo escrito no momento da aplicação (correção de código, não de schema).

### 21.2 Tabela nova proposta (única, indispensável)

Não existe hoje nenhuma tabela que represente "oportunidade ranqueada pelo ML, ainda não decidida pelo L3" — `pipeline_watchlist_assets` é cache de universo (sem score de ML nem expiração), e `shadow_trades` só existe **depois** de uma decisão. Para fechar o gap descrito na seção 10.1, seria necessária uma tabela nova, por exemplo `ml_opportunity_watchlist`:

```
ml_opportunity_watchlist
  id UUID PK
  symbol VARCHAR
  scored_at TIMESTAMPTZ
  expires_at TIMESTAMPTZ
  source_layer VARCHAR        -- 'L1' | 'POOL'
  model_id UUID FK -> ml_models.id
  model_version VARCHAR
  label_used VARCHAR
  ml_opportunity_score DOUBLE PRECISION
  p_pump_30m DOUBLE PRECISION NULL
  p_clean_pump_60m DOUBLE PRECISION NULL
  p_tp_before_sl_4h DOUBLE PRECISION NULL
  p_dump_30m DOUBLE PRECISION NULL
  expected_return_60m DOUBLE PRECISION NULL
  expected_drawdown_60m DOUBLE PRECISION NULL
  confidence VARCHAR
  opportunity_type VARCHAR
  best_profile_id UUID FK -> profiles.id NULL
  reason_codes JSONB
  risk_codes JSONB
  status VARCHAR              -- 'ACTIVE' | 'CONSUMED' | 'EXPIRED'
  created_at TIMESTAMPTZ
```

Esta seria a única tabela genuinamente nova proposta nesta auditoria — todo o resto é correção de processo ou preenchimento de colunas já existentes.

---

## 22. Plano de reformulação

**Restrição de design respeitada:** nenhum módulo externo novo chamado "Auto-Calibrator". Toda capacidade adaptativa nova é um recurso interno do Profile Intelligence.

### 22.1 Princípio geral
O Profile Intelligence já tem a maior parte da estrutura necessária (audit log, versionamento via Sistema B, aprovação humana, proteção contra overfitting de profile). O que falta não é um módulo novo grande — é **conectar** peças que já existem e **fechar gates** que hoje deixam passar tudo (decisões) ou bloqueiam tudo (sugestões).

### 22.2 Recursos internos a criar/adaptar dentro do Profile Intelligence

1. **Label Lab** (novo, interno ao PI): tela/serviço para registrar, treinar e comparar os labels candidatos da seção 15 lado a lado com `is_tp_4h_v1`, usando os mesmos dados de `shadow_trades`, sem qualquer efeito em produção até validação. Não precisa de tabela nova além de `ml_models.label_version`/`metrics_json`, que já suportam múltiplos labels.
2. **ML Opportunity Ranking** (novo, interno ao L1, alimentado pelo PI): job que escreve em `ml_opportunity_watchlist` (seção 21.2) a cada execução do scan L1, usando o modelo `active` da lane L1_SPECTRUM — mas **só depois** de um Promotion Gate (item 4) aprovar esse modelo.
3. **Profile Calibration** (adaptação do que já existe em `profile_suggestion_service.py`/`indicator_lift_service.py`): em vez de criar algo novo, **investigar e corrigir** por que `validation_status='blocked_no_validation'` em 100% dos casos antes de adicionar qualquer lógica nova de calibração.
4. **Promotion Gates** (novo, interno ao registro de `ml_models`): antes de qualquer modelo poder ir de `candidate` para `active`, exigir programaticamente: `test_roc_auc >= 0.55` (ou outro piso definido com o usuário), `|val_auc - test_auc| <= 0.15`, `test_samples >= N mínimo` (a definir). Hoje **não existe** esse gate — v44/v46 foram promovidos sem ele.
5. **Shadow Feedback** (adaptação): garantir que `backfill_orchestrator_scores` (ou equivalente) rode automaticamente a cada novo shadow trade fechado, não apenas manualmente uma vez.
6. **Mutation Audit** (já existe, manter): `autopilot_audit_logs` e `profile_intelligence_autopilot_audit` já cobrem isso adequadamente — nenhuma mudança estrutural necessária, apenas garantir que toda nova rota de mutação (incluindo a futura calibração) escreva nessas tabelas.
7. **Profile Versioning / Promotion Control** (já existe no Sistema B — `profile_intelligence_autopilot_candidates`, estados `PENDING_HUMAN_APPROVAL`→`APPROVED`→`LIVE_ACTIVATED`): em vez de criar algo novo, **usar o que já existe** — hoje 0 candidatos chegam a esse fluxo porque a etapa anterior (sugestões válidas) está com 100% de bloqueio.

### 22.3 Fluxo de dados final (mapeado às peças reais)

```
POOL (pool_service.py, sem mudança)
  ↓
L1 (pipeline_scan.py) + ML Opportunity Ranking (novo job, lê ml_models WHERE lane='L1_SPECTRUM' AND status='active' E aprovado pelo Promotion Gate)
  ↓
ml_opportunity_watchlist (tabela nova, seção 21.2)
  ↓
L3 Profile Matcher (profile_engine.py, já existe — passa a poder ler ml_opportunity_score como input adicional, não obrigatório)
  ↓
Shadow (shadow_trade_service.py / shadow_trade_monitor.py — passa a gravar ml_model_id/ml_probability no momento da criação, não via backfill)
  ↓
Profile Intelligence Feedback (profile_intelligence_service.py — sem mudança estrutural, mas com investigação prioritária do bloqueio de validação)
  ↓
Sugestão/Ajuste/Profile Candidate (profile_suggestion_service.py + profile_intelligence_autopilot_service.py — já existem)
  ↓
Shadow Validation (Sistema B, ciclo já existente REVIEW_SHADOW)
  ↓
Promotion Control (Sistema B, approve_candidate_for_live/activate_approved_candidate — já existe, só precisa de matéria-prima chegando)
```

Nenhuma etapa deste fluxo final exige um módulo "Auto-Calibrator" — toda a capacidade adaptativa cabe dentro de Profile Intelligence (PI Engine + Sistema B), reaproveitando 90%+ da infraestrutura já existente.

---

## 23. Especificação do futuro script único de reformulação

**Não implementar nesta etapa.** Especificação para uso futuro:

1. **Arquivos a alterar:**
   - `backend/app/services/decision_orchestrator.py` — adicionar chamada automática de `backfill_orchestrator_scores` (ou gravação direta no momento da criação do shadow) em vez de só backfill manual.
   - `backend/app/services/shadow_trade_service.py::_create_from_decision` — gravar `ml_model_id`/`ml_probability` no momento da criação, lendo do modelo ativo da lane correta.
   - `backend/app/ml/prediction_service.py::WinFastPredictor._get_threshold` — corrigir seleção de modelo ativo para filtrar por `model_lane` (resolve P2-5).
   - `backend/app/services/ml_challenger_service.py` — adicionar Promotion Gate (`test_roc_auc` mínimo, degradação val→test máxima) antes de permitir `status='active'`.
   - `backend/sql/update_ml_label_to_tp_4h.sql` — corrigir `config_type` (resolve P1-6).
   - `config_profiles` (via script, não migration de schema) — consolidar `enable_catboost`/`enable_lightgbm` numa única fonte (resolve P1-3); criar linha `orchestrator_weights` explícita (resolve P1-4).
   - `backend/app/services/profile_suggestion_service.py` — investigar e corrigir o gate que bloqueia 100% das sugestões em `validation_status='blocked_no_validation'`.
   - Novo: serviço de "Label Lab" dentro de `backend/app/services/profile_intelligence_*` (nome exato a definir com o usuário).
   - Novo: job "ML Opportunity Ranking" em `backend/app/tasks/`.

2. **Tabelas a migrar:** nenhuma migração estrutural em tabelas existentes é estritamente necessária — os campos relevantes já existem (seção 21.1).

3. **Novas colunas necessárias:** nenhuma identificada como indispensável além da tabela nova.

4. **Novas tabelas necessárias:** `ml_opportunity_watchlist` (seção 21.2) — única.

5. **Endpoints novos/alterados:** endpoint de leitura para `ml_opportunity_watchlist`; endpoint do "Label Lab" (comparação de labels); endpoint de gate de promoção de modelo (ver/aprovar manualmente, se o usuário quiser aprovação humana também para modelos, análogo ao que já existe para profiles).

6. **Jobs novos/alterados:** job "ML Opportunity Ranking" (novo, frequência a definir, sugestão: mesma cadência do scan L1); job de backfill de orchestrator score (alterar de manual para agendado, ou mover para o momento da criação do shadow).

7. **Serviços novos/alterados:** "Label Lab" (novo); `ml_challenger_service.py` (alterado, Promotion Gate); `decision_orchestrator.py` (alterado, automação).

8. **Contratos de dados:** definir schema de `orchestrator_payload` formalmente (hoje é JSONB livre); definir schema de `reason_codes`/`risk_codes` em `ml_opportunity_watchlist`.

9. **Payloads esperados:** a definir junto com o contrato de dados acima — fora do escopo desta auditoria read-only.

10. **Testes unitários:** Promotion Gate (casos: AUC abaixo do piso bloqueia; degradação val→test acima do limite bloqueia); seleção de modelo ativo por lane (caso 2 modelos ativos simultâneos, lanes diferentes, deve escolher o da lane correta).

11. **Testes de integração:** criação de shadow trade grava `ml_model_id`/`ml_probability` corretamente; suggestion pipeline não permite `status='applied'` quando `validation_status != 'validated'` (resolve P1-2).

12. **Testes de regressão:** confirmar que `decisions_log`→`shadow_trades` linkage não piora (deve permanecer ou melhorar os 9,79% atuais); confirmar que win rate/P&L histórico recalculado bate com os números desta auditoria antes/depois de qualquer dedupe de `decision_id`.

13. **Estratégia de rollback:** todas as mudanças de dados devem ser aditivas (novas colunas/tabelas, nunca DELETE); qualquer correção de `decision_id` duplicado deve primeiro arquivar as linhas conflitantes (não apagar) antes de decidir qual outcome é "oficial".

14. **Ordem segura de execução:** (1) corrigir gravação de `ml_model_id`/`ml_probability` no momento da criação → (2) investigar e corrigir gate de validação de sugestões → (3) Promotion Gate de modelos ML → (4) criar `ml_opportunity_watchlist` e o job de ranking → (5) Label Lab.

15. **Riscos:** alterar o gate de validação de sugestões pode, se mal calibrado, passar a aprovar sugestões ruins (risco simétrico ao atual, que bloqueia tudo); Promotion Gate pode deixar o sistema sem nenhum modelo `active` se o piso for muito alto (hoje nenhum modelo atingiria `test_roc_auc >= 0.55`, já que v44/v46 estão abaixo de 0,5).

16. **Critérios de aceite:** ver seção 24.

---

## 24. Critérios de aceite

1. `shadow_trades.ml_model_id`/`ml_probability` passam a ser preenchidos em ≥95% das novas linhas criadas após o deploy (hoje 0%).
2. Existe ao menos 1 modelo `active` com `test_roc_auc >= 0,55` antes de qualquer plano de ligar `ML_GATE_ENABLED` em produção real — **ou** decisão explícita do usuário de aceitar um piso diferente, documentada.
3. Taxa de sugestões com `validation_status != 'blocked_no_validation'` deixa de ser 0% (hoje 0/101).
4. Nenhuma criação de profile via sugestão ocorre com `validation_status='blocked_no_validation'` (hoje 2/2 dos profiles aplicados violam isso).
5. `decision_id` duplicado com outcomes conflitantes não cresce a partir da data da correção (hoje 17 casos históricos).
6. `config_profiles.enable_catboost`/`enable_lightgbm` têm uma única fonte de verdade, sem divergência entre `config_type`.

---

## 25. Queries SQL usadas na auditoria

Todas executadas em sessão `SET TRANSACTION READ ONLY` contra o Postgres de produção (`railway` db via proxy `zephyr.proxy.rlwy.net:23422`). Lista por tema (nomes correspondem às tags `[query]` citadas ao longo do relatório):

**Schema/metadados:** `SELECT version_num FROM alembic_version`; `SELECT table_name, ... FROM information_schema.tables`; `SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name=...`; `SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='shadow_trades'::regclass`; `SELECT indexname, indexdef FROM pg_indexes WHERE tablename='shadow_trades'`.

**Shadow Portfolio (Fase 3):** `total_trades`, `by_status`, `by_outcome`, `by_source`, `date_range`, `overall_winrate`, `pnl_by_outcome`, `holding_by_outcome_detail`, `tp_hit_holding_vs_4h`, `label_is_tp_4h_v1_distribution`, `by_profile_perf`, `profiles_count`, `profiles_active_status`, `missing_decision_id`, `missing_profile_id_by_source`, `missing_entry_price`, `missing_features_snapshot`, `status_outcome_mismatch`, `tp_price_not_reached`, `sl_price_not_reached`, `pnl_incoherent`, `pnl_incoherent_sl`, `stuck_running`, `running_age_buckets`, `duplicate_decision_id`, `dup_decision_detail`, `dup_decision_full_rows`, `total_dup_decision_ids`, `dup_outcome_conflict`, `watchlist_link`.

**ML Models (Fase 6):** `ml_models_all`, `ml_models_status_count`, `active_models_full`, `recent_candidates_v44_v46`, `latest_10_models`, `v44_v46_metrics_json`, `ml_probability_nonnull_sample`, `final_priority_nonnull_sample`.

**Watchlists/decisões/PI/Auto-Pilot (Fases 7-9):** `pipeline_watchlists_detail`, `pipeline_watchlists_count_by_level`, `pwa_total`, `profile_suggestions_status`, `profile_suggestions_applied`, `ps_applied_rows`, `ps_validation_actionability`, `profile_intelligence_runs_status`, `decisions_log_count`, `decisions_log_date_range`, `decisions_to_shadow_link`, `pi_autopilot_candidates_state`, `config_profiles_types`, `orchestrator_weights_config`, `autopilot_guardrails_config`, `profile_intelligence_config`, `ml_config_type`, `autopilot_audit_action_counts`, `autopilot_audit_recent`.

**Indicadores (Fase 4):** `feature_snapshot_key_coverage`, `feature_snapshot_null_value_rate`, `indicators_count`, `indicator_snapshots_count`, `alpha_scores_count`, `opportunity_snapshots_table`, `pool_coins_table`.

Todas as queries foram executadas via script auxiliar Python (`psycopg2`, `conn.set_session(readonly=True)`) criado especificamente para esta auditoria — nenhuma escrita foi feita no banco.

---

## 26. Arquivos inspecionados

Esta lista reflete os arquivos citados com caminho/linha pelos 3 sub-agentes de auditoria de código (leitura apenas) e por esta sessão principal:

**Pipeline/POOL/L1/L3/Decisões:** `backend/app/services/pool_service.py`, `backend/app/services/pool_selection.py`, `backend/app/tasks/pipeline_scan.py`, `backend/app/services/pipeline_rejections.py`, `backend/app/services/profile_engine.py`, `backend/app/services/block_engine.py`, `backend/app/services/decision_audit_service.py`.

**Shadow Portfolio:** `backend/app/services/shadow_trade_service.py`, `backend/app/tasks/shadow_trade_monitor.py`, `backend/app/services/exit_metrics.py`, `backend/app/models/shadow_trade.py`.

**ML:** `backend/app/ml/feature_extractor.py`, `backend/app/ml/macro_features.py`, `backend/app/ml/dataset_policy.py`, `backend/app/ml/prediction_service.py`, `backend/app/ml/predict_service.py` (código morto), `backend/app/ml/gcs_model_loader.py`, `backend/app/ml/trainer.py`, `backend/app/services/ml_challenger_service.py`, `backend/app/services/decision_orchestrator.py`, `ml_trainer/job.py` (papel não totalmente confirmado), `backend/sql/update_ml_label_to_tp_4h.sql`.

**Profile Intelligence:** `backend/app/api/profile_intelligence.py`, `backend/app/models/profile_intelligence.py`, `backend/app/services/profile_intelligence_service.py`, `backend/app/services/indicator_lift_service.py`, `backend/app/services/profile_suggestion_service.py`, `backend/app/services/profile_create_service.py`, `backend/app/services/profile_validation_service.py`, `backend/app/services/profile_intelligence_audit_service.py`, `backend/app/tasks/profile_intelligence_job.py`.

**Auto-Pilot:** `backend/app/api/autopilot.py`, `backend/app/services/autopilot_engine.py`, `backend/app/tasks/autopilot.py`, `backend/app/services/profile_intelligence_autopilot_service.py`, `backend/app/models/profile_intelligence_autopilot.py`, `backend/app/api/profiles.py`, `backend/app/tasks/celery_app.py`.

**Watchlists/Indicadores:** `backend/app/models/pipeline_watchlist.py`, `backend/app/models/custom_watchlist.py`, módulos de captura de indicadores citados pelo agente (`order_flow_service.py`, `market_data_service.py`, `indicator_classifier.py` — citados, não confirmado leitura linha-a-linha completa).

**Frontend:** `frontend/app/profile-intelligence/page.tsx`, `frontend/app/autopilot/page.tsx`, `frontend/components/profiles/AutoPilotToggle.tsx`, `frontend/components/pools/PoolAutoPilotSection.tsx`.

**Documentos de projeto consultados:** `docs/PROMPT_UNICO_AUDITORIA_L3_LIGHTGBM_CATBOOST_SHADOW_WATCHLISTS.md` (citado apenas para confirmar ausência de "Auto-Calibrator" em spec antiga), `specs/profile-intelligence-autopilot.md`, `specs/ml-retrain-compare.md`.

---

## 27. Pendências e perguntas abertas

1. **Divergência do win rate (34,3% citado pelo usuário vs. 38,13%/39,27% calculado nesta auditoria)** — não confirmado qual filtro/query do frontend produz exatamente 34,3%. Recomenda-se que o usuário aponte a tela/query exata para reconciliar.
2. **Conteúdo exato de `ML_EXCLUDED_FIELDS`** (`feature_extractor.py`) não foi lido nesta sessão — necessário para confirmar 100% a ausência de leakage.
3. **Papel exato de `ml_trainer/job.py`** em relação a `ml_challenger_service.py` — não confirmado se é um pipeline legado morto, um pipeline paralelo ativo, ou parte do mesmo fluxo por outro caminho.
4. **Causa raiz dos 90,2% de decisões L3 sem shadow trade correspondente** — não confirmado se é política de sampling/reentry legítima ou perda de dados.
5. **Caminho de leitura real da flag `enable_catboost`/`enable_lightgbm`** — não confirmado qual `config_type` (`'ml'` ou `'profile_intelligence'`) é efetivamente lido pelo código de treino; a memória do projeto e os achados de código indicam `'profile_intelligence'`, mas isso não foi reconfirmado linha-a-linha para esta flag específica (foi confirmado para `ml_win_fast_threshold_seconds`, não para `enable_catboost`/`enable_lightgbm`).
6. **Cobertura real de `OBV`, `PSAR`, `Market data confidence`, `Bid ask imbalance`** em `features_snapshot` — não medida por query direta nesta sessão (apenas por citação de código).
7. **Causa raiz de por que `profile_suggestions` não gerou nenhuma sugestão nova desde 2026-06-20**, apesar de `profile_intelligence_runs` ter continuado executando até 2026-06-23 — não investigado em profundidade.
8. **Motivo exato da queda de cobertura de `macd_histogram_pct`/`vwap_distance_pct`/`bb_width`/`orderbook_depth_usdt` para ~17%** (vs. ~97% de `rsi`/`adx`) — hipótese de corte de schema/deploy não confirmada por análise temporal direta (seria necessário cruzar `created_at` com a presença da chave, não feito nesta sessão).
9. **Caminho aninhado de `macro_context_available`** — não testado se a chave existe em sub-objeto do JSON em vez da raiz.
10. **Decisão do usuário sobre o piso de `test_roc_auc` do Promotion Gate proposto (seção 22.2, item 4)** — esta auditoria sugere 0,55 como ponto de partida, mas é uma decisão de produto/risco, não uma constatação técnica.

