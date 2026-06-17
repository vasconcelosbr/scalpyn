# AUDITORIA TÉCNICA — PROFILE INTELLIGENCE ENGINE
**Data:** 2026-06-17  
**Versão do sistema:** migration head = 079_lab_shadow_source  
**Escopo:** Levantamento técnico completo. Nenhum código foi alterado nesta fase.

---

## A. MAPA DAS TABELAS ATUAIS

### Tabelas de Negócio

| Tabela | Campos Principais | Propósito |
|--------|------------------|-----------|
| **profiles** | `id` UUID, `user_id`, `name`, `description`, `is_active`, `config` JSONB, `profile_role`, `pipeline_order`, `preset_ia_config`, `created_at`, `updated_at` | Strategy Profiles — config completa (filters + scoring + signals + block_rules + entry_triggers) em JSONB único |
| **config_profiles** | `id`, `user_id`, `pool_id`, `config_type`, `config_json` JSONB, `is_active`, `created_at`, `updated_at` | Configurações globais reutilizáveis. config_types ativos: `score`, `filters`, `block`, `spot_engine`, `autopilot_guardrails`, `ml_research` |
| **config_audit_log** | `id`, `config_id`, `changed_by`, `previous_json`, `new_json`, `change_description`, `changed_at` | Versionamento de mudanças em config_profiles |
| **shadow_trades** | `id`, `user_id`, `symbol`, `entry_price`, `exit_price`, `outcome` (TP_HIT/SL_HIT/TIMEOUT), `pnl_pct`, `mae_pct`, `mfe_pct`, `status`, `source`, `profile_id`, `profile_name`, `profile_version`, `strategy_type`, `rules_snapshot` JSONB, `features_snapshot` JSONB, `features_snapshot_exit` JSONB, `config_snapshot` JSONB, `final_priority_score`, `ml_probability`, `ttt_outcome`, `ttt_fast_win_bucket`, `holding_seconds` | Portfolio simulado L3 + Strategy Lab |
| **decisions_log** | `id` BigInt, `symbol`, `strategy`, `score`, `decision` (ALLOW/BLOCK), `l1_pass`, `l2_pass`, `l3_pass`, `direction`, `reasons` JSONB, `metrics` JSONB, `outcome` (tp/sl/timeout), `pnl_pct`, `user_id`, `created_at` | Auditoria L1→L3. **Sem `profile_id`** — lacuna crítica |
| **pipeline_watchlist_rejections** | `id`, `watchlist_id`, `user_id`, `profile_id`, `symbol`, `stage`, `failed_indicator`, `failed_type`, `current_value`, `expected_value`, `evaluation_trace` JSONB, `recorded_at` | Rejeições com trace detalhado de qual indicador falhou |
| **ml_models** | `id`, `version`, `status`, `roc_auc`, `decision_threshold`, `model_blob` BYTEA, `feature_columns_json`, `feature_columns_hash`, `profile_id`, `profile_version`, `model_scope` (global/profile), `train_from`, `train_to`, `hyperparams` JSONB, `created_at` | Modelos XGBoost. Suporte a scoped-profile (migration 078) |
| **ml_predictions** | `id`, `model_id`, `decision_id`, `symbol`, `prediction_probability`, `prediction_label`, `created_at` | Predições por decisão |
| **ai_provider_keys** | `id`, `user_id`, `provider` (anthropic/openai/gemini), `api_key_encrypted`, `key_hint`, `is_active`, `monthly_token_limit`, `tokens_used_month`, `last_used_at`, `created_at` | Chaves criptografadas por provider |
| **watchlist_profiles** | `id`, `user_id`, `watchlist_id`, `profile_type` (L2/L3), `profile_id`, `is_enabled` | Junction profiles ↔ watchlists |
| **trade_simulations** | `id`, `symbol`, `result` (WIN/LOSS/TIMEOUT), `decision_type`, `decision_id`, `features_snapshot`, `config_snapshot` | Dataset ML — mirror de shadow_trades com labels |
| **pipeline_watchlists** | `id`, `user_id`, `level` (POOL/L1/L2/L3), `watchlist_name`, `source_pool_id`, `filters_json`, `is_active` | Cascata de filtros por nível |
| **pipeline_watchlist_assets** | `id`, `watchlist_id`, `symbol`, `level`, `status`, `score`, `is_approved`, `reasons`, `last_confirmed_at` | Snapshot vivo do estado de cada ativo por nível |

