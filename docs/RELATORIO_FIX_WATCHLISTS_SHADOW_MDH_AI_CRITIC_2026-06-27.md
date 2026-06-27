# RELATÓRIO — CORREÇÃO DOS BLOQUEIOS: WATCHLISTS L3, TR ZUMBIS, MDH E AI CRITIC

**Data:** 2026-06-27  
**Prompt base:** `PROMPT_FIX_WATCHLISTS_SHADOW_MDH_AI_CRITIC_2026-06-27.md`  
**Estágio inicial:** `WATCHLIST_SHADOW_AUTOCALIBRATION_BLOCKED`  
**Estágio final:** `PIPELINE_PARTIALLY_RESTORED_REMAINING_DATA_FEED_BLOCKERS`  
**HEAD (auditoria):** `d719ce7`

---

## 1. Resumo Executivo

| Item | Status | Evidência |
|---|---|---|
| Shadow closer completou trades pós-fix | **PASS** | 471 completions pós-deploy (L3:168, L3_LAB:216, L1:34, L3_SIM:30, L3_REJ:23) |
| TR 219 PENDING zumbis limpos | **PASS** | 219 → CANCELLED; TR PENDING=0 |
| L3 watchlists repovoadas | **PARCIAL** | 28/102 populadas (era 2/102); 74 vazias (37 inactive profiles, ~37 strict filters) |
| L3 gera decisions/shadow | **PASS** | 111 ALLOW + 90 shadows L3 + 65 L3_LAB (24h) |
| L1 gera shadows | **PASS** | 15 shadows L1_SPECTRUM (24h), last=14:54 UTC |
| MDH zscore | **SKIP OPCIONAL** | Alternativa C em vigor — SKIPPED não bloqueia trades |
| AI Critic tokens > 0 | **PENDING** | Fix `1eee131` deployado; próximo ciclo 17:03 UTC |
| PI medium gera indicators/hard neg/suggestions | **PASS** | 624 rows, 256 hard neg, 62 REDUCE_RISK (14:11 UTC) |
| Activity Timeline registra ações | **PASS** | HEARTBEAT SCANNING_SHADOW cada 5 min |
| Nenhum profile criado | **PASS** | profiles_created_24h=0 |
| Nenhuma mutação/live/model active | **PASS** | live=0, orders=0, new_models=0, mutations=0 |

---

## 2. Pre-flight Safety (Fase 0)

**SQL executado às ~14:47 UTC (read-only):**

```
live_enabled=0          ✓
autopilot_enabled=1     (auto-pilot ativo para 1 profile — esperado)
total_profiles=109
possible_live_orders=0  ✓
active_new_models_24h=0 ✓
```

**Railway vars confirmadas:**
- `ML_GATE_ENABLED` ausente → `false` (default)
- `PI_LIVE_LOOKBACK_H=168` no scalpyn-worker-compute
- `AI_KEYS_ENCRYPTION_KEY` presente em scalpyn-worker-compute e scalpyn

**Resultado:** `SAFETY_PRECHECK_PASS`

---

## 3. Fase A — Shadow Closer pós-fix `d719ce7`

### A.1 Deploy confirmado
HEAD no worker-execution: `d719ce7` (confirmado via git log + Railway redeploy automático após push ~14:xx UTC).

### A.2 Ausência do erro antigo
Nenhuma linha `NoReferencedTableError` ou `Foreign key associated with column 'shadow_trades.ranking_id'` nos logs pós-deploy do `scalpyn-worker-execution`.

### A.3 Trades fechados (completions pós-deploy)

| source | completed_1h | completed_6h | last_completed |
|---|---:|---:|---|
| L3_LAB | 216 | 216 | 2026-06-27 14:43:59 UTC |
| L3 | 168 | 168 | 2026-06-27 14:43:58 UTC |
| L3_SIMULATED | 30 | 30 | 2026-06-27 14:43:58 UTC |
| L1_SPECTRUM | 34 | 34 | 2026-06-27 14:21:04 UTC |
| L3_REJECTED | 23 | 23 | 2026-06-27 14:21:04 UTC |

**Resultado: PASS** — 471 trades fechados na 1ª hora após deploy. `last_completed > 2026-06-25 19:45 UTC` ✓

### A.4 Open trades processados

| source | status | total | proc_1h | newest_proc |
|---|---|---:|---:|---|
| L3 | RUNNING | 83 | 83 | 2026-06-27 14:40 UTC |
| L3_LAB | RUNNING | 32 | 20 | 2026-06-27 14:45 UTC |
| L3_SIMULATED | RUNNING | 26 | 26 | 2026-06-27 14:40 UTC |
| L1_SPECTRUM | RUNNING | 14 | 14 | 2026-06-27 14:40 UTC |

