# Baseline — Profile Intelligence Adaptive Loop (antes da implementação)

**Data:** 2026-06-24
**Branch:** `fix/profile-intelligence-adaptive-loop` (criada a partir de `main`)
**Origem:** segue a auditoria `docs/AUDITORIA_COMPLETA_POOL_L1_L3_SHADOW_ML_PI_AUTOPILOT_2026-06-24.md`.

Este documento registra o estado real do sistema **imediatamente antes** de qualquer alteração funcional desta implementação, para permitir comparação antes/depois e rollback informado.

## Alembic

- Head atual: `104_ml_metrics_json` — confirmado tanto em `SELECT version_num FROM alembic_version` (banco de produção) quanto como ponta da cadeia de migrations em `backend/alembic/versions/` (nenhum arquivo o referencia como `down_revision`, ou seja, é o topo real).
- **Nenhuma migration pendente.** Schema consistente. Seguro para prosseguir.

## ml_models (47 modelos registrados)

| status | model_lane | count |
|---|---|---|
| rejected | NULL (legado) | 12 |
| candidate | L1_SPECTRUM | 10 |
| retired | NULL (legado) | 9 |
| candidate | L3_PROFILE | 9 |
| retired | L1_SPECTRUM | 2 |
| candidate | NULL | 2 |
| retired | L3_PROFILE | 1 |
| **active** | **L3_PROFILE** | **1** (v44, CatBoost) |
| **active** | **L1_SPECTRUM** | **1** (v46, LightGBM) |

Os dois modelos `active` têm `test_roc_auc` abaixo de 0,5 (anti-preditivo), conforme `metrics_json` capturado na auditoria — esta é a causa raiz do P0-1 que esta implementação corrige via Promotion Gate.

## shadow_trades

- Total no momento da auditoria (24/06 14h): 11.190
- Total no momento deste baseline (24/06, antes da implementação): **12.519** (pipeline de produção continuou rodando entre as duas medições — crescimento normal, não é anomalia).

## profiles

- Total: 109
- Com `auto_pilot_enabled=true`: **0**
- Com `live_trading_enabled=true`: **0**

Confirma o estado já documentado na auditoria: nenhum profile está habilitado para Auto-Pilot automático nem para trading real. Esta implementação não altera esse estado (regra absoluta #1/#2: não ativar live trading).

## profile_suggestions

| status | count |
|---|---|
| applied | 2 |
| exploratory_only | 99 |

100% das 101 sugestões têm `validation_status='blocked_no_validation'`, incluindo as 2 `applied` (P1-1/P1-2 da auditoria).

## profile_intelligence_autopilot_candidates

| state | count |
|---|---|
| SHADOW_COLLECTING | 30 |
| DISABLED | 61 |

Nenhum candidato em `PENDING_HUMAN_APPROVAL`, `APPROVED` ou `LIVE_ACTIVATED`.

## config_profiles (14 tipos, 1 linha ativa cada)

`ai-settings`, `autopilot_guardrails`, `block`, `decision_log`, `indicators`, `ml`, `ml_research`, `profile_intelligence`, `risk`, `score`, `signal`, `spot_engine`, `strategy`, `universe`.

Não existe `config_type='orchestrator_weights'` (P1-4 — será criado nesta implementação, Fase 3).

## Riscos identificados antes de iniciar

1. Working tree do repositório já tinha ~700 arquivos modificados/deletados antes desta implementação começar (majoritariamente cache do `graphify-out/` e alguns runbooks em `docs/runbooks/`), **não relacionados a este trabalho**. Nenhum desses arquivos será tocado ou commitado por esta implementação — apenas os arquivos explicitamente listados nos commits desta branch.
2. `dataset_contract_id` e `source_filter` em `ml_models` existem como colunas desde a migration 101 mas **nunca foram populados por nenhum INSERT** até esta implementação (confirmado: 0 modelos com esses campos preenchidos). Corrigido na Fase 2 (Promotion Gate) — `_save_to_db` agora os calcula e grava.
3. Nenhum código no backend faz `UPDATE ml_models SET status='active'` — a promoção de v44/v46 para `active` foi manual (SQL direto fora da aplicação, fora do escopo desta auditoria). Não existe hoje um endpoint de promoção automática a ser "travado" — esta implementação adiciona o Promotion Gate como pré-requisito para qualquer promoção futura, mas não altera os 2 modelos já `active` (regra: não apagar, não promover/despromover automaticamente sem confirmação).

## Decisão sobre v44/v46 já ativos

Conforme Fase 2 da especificação: "Não apagar. Reavaliar gates. Marcar como ineligible_for_ranking dentro de metrics_json/promotion_gate." Esta implementação:
- Mantém `status='active'` inalterado nos dois modelos (não despromove automaticamente).
- Roda o Promotion Gate sobre eles via backfill (`backend/scripts/backfill_model_promotion_gate.py`, dry-run por padrão) e grava o resultado (`REJECTED`, esperado) em `metrics_json.promotion_gate`.
- A partir dessa gravação, o filtro de elegibilidade usado por inferência/ranking (Fase 1) passa a exigir `promotion_gate.status='APPROVED'`, então v44/v46 deixam de ser **selecionáveis por código novo** mesmo permanecendo `status='active'` no banco — efeito prático equivalente a "ineligible_for_ranking" sem alterar o campo `status` diretamente.