### Sources ativas em `shadow_trades.source`

| Source | Descrição |
|--------|-----------|
| `L3` | Canonical — aprovados L3 (profile_id IS NULL) |
| `L3_REJECTED` | Ativos rejeitados na L3 — dados ML |
| `L3_SIMULATED` | Contrafactual — todos os ativos que chegam ao gate L3 |
| `L1_SPECTRUM` | Espectro completo pós-L1, antes de L2 — treino ML sem viés de seleção |
| `L3_LAB` | Strategy Lab (profile_id IS NOT NULL) — migration 079 |

---

## B. MAPA DOS ENDPOINTS ATUAIS

### Auth (`/api/auth`)

| Método | Path | Propósito |
|--------|------|-----------|
| POST | `/auth/register` | Cria usuário + seed defaults |
| POST | `/auth/login` | JWT access (60 min) + refresh (7 dias) |
| POST | `/auth/refresh` | Renova access token |

### Profiles (`/api/profiles`)

| Método | Path | Propósito |
|--------|------|-----------|
| GET | `/profiles/` | Lista profiles do usuário |
| GET | `/profiles/{id}` | Detalhe + hidratação com score global |
| POST | `/profiles/` | Cria profile (valida + normaliza config JSONB) |
| PUT | `/profiles/{id}` | Atualiza config + metadados |
| DELETE | `/profiles/{id}` | Remove + limpa watchlist_profiles |
| POST | `/profiles/{id}/test` | Testa profile contra market data live |
| POST | `/profiles/test-config` | Testa config sem salvar |
| POST | `/profiles/watchlist/{wl_id}/assign` | Vincula profile a watchlist |
| DELETE | `/profiles/watchlist/{wl_id}/profile` | Remove vínculo |
| PUT | `/profiles/watchlist/{wl_id}/toggle` | Ativa/desativa |
| POST | `/profiles/{id}/autopilot/toggle` | Liga/desliga auto-pilot |

### Config Global (`/api/config`)

| Método | Path | Propósito |
|--------|------|-----------|
| GET | `/config/{config_type}` | Lê config master (score/filters/block/...) |
| PUT | `/config/{config_type}` | Atualiza config + audit log + Redis invalidate |
| GET | `/config/flags` | Feature flags (público, sem auth) |

### Shadow Trades (`/api/shadow-trades`)

| Método | Path | Propósito |
|--------|------|-----------|
| GET | `/shadow-trades/` | Listagem paginada (filtros: status, symbol, date, source, profile_id) |
| GET | `/shadow-trades/summary` | Agregados (win_rate, pnl, count) — suporta profile_id |
| GET | `/shadow-trades/{id}` | Detalhe + snapshots entry/exit |
| GET | `/shadow-trades/prices` | Batch lookup preços atuais |

### Decisões (`/api/decisions`)

| Método | Path | Propósito |
|--------|------|-----------|
| GET | `/decisions/` | Listagem paginada (symbol, decision, date, direction) |
| GET | `/decisions/summary` | Agregados (aprovados, bloqueados, latência) |
| GET | `/decisions/{id}` | Detalhe + reasons + metrics JSONB |

### ML (`/api/ml`)

| Método | Path | Propósito |
|--------|------|-----------|
| POST | `/ml/train` | Treina XGBoost (background task; mín 100 records) |
| POST | `/ml/evaluate` | Avalia modelo (AUC, precision, recall, feature importance) |
| POST | `/ml/predict` | Predição single |
| POST | `/ml/batch-predict` | Batch prediction |
| GET | `/ml/models` | Lista modelos (global vs profile-scoped — migration 078) |

### AI Keys (`/api/ai-keys`)