**Resultado: PASS** — RUNNING trades sendo processados ativamente.

---

## 4. Fase B — TR PENDING Cleanup

### B.1 Validação de escopo

```
tr_pending_total=219    ✓ (exact match)
null_entry_ts=219       ✓
null_tp=219             ✓
null_sl=219             ✓
oldest=2026-06-22 18:32 UTC
newest=2026-06-25 19:45 UTC
```

**Contrato bate exatamente. Prosseguiu para B.2.**

### B.2 Snapshot pré-limpeza

```sql
-- Tabela shadow_trades_cleanup_audit criada
INSERT INTO shadow_trades_cleanup_audit ...
-- WHERE symbol='TR' AND status='PENDING' AND entry_timestamp IS NULL AND tp_price IS NULL AND sl_price IS NULL
```

```
snapshot_rows=219 ✓
audit_reason='TR_PENDING_ZOMBIE_CLEANUP_PRE'
```

### B.3 Cleanup controlado

```sql
UPDATE shadow_trades
SET status='CANCELLED', outcome=COALESCE(outcome,'invalid_symbol'),
    skip_reason=COALESCE(skip_reason,'invalid_symbol_TR'),
    updated_at=now(), completed_at=COALESCE(completed_at,now())
WHERE symbol='TR' AND status='PENDING' AND entry_timestamp IS NULL
  AND tp_price IS NULL AND sl_price IS NULL
```

```
rows_updated=219
remaining_TR_pending=0
→ COMMIT OK
```

### B.4 Estado TR pós-cleanup

| symbol | status | total | last_update |
|---|---|---:|---|
| TR | CANCELLED | 219 | 2026-06-27 14:49:39 UTC |

**Resultado: PASS** — TR PENDING=0, TR CANCELLED=219 ✓

---

## 5. Fase C — L3 Watchlists

### C.1 Estado atual

| métrica | antes (auditoria) | agora |
|---|---:|---:|
| L3 watchlists total | 102 | 102 |
| L3 vazias | 100 | 74 |
| L3 populadas | 2 | 28 |
| total L3 assets ativos (level_direction=NULL) | 2 | 21 |
| last_scan (populadas) | 2026-06-20 | 2026-06-27 14:xx UTC |

### C.2 Categorização das 74 vazias

| categoria | count | causa |
|---|---:|---|
| Scanned em <1h, sem assets | 29 | Filtros L3 muito restritivos — nenhum símbolo passou hoje |
| Scanned em 1h–7d | 24 | Idem; scans regulares mas sem candidatos |
| Nunca escaneadas (last_scan=NULL) | 37 | `auto_refresh=False` + profile `is_active=False` |

**As 37 nunca-escaneadas** são de perfis inativados (`is_active=False, auto_pilot_enabled=False`). Behavior esperado — auto-pilot desativado não gera scans. Não requer ação.

**As 29+24 recentemente escaneadas** são watchlists com condições muito específicas (ex: `adx_gte_35_AND_ema50_gt_ema200_AND_rsi_gte_72`) — nenhum símbolo qualifica no momento. Behavior normal.

### C.3 Pipeline L3 ativo

```
decisions_log L3 ALLOW (24h): 111  last=14:49 UTC ✓
shadow_trades L3 (24h):         90  last=14:49 UTC ✓
shadow_trades L3_LAB (24h):     65  last=14:50 UTC ✓
```

**Resultado: PARCIAL** — Pipeline L3 operacional. 28/102 watchlists populadas. 74 vazias por causas documentadas (profiles inativos + filtros restritivos). Nenhuma ação corretiva necessária além do que já está em execução.

---

## 6. Fase D — MDH: zscore e ema9_gt_ema50

### D.1 Origem do problema

```
INFO:app.services.indicator_validity:{"event": "indicator_skipped", "indicator": "zscore", "value": null, "status": "SKIPPED", "reason": "indicator_not_available"}
```

**Origem:** `indicator_validity.py` no `scalpyn-worker-compute` — chamado pela PI medium cycle ao avaliar condições de profiles contra `features_snapshot` de shadow trades.

**Não é MDH.** MDH é usado exclusivamente para macro features de ML (em `ml/macro_client.py`). Os indicadores do pipeline (rsi, ema, adx, etc.) são computados localmente por `_feature_engine.calculate(df)`.

### D.2 Root cause

`zscore` está configurado em `spot_scanner.py:606` (scanner legado) mas **não** é computado pelo `pipeline_scan.py`. As `features_snapshot` de shadow trades recentes confirmam:

```
L1_SPECTRUM LTC_USDT: ['adx','atr','obv','rsi','ema5','ema9','macd','psar','vwap',
                        'close','ema10','ema21','ema30','ema50','price','rsi_6','score',
                        'ema200','rsi_12','rsi_24'...]  → zscore=ABSENT
```

### D.3 Alternativa escolhida: C (já em vigor)

A Alternativa C (tornar indicador opcional) está **já implementada** por design:
- `indicator_validity.py` retorna `SKIPPED` quando indicador ausente (nunca `FAIL`)
- Pipeline continua; regra que usa zscore não é avaliada, não bloqueia trade

**Impacto em L1:** L1_SPECTRUM gerou 15 shadows hoje sem zscore. **Nenhum bloqueio.**

**`ema9_gt_ema50`:** usado apenas em `futures_pipeline_scorer.py` (futuros). L1_SPECTRUM é spot. **Sem impacto.**

### D.4 Evidência pós-fix (Alternativa C em vigor)

```
l1_shadow_24h=15    last=2026-06-27 14:54 UTC
l3_decisions_24h=111  last=2026-06-27 14:49 UTC
```

**Resultado: SKIP OPCIONAL** — Alternativa C em vigor. Não bloqueia pipeline. `zscore` é ausência de feature no pipeline_scan (não MDH). Adicioná-lo seria melhoria futura, não correção de bloqueio.

---

## 7. Fase E — AI Critic

### E.1 Chave Anthropic

```sql
provider=anthropic  active=True  validated=True  updated=2026-06-27 13:19 UTC
```

`AI_KEYS_ENCRYPTION_KEY` presente em `scalpyn-worker-compute` e `scalpyn` (mesma chave Fernet).

### E.2 Fix `1eee131` deployado

Commit `1eee131` (2026-06-27 10:25 UTC local / 13:25 UTC) adicionou fallback em `profile_intelligence_live_service.py:480-495`:

```python
ai_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not ai_key:
    # Fallback: read active+validated key from DB (ai_provider_keys)
    key_row = await db.execute(text("SELECT api_key_encrypted ..."))
    enc = key_row.scalar_one_or_none()
    if enc:
        ai_key = decrypt_value(enc)
```

### E.3 Reviews existentes (pré-fix)

| review_id | status | tokens_input | tokens_output | requested_at | next_review_at |
|---|---|---:|---:|---|---|
| 0021d049 | COMPLETED | 0 | 0 | 13:03:40 UTC | **17:03:40 UTC** |
| eec32b85 | COMPLETED | 0 | 0 | 09:03:15 UTC | 13:03:15 UTC |
| 801966a9 | COMPLETED | 0 | 0 | 04:58:24 UTC | — |

Todas as reviews hollow (tokens=0) são **pré-deploy** do fix. O fix foi commitado às 13:25 UTC; o Railway fez redeploy de `d719ce7` (que inclui `1eee131`) às ~14:xx UTC.

### E.4 Próximo ciclo

`next_review_at = 2026-06-27 17:03:40 UTC` — primeiro ciclo com o código corrigido **e** `AI_KEYS_ENCRYPTION_KEY` presente no worker. Deve retornar `tokens_input > 0`.

**Resultado: PENDING_VALIDATION** — Fix deployado, chave disponível. Validação na próxima execução (17:03 UTC).

---

## 8. Fase F — Profile Intelligence Medium Cycle

### Última execução: 2026-06-27 14:11:45 UTC

Activity log confirma:
```
MINING_INDICATORS           → phase=medium
MINING_HARD_NEGATIVES       → phase=medium
GENERATING_ADJUSTMENT_SUGGESTIONS → phase=medium
SUGGESTION_CREATED REDUCE_RISK L3_VOLATILIDADE_MODERADA_V3
SUGGESTION_CREATED REDUCE_RISK L3_PULLBACK_TENDENCIA_V4
[→ IDLE]
HEARTBEAT SCANNING_SHADOW   (cada 5 min — último: 14:56 UTC)
```

### Tabelas F

| tabela | rows | profiles | indicadores/tipo | last |
|---|---:|---:|---|---|
| profile_indicator_performance | 624 | 39 | 4 distintos | 14:11 UTC |
| profile_hard_negative_patterns | 256 | 37 | — | 14:11 UTC |
| profile_adjustment_suggestions (PENDING_SHADOW_VALIDATION) | 62 | — | REDUCE_RISK | 14:11 UTC |