| Método | Path | Propósito |
|--------|------|-----------|
| GET | `/ai-keys/` | Lista providers configurados (hint, status, token usage) |
| POST | `/ai-keys/{provider}` | Salva chave criptografada (sk-ant-* validado para Anthropic) |
| DELETE | `/ai-keys/{provider}` | Remove chave |

---

## C. MAPA DOS COMPONENTES FRONTEND ATUAIS

| Path | Propósito | Reaproveitável |
|------|-----------|---------------|
| `app/profiles/page.tsx` | CRUD profiles — ProfileBuilder + ProfileCard | ✅ Base para "criar profile gerado por IA" |
| `app/settings/score/page.tsx` | Scoring Rules master — thresholds, weights | ✅ Adicionar impact badges inline |
| `app/settings/block/page.tsx` | Block Rules + Entry Triggers global | ✅ Reutilizar lógica de condições |
| `app/settings/general/page.tsx` | AI Keys (Anthropic/OpenAI/Gemini) | ✅ Chave Anthropic já configurável |
| `app/dashboard/shadow-portfolio/page.tsx` | Shadow trades list + filtros + summary | ✅ Filtro profile_id já existe |
| `app/decisions/page.tsx` | Decisions log com filtros | ✅ Adicionar coluna profile |
| `app/ml-models/page.tsx` | ML models list + comparação | ✅ Integrar com profile-scoped models |
| `components/profiles/ProfileCard.tsx` | Card com actions (edit/delete/test) | ✅ Adicionar botão "Intelligence" |
| `components/ScoreRuleEditor.tsx` | Editor de regras scoring | ✅ Base para editor gerado por IA |
| `components/FilterConditionBuilder.tsx` | Builder de condições dinâmicas | ✅ Reutilizar em signal/block editors |
| `lib/apiGet, apiPost, apiPut, apiDelete` | HTTP helpers com Bearer auth | ✅ Usar nos novos endpoints |

---

## D. FLUXO DE CRIAÇÃO/EDIÇÃO DE STRATEGY PROFILES

```
User → /profiles → "Create Profile"
  ↓
ProfileBuilder (frontend)
  define: name, is_active, config JSONB:
    {
      filters:     {logic: "AND", conditions: [...]},
      scoring:     {selected_rule_ids: [...], weights: {liquidity:25, momentum:25, ...}},
      signals:     {logic: "AND", conditions: [...]},   ← alias: entry_triggers
      block_rules: [{field, operator, value, ...}]
    }
  ↓
POST /api/profiles/ com payload
  ↓
Backend: _validate_profile_config()
  - normaliza logic (AND/OR)
  - valida scoring weights (default 25% cada)
  - preserva block_rules + entry_triggers
  ↓
INSERT profiles (id, user_id, name, config JSONB, is_active)
  ↓
(Opcional) POST /profiles/watchlist/{wl_id}/assign
  → INSERT watchlist_profiles (junction)
```

**Ponto crítico**: Signals, scoring e block_rules vivem juntos dentro de `profiles.config` JSONB. Não há tabelas separadas por tipo de regra.

---

## E. FLUXO DE CRIAÇÃO/EDIÇÃO DE SIGNALS, SCORING E BLOCK RULES

> **Não existem endpoints separados para Signals/Scoring/Block Rules.**  
> Tudo é editado dentro do `profiles.config` JSONB ou `config_profiles.config_json` JSONB.

### Scoring Rules Master
```
/settings/score → ScoreEngineSettings component
  ↓ GET /api/config/score → config_profiles WHERE config_type='score'
  ↓ Edita rules (thresholds, weights, auto_select_top_n)
  ↓ PUT /api/config/score → UPDATE config_profiles + INSERT config_audit_log
```

### Block Rules Global
```
/settings/block → BlockEditor component
  ↓ GET /api/config/block → config_profiles WHERE config_type='block'
  ↓ Edita blocks + entry_triggers
  ↓ PUT /api/config/block → UPDATE config_profiles + audit_log
```