**Resultado: PASS** ✓

---

## 9. Fase G — Validação End-to-End

### G.1 L3

```
decisions_log L3 ALLOW (24h): 111    last=14:49 UTC
shadow_trades L3 (24h):         90    last=14:49 UTC
shadow_trades L3_LAB (24h):     65    last=14:50 UTC
```

**PASS** ✓

### G.2 L1

```
l1_shadow_24h=15    completed=1    open=14    symbols=15    last=14:54 UTC
```

**PASS** ✓

### G.3 Shadow Analyzer API

Endpoint `/api/profile-intelligence/live/shadow-summary?hours=24` retornou HTTP 401 (JWT inválido em ambiente de teste local — Railway não resolve JWT sem chave correta). Dados confirmados via SQL direto em G.1/G.2.

---

## 10. Fase H — Safety Final

### H.1 Profiles criados

```
profiles_created_24h=0 ✓
total_profiles=109 (inalterado)
```

### H.2 Autopilot pending actions

```
(no rows) ✓
→ 0 CREATE_PROFILE / 0 DUPLICATE_PROFILE / 0 PROMOTE_LIVE / 0 ENABLE_LIVE
```

### H.3 Mutações aplicadas

```
mutations_applied_24h=0 ✓
```

### H.4 Live/model safety

```
live_enabled=0          ✓
possible_live_orders=0  ✓
active_new_models_24h=0 ✓
ML_GATE_ENABLED=false   ✓
```

**Safety: PASS** ✓

---

## 11. Deploy

### Commits ativos

| commit | descrição |
|---|---|
| `d719ce7` | fix: use_alter=True on ranking_id FK — shadow monitor |
| `1eee131` | fix(pi-live): AI Critic reads Anthropic key from DB |

Ambos em `main`, deployados via Railway auto-redeploy (~14:xx UTC). Serviços atualizados: `scalpyn`, `scalpyn-worker-execution`, `scalpyn-worker-compute`, `scalpyn-worker-structural`, `scalpyn-worker-micro`, `scalpyn-beat`.

---

## 12. Tabelas Obrigatórias K.1–K.5

### K.1 Shadow Closer

| source | completed_1h | completed_6h | last_completed | processed_1h | status |
|---|---:|---:|---|---:|---|
| L3_LAB | 216 | 216 | 14:43:59 UTC | 20 | PASS |
| L3 | 168 | 168 | 14:43:58 UTC | 83 | PASS |
| L3_SIMULATED | 30 | 30 | 14:43:58 UTC | 26 | PASS |
| L1_SPECTRUM | 34 | 34 | 14:21:04 UTC | 14 | PASS |
| L3_REJECTED | 23 | 23 | 14:21:04 UTC | — | PASS |

### K.2 TR Cleanup

| métrica | valor |
|---|---:|
| TR PENDING antes | 219 |
| Snapshot rows (shadow_trades_cleanup_audit) | 219 |
| TR rows atualizadas | 219 |
| TR PENDING depois | 0 |
| TR CANCELLED depois | 219 |

### K.3 L3 Watchlists

| métrica | antes | depois |
|---|---:|---:|
| L3 watchlists total | 102 | 102 |
| L3 vazias | 100 | 74 |
| L3 populadas | 2 | 28 |
| total L3 assets ativos | 2 | 21 |
| L3 decisions/24h | 0 | 111 |
| L3 shadows/24h | 0 | 90 |

### K.4 L1/MDH

| indicador | antes | depois | fonte | status |
|---|---|---|---|---|
| zscore | unavailable | SKIP OPCIONAL | pipeline_scan (não computa) | Alternativa C — não bloqueia |
| ema9_gt_ema50 | unavailable | N/A spot | futures_pipeline_scorer | Não afeta L1 spot |
| L1 shadows/24h | 0 | 15 | pipeline_scan → shadow_monitor | PASS |

### K.5 AI Critic

| review_id | status | tokens_in | tokens_out | summary | resultado |
|---|---|---:|---:|---|---|
| 0021d049 | COMPLETED | 0 | 0 | NULL | HOLLOW (pré-fix) |
| (17:03 UTC) | — | — | — | — | PENDING_VALIDATION |

---

## 13. Ledger de Evidências K.3