### Signals (entry_triggers) do Profile
```
/profiles → ProfileBuilder → aba "Signals"
  ↓ Edita entry_triggers: {logic: "AND", conditions: [{field, op, value}]}
  ↓ PUT /api/profiles/{id} com config atualizado
  ↓ UPDATE profiles SET config = ...
```

### Scoring vinculado ao Profile
```
/profiles → ProfileBuilder → aba "Scoring"
  ↓ Seleciona selected_rule_ids (das scoring rules master)
  ↓ PUT /api/profiles/{id}
  ↓ Backend _hydrate_profile_config_with_global_score()
     merges profile.config.scoring com config_profiles (score) na resposta GET
```

---

## F. DADOS DISPONÍVEIS PARA INTELIGÊNCIA

### Indicadores em `features_snapshot` JSONB (entrada — 37 features FEATURE_COLUMNS)

```
taker_ratio           volume_delta          rsi
macd_histogram_pct    adx                   adx_acceleration
spread_pct            volume_spike          bb_width
atr_pct               ema9_gt_ema21         ema50_gt_ema200
volume_24h_usdt       orderbook_depth_usdt  vwap_distance_pct
flow_strength         trend_alignment       momentum_strength
rsi_slope_3           rsi_slope_5           macd_hist_slope_3
buy_pressure          orderbook_pressure    stochastic_k
stochastic_d          psar_trend            di_trend
zscore                ema9_distance_pct
```

### Indicadores em `features_snapshot_exit` JSONB (saída)
Mesmo formato flat do entry — snapshot no momento do close.

### Métricas de Saída (colunas normalizadas em shadow_trades)

```
outcome               pnl_pct               mae_pct
mfe_pct               holding_seconds       ttt_outcome
ttt_fast_win_bucket   time_to_tp_minutes    profit_velocity
price_after_1h        price_after_2h        price_after_4h
price_after_8h        price_after_24h
delayed_tp (bool — teria atingido TP em 24h pós-timeout?)
```

### `rules_snapshot` JSONB (migration 077)
Regras ativas no momento da entrada — permite análise "quais rules levaram à aprovação".

### `decisions_log.metrics` JSONB
Valores de todos os indicadores no momento da decisão L3 — antes do shadow ser criado.

### `pipeline_watchlist_rejections.evaluation_trace` JSONB
Stack detalhado de condições testadas com current_value vs expected_value.

---

## G. LACUNAS ENCONTRADAS

| # | Lacuna | Impacto |
|---|--------|---------|
| **G1** | `decisions_log` **não tem `profile_id`** — impossível linkar decisão ao profile L3 que aprovou | CRÍTICO — sem isso não há atribuição de win/loss por profile |
| **G2** | **Tabela `opportunity_snapshots` não existe** — ativos que chegam ao L3 mas não são aprovados por nenhum profile não têm snapshot completo de features | CRÍTICO — sem isso não é possível identificar combinações não testadas |
| **G3** | **Sem CRUD de Scoring Rules individuais** — rules vivem em JSONB monolítico sem ID estável | Dificulta atribuição de impacto por rule |
| **G4** | **Sem coluna `profile_type` em `profiles`** — Strategy Lab só é identificável pelo nome (L3_*_V3) | Frágil para queries e filtros programáticos |
| **G5** | **Sem audit log de mudanças em `profiles.config`** — impossível análise temporal de performance por versão de configuração | Impossibilita comparar "profile v1 vs v2" |
| **G6** | **Sem tabela `profile_metrics`** — win_rate por profile exige JOIN pesado a cada requisição | Performance degradada em queries analíticas |
| **G7** | **Sem unique constraint em `config_profiles`** — duplicação possível por (user_id, config_type) | Risco de inconsistência silenciosa |
| **G8** | **Sem endpoint de contexto para Anthropic** — para gerar sugestões textuais, precisará de endpoint que retorne estatísticas estruturadas | Bloqueia camada explicativa |

### Detalhe sobre G2 — `opportunity_snapshots` (lacuna mais crítica)

`pipeline_watchlist_rejections` registra qual indicador falhou, mas sem snapshot completo de features. Para o Profile Intelligence Engine identificar "combinações possíveis não testadas", é necessária uma tabela capturando todos os ativos avaliados com seus 37 indicadores, independente de aprovação.