| Afirmação | Origem | Valor literal |
|---|---|---|
| Completions pós-deploy 1h L3 | SQL shadow_trades completed_at >= now()-1h | 216 (L3_LAB), 168 (L3), 34 (L1) |
| TR PENDING antes | SQL COUNT WHERE symbol='TR' AND status='PENDING' | 219 |
| TR contract bate | SQL null_entry=219, null_tp=219, null_sl=219 | 219/219/219 |
| Snapshot criado | SQL COUNT FROM shadow_trades_cleanup_audit | 219 |
| TR PENDING depois | SQL COUNT WHERE symbol='TR' AND status='PENDING' | 0 |
| TR CANCELLED depois | SQL GROUP BY symbol, status | 219 |
| L3 watchlists total | SQL COUNT pipeline_watchlists WHERE level='L3' | 102 |
| L3 populadas antes | Auditoria anterior (session summary) | 2 |
| L3 populadas agora | SQL assets_count>0 GROUP BY pw.id | 28 |
| L3 decisions 24h | SQL decisions_log WHERE strategy='L3' AND created_at >= now()-24h | 111 ALLOW |
| L3 shadows 24h | SQL shadow_trades WHERE source='L3' AND created_at >= now()-24h | 90 |
| L3_LAB shadows 24h | SQL shadow_trades WHERE source='L3_LAB' | 65 |
| L1 shadows 24h | SQL shadow_trades WHERE source='L1_SPECTRUM' | 15 |
| zscore SKIP | Railway log worker-compute indicator_validity.py | indicator_skipped, reason=indicator_not_available |
| zscore em features_snapshot | SQL features_snapshot L1_SPECTRUM LTC_USDT | zscore=False (ausente) |
| AI key active | SQL ai_provider_keys WHERE provider='anthropic' | active=True, validated=True |
| AI_KEYS_ENCRYPTION_KEY worker | railway variables --service scalpyn-worker-compute | cweIPXDjg...= |
| AI next_review_at | SQL profile_ai_reviews ORDER BY requested_at DESC | 2026-06-27 17:03:40 UTC |
| PI indicators run | SQL + activity_log event_type=MINING_INDICATORS | 14:11:45 UTC |
| PI indicators rows | SQL profile_indicator_performance | 624 rows, 39 profiles |
| PI hard negatives | SQL profile_hard_negative_patterns | 256 rows, 37 profiles |
| PI suggestions | SQL profile_adjustment_suggestions PENDING_SHADOW_VALIDATION | 62 REDUCE_RISK |
| live_enabled | SQL COUNT profiles WHERE live_trading_enabled=true | 0 |
| possible_live_orders | SQL COUNT orders NOT IN cancelled/rejected/simulation/shadow | 0 |
| active_new_models_24h | SQL COUNT ml_models created_at>=now()-24h AND status=active | 0 |
| mutations_24h | SQL COUNT profile_adjustment_suggestions WHERE mutation_applied=true | 0 |
| HEAD commit | git rev-parse HEAD | d719ce7 |

---

## 14. Veredito

```
PIPELINE_PARTIALLY_RESTORED_REMAINING_DATA_FEED_BLOCKERS
```

### Itens PASS
- Shadow closer voltou a fechar trades após fix `d719ce7` (471 completions/1h)
- TR 219 PENDING zumbis limpos com snapshot e WHERE estrito
- L3 pipeline operacional: 111 ALLOW decisions + 90 L3 + 65 L3_LAB shadows (24h)
- L1 pipeline operacional: 15 shadows (24h)
- PI medium cycle: 624 indicators, 256 hard negatives, 62 suggestions (14:11 UTC)
- PI live heartbeat: SCANNING_SHADOW cada 5 min
- Safety: live=0, orders=0, new_models=0, mutations=0, profiles_created=0

### Itens PARCIAL/PENDING
- **L3 watchlists:** 28/102 populadas (74 vazias por profiles inativos ou filtros restritivos — pipeline funcionando)
- **AI Critic:** fix deployado, key disponível, mas próximo ciclo 17:03 UTC não ocorreu ainda
- **zscore:** Alternativa C em vigor (SKIP, não bloqueia); adicionar à pipeline_scan seria melhoria futura

### Ações residuais recomendadas
1. Validar AI Critic às 17:03 UTC: `SELECT tokens_input, tokens_output, summary FROM profile_ai_reviews ORDER BY requested_at DESC LIMIT 1`
2. Watchlists com `auto_refresh=False` para profiles inativos: sem ação (correto por design)
3. zscore em pipeline_scan: melhoria opcional para aumentar cobertura de PI analysis