**Proposta de schema:**
```sql
CREATE TABLE opportunity_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    symbol          VARCHAR NOT NULL,
    watchlist_id    UUID,
    execution_id    VARCHAR,           -- ciclo de scan
    features_json   JSONB NOT NULL,    -- 37 indicadores flat
    profiles_evaluated  UUID[],        -- profile_ids avaliados
    profiles_approved   UUID[],        -- profile_ids que aprovaram
    profiles_rejected   UUID[],        -- profile_ids que rejeitaram
    rejection_reasons   JSONB,         -- {profile_id: {indicator, value, threshold}}
    source          VARCHAR DEFAULT 'L3_GATE',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON opportunity_snapshots (user_id, created_at DESC);
CREATE INDEX ON opportunity_snapshots (symbol, created_at DESC);
```

---

## H. RECOMENDAÇÃO DE ARQUITETURA

### Fase 1 — Data Foundation (migrations 080–084)
```
├── profiles.profile_type ('STANDARD' / 'LAB' / 'AUTOPILOT')
├── decisions_log.profile_id UUID FK (nullable, back-compat)
├── opportunity_snapshots — novo (ver G2)
├── config_profiles UNIQUE(user_id, config_type) WHERE pool_id IS NULL
└── profile_audit_log — log imutável de mudanças em profiles.config
```

### Fase 2 — Intelligence Engine (migrations 085–086 + novos services)
```
├── profile_metrics (win_rate, pnl, sharpe — refreshed daily por Celery beat)
├── rule_contribution (impacto de cada rule nos outcomes)
├── profile_intelligence_service.py (calcula métricas + sugestões estatísticas)
└── Anthropic APENAS como camada explicativa:
      input  → estatísticas calculadas pelo sistema (nunca inventadas)
      output → texto em português explicando o que os números dizem
```

### Fase 3 — API + Frontend
```
├── GET  /api/profile-intelligence/{id}     → métricas + sugestões
├── POST /api/profile-intelligence/generate → cria profile via análise
├── GET  /api/config/score/rules            → lista rules individuais
├── POST /api/config/score/rules            → cria rule com ID estável
├── PUT  /api/config/score/rules/{rule_id}  → edita rule
├── GET  /api/config/score/rules/{rule_id}/impact → impacto histórico
└── frontend/app/profile-intelligence/[id]/page.tsx → dashboard
```

---

## I. ARQUIVOS A ALTERAR NA IMPLEMENTAÇÃO

### Migrations (criar — próxima = 080)

```
backend/alembic/versions/080_opportunity_snapshots.py
backend/alembic/versions/081_profile_type_versioning.py
backend/alembic/versions/082_decision_profile_link.py
backend/alembic/versions/083_profile_metrics_tables.py
backend/alembic/versions/084_config_profiles_unique.py
backend/alembic/versions/085_profile_audit_log.py
```

### Backend — Criar

```
backend/app/models/opportunity_snapshot.py
backend/app/models/profile_metrics.py
backend/app/models/rule_contribution.py
backend/app/models/profile_audit_log.py
backend/app/services/profile_intelligence_service.py
backend/app/services/rule_attribution_service.py
backend/app/api/profile_intelligence.py
backend/app/tasks/profile_intelligence_job.py   ← Celery beat daily
```

### Backend — Alterar

```
backend/app/tasks/pipeline_scan.py     ← gravar profile_id em decisions_log
                                          + popular opportunity_snapshots por ciclo
backend/app/api/profiles.py            ← suportar profile_type no create/update
backend/app/models/profile.py          ← adicionar profile_type, profile_version
backend/app/api/config.py              ← CRUD de scoring rules individuais
```

### Frontend — Criar

```
frontend/app/profile-intelligence/[id]/page.tsx
frontend/components/profile-intelligence/MetricsChart.tsx
frontend/components/profile-intelligence/RuleContributionTable.tsx
frontend/components/profile-intelligence/OptimizationSuggestions.tsx
frontend/components/profile-intelligence/GenerateProfileModal.tsx
frontend/hooks/useProfileIntelligence.ts
```

### Frontend — Alterar

```
frontend/app/profiles/page.tsx                      ← botão "Intelligence" no ProfileCard
frontend/app/settings/score/page.tsx                ← impact badges + CRUD individual de rules
frontend/app/dashboard/shadow-portfolio/page.tsx    ← filtro profile_id já existe (ok)
```

---

## J. MIGRATIONS NECESSÁRIAS

| Migration | Descrição |
|-----------|-----------|
| **080** | `opportunity_snapshots` — features de TODOS os ativos avaliados por ciclo, independente de aprovação. Popula em `pipeline_scan.py` |
| **081** | `profiles.profile_type` VARCHAR(20) DEFAULT 'STANDARD' + `profiles.profile_version` TIMESTAMPTZ |
| **082** | `decisions_log.profile_id` UUID FK → profiles (nullable) + índice |
| **083** | `profile_metrics` + `rule_contribution` tables — cache daily de performance |
| **084** | UNIQUE INDEX em `config_profiles(user_id, config_type)` WHERE `pool_id IS NULL AND is_active` |
| **085** | `profile_audit_log` — log imutável de mudanças em `profiles.config` com previous/new JSON |

---

## K. RISCOS E CUIDADOS

| # | Risco | Severidade | Mitigação |
|---|-------|-----------|-----------|
| **K1** | **Data leakage ML** — `decisions_log.metrics` pode conter `score` → feature circular | CRÍTICO | `ML_EXCLUDED_FIELDS` já existe; adicionar assertion em `extract_features()` + test case |
| **K2** | **Multi-tenancy** — novo endpoint sem `user_id` filter expõe dados de outros usuários | CRÍTICO | Checklist de code review; teste obrigatório multi-tenant em cada endpoint novo |
| **K3** | **Profile version mismatch** — shadow roda com v1, profile atualiza para v2 mid-trade | ALTO | `rules_snapshot` já mitiga (migration 077); não regredir |
| **K4** | **Anthropic cost overrun** — Intelligence Engine pode chamar API em loop | ALTO | `monthly_token_limit` já em schema; cache de sugestões + hard cutoff no service |
| **K5** | **Race condition config_profiles** — dois PUTs paralelos em `/config/score` | MÉDIO | SELECT FOR UPDATE ou versão otimista (timestamp check) |
| **K6** | **Crescimento de `opportunity_snapshots`** — ~90 ativos × 144 ciclos/dia = 12.960 rows/dia | MÉDIO | Partition por mês + índice em created_at; retention policy 90 dias |
| **K7** | **Rule attribution ambiguidade** — trade aprovado por múltiplas regras | MÉDIO | Abordagem pragmática: regra discriminante (Shapley simplificado) |
| **K8** | **Backward compat config JSONB** — novos campos adicionados quebram código antigo | BAIXO | Sempre `.get(key, default)` no backend; migration backfill quando necessário |
| **K9** | **Anthropic como fonte de métricas** — risco de IA inventar números | CRÍTICO (design) | Regra absoluta: Anthropic recebe apenas dados calculados pelo sistema, retorna apenas texto |

---

## Apêndice — Status Atual

| Item | Status |
|------|--------|
| Profiles com config JSONB completo (signals + scoring + block_rules) | ✅ |
| Shadow trades com atribuição de profile (profile_id, rules_snapshot) | ✅ |
| ML models com suporte a profile-scoped (migration 078) | ✅ |
| Chave Anthropic criptografada em `ai_provider_keys` | ✅ |
| config_audit_log para mudanças de config global | ✅ |
| Strategy Lab ativo com source=L3_LAB (migration 079) | ✅ |
| `decisions_log` com `profile_id` | ❌ |
| `opportunity_snapshots` | ❌ |
| Profile Intelligence Engine | ❌ |
| Scoring Rules com CRUD individual | ❌ |
| Audit log de mudanças em `profiles.config` | ❌ |
| `profile_metrics` (cache de performance) | ❌ |
