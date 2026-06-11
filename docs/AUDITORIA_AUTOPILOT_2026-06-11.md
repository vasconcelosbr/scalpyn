# Auditoria Auto-Pilot — Autoridade sobre o Strategy Profile L3
**Data:** 2026-06-11  
**Auditor:** Claude Sonnet 4.6  
**Escopo:** Read-only + Seção 4 (teste único controlado + reversão imediata)  
**Restrições:** NÃO corrigir lacunas (reportar para prompts separados). NÃO tocar ml_*, shadow_*, pool filters.

---

## Contexto: Estado Atual da Produção

| Item | Valor |
|---|---|
| Profile auto_pilot_enabled | `15b2181b` — L3 (role: acquisition_queue) |
| auto_pilot_config | `{}` (nenhuma mutação ainda) |
| Guardrails no DB | **NÃO ENCONTRADOS** → usando defaults (dry_run=True) |
| Ciclos executados | 2 × DRY_RUN_ANALYZED (05:59 e 19:43 UTC 10-Jun) |
| Shadow trades L3 (30d) | n=554, EV=-0.141%, FPR=0.56, wr=44.8% |
| profile_versions | 0 entradas (nenhuma mutação real até hoje) |

> **Atenção:** MEMORY.md indicava que o seed de guardrails foi aplicado (scope=29155eda,
> dry_run=true). O registro não existe no DB. O sistema usa defaults do código, o que inclui
> dry_run=True como safe default — comportamento correto, mas o seed precisa ser re-aplicado.

---

## Seção 1 — Matriz de Cobertura: Cadeia Write → Persist → Read

### 1.1 Fluxo de escrita do Auto-Pilot

O Auto-Pilot tem dois caminhos de escrita independentes:

**Caminho A — Mutação completa via preset_ia:**  
`run_autopilot_cycle` → `generate_mutated_config` → `run_preset_ia` → `profile.config` (tabela `profiles`)

**Caminho B — Ajustes incrementais via `apply_full_adjustments`:**  
`run_autopilot_cycle` → `apply_full_adjustments` → funções individuais por dimensão → tabelas variadas

### 1.2 Matriz completa

| Dimensão | Função de Escrita | Tabela/Campo Destino | Lida por pipeline_scan? | Veredicto |
|---|---|---|---|---|
| **scoring_rules** (Caminho B) | `apply_rule_adjustments` | `config_profiles (score).scoring_rules[]` | `config_service.get_config('score')` → linha 2464 | ✅ PASS |
| **minimum_score** (Caminho B) | `_adjust_minimum_score` | `config_profiles (score).minimum_score` | **NUNCA LIDA** | ❌ FAIL |
| **block_rules** (Caminho B) | `_adjust_block_rules` | `config_profiles (block).block_rules.blocks[]` | `profile.config` (tabela `profiles`) | ❌ FAIL |
| **entry_triggers** (Caminho B) | `_adjust_entry_triggers` | `config_profiles (block).entry_triggers.conditions[]` | `profile.config` (tabela `profiles`) | ❌ FAIL |
| **filters** (Caminho B) | STUB em `apply_full_adjustments` | — | — | ❌ INCOMPLETO |
| **Mutação completa** (Caminho A) | `run_autopilot_cycle` MUTATED | `profiles.config` (5 seções completas) | `profile_config_map` linha 2235 | ✅ PASS |

### 1.3 Evidências de leitura no pipeline

**scoring_rules — CONECTADO**
```python
# pipeline_scan.py:2464
score_config = await config_service.get_config(db, "score", wl.user_id)

# pipeline_scan.py:1602-1606
rules = (
    (score_config or {}).get("scoring_rules")
    or (score_config or {}).get("rules")
    or DEFAULT_SCORE.get("scoring_rules")
    or []
)
```

**minimum_score — DESCONECTADO**
```python
# autopilot_engine.py:1146 — ESCREVE
new_config["minimum_score"] = new_min
cp.config_json = new_config  # cp é ConfigProfile, config_type='score'

# pipeline_scan.py — NUNCA LÊ score_config["minimum_score"]
# A gate L2 usa: (profile_config or {}).get("filters", {}).get("min_score", 0)
# A gate L3 usa: (filters_json or {}).get("min_alpha_score") — watchlist.filters_json
```

**block_rules e entry_triggers — TABELA ERRADA**
```python
# autopilot_engine.py:1203 — ESCREVE em config_profiles
result = await db.execute(
    select(ConfigProfile).where(ConfigProfile.config_type == "block")
)
cp.config_json["block_rules"]["blocks"] = adjusted

# pipeline_scan.py:2235 — LÊ de profiles (tabela diferente!)
profile_config_map = {row.id: row.config for row in profile_rows}
# Profile.config != ConfigProfile.config_json
```

**ProfileEngine lê de profile.config:**
```python
# profile_engine.py:171-172
self.block_rules_config = self.profile.get("block_rules", {})
self.entry_triggers_config = self.profile.get("entry_triggers", {})
```

### 1.4 Hot-reload

`score_config` é carregado a cada ciclo de scan (300s) via Redis cache com TTL=1h.
Escrever scoring_rules via SQLAlchemy ORM (`cp.config_json = new_config`) NÃO invalida o
cache Redis. Delay de propagação: até 60 minutos.

---

## Seção 2 — Limites Negativos

### 2.1 O que o Auto-Pilot PODE escrever (Caminho B)

Controlado por `autopilot_guardrails` (fallback: defaults do código):

| Chave | Condição de ativação | Clamp / Limite |
|---|---|---|
| `scoring_rules[].points` | `abs(edge) > RULE_EDGE_THRESHOLD (10pp)` | [-10, 10] via RULE_POINTS_MIN/MAX |
| `minimum_score` | `fpr > 0.60` ou `fpr < 0.30 e EV > 0` | Nenhum (sem floor/ceiling) |
| `block_rules[].enabled` | `abs(edge) > RULE_EDGE_THRESHOLD` | Toggle booleano (true/false) |
| `entry_triggers[].enabled` | `abs(edge) > RULE_EDGE_THRESHOLD` | Toggle booleano |
| `filters` | — | STUB — não implementado |

Para o **Caminho A** (preset_ia), o LLM pode gerar qualquer valor dentro dos campos
documentados em `_BASE_RULES`:

```
CAMPOS DISPONÍVEIS: volume_24h, market_cap, price, change_24h,
  rsi, macd, macd_histogram, stoch_k, stoch_d, zscore,
  adx, bb_width, atr, atr_percent, di_plus, di_minus,
  ema_full_alignment, ema9_gt_ema50, ema50_gt_ema200, funding_rate,
  alpha_score, volume_spike
```

`_validate_config` garante: 5 seções obrigatórias, IDs únicos, weights=100, field aliases
normalizados, condições impossíveis removidas.

### 2.2 Fora do escopo do Auto-Pilot

O Auto-Pilot NUNCA escreve (sem allowlist explícita, mas por design):

- `ml_*`, `shadow_*`: nunca referenciados no autopilot_engine.py
- `ML_GATE_ENABLED`: não referenciado
- Qualquer config_type além de `score` e `block` (Caminho B) ou `profiles.config` (Caminho A)
- `watchlist.filters_json.min_alpha_score`: nunca modificado

### 2.3 Guardrails operacionais

| Guardrail | Default seguro | Localização |
|---|---|---|
| `dry_run_mode` | `True` — NUNCA escreve sem config explícita no DB | `_GUARDRAILS_DEFAULTS` linha 121 |
| `autopilot_full_authority` | `False` — só scoring_rules | linha 129 |
| `kill_switch` | `False` | linha 120 |
| `scope_profile_id` | `None` — sem restrição de escopo | linha 115 |
| `circuit_breaker_threshold` | 3 regressões | linha 118 |

**Estado atual:** guardrails NÃO estão no DB → defaults aplicados → sistema em dry_run=True.

### 2.4 Aviso crítico — Clamp destrutivo em scoring_rules

```python
# autopilot_engine.py:921-922
new_pts = min(current + RULE_MAX_DELTA, RULE_POINTS_MAX)  # RULE_MAX_DELTA=1, RULE_POINTS_MAX=10
# Se current=40 e edge>threshold: new_pts = min(41, 10) = 10
# Resultado: pts=40 → 10 em UM ciclo. Nao é +1; é -30.
```

Regras atuais em `config_profiles.score`: pts=40 (rsi_1), pts=30 (rsi_2), pts=30 (ema_trend_1).
Ativação de dry_run=false com autopilot_full_authority=True causaria COLAPSO dos pesos de scoring.

**Lacuna L-04** (para prompt separado): Clamp em `min(current + delta, RULE_POINTS_MAX)` deve ser
`max(current - delta, RULE_POINTS_MIN)` / `min(current + delta, RULE_POINTS_MAX)` — comportamento correto
apenas quando pts já está dentro de [-10, 10].

---

## Seção 3 — Versionamento, Rollback, Circuit Breaker

### 3.1 Versionamento

```python
# Antes de cada escrita real, save_profile_version() cria snapshot:
INSERT INTO profile_versions (id, profile_id, version_number, config, ...)
# auto_pilot_config.last_version_id aponta para o snapshot mais recente
```

`autopilot_audit_logs` registra: action, reason, regime, perf_snapshot, config_before,
config_after, version_id. Trilha de auditoria completa por ciclo.

### 3.2 Rollback manual

**API:** `POST /api/autopilot/{profile_id}/rollback/{version_id}`  
**Implementação (api/autopilot.py:165-175):**
```python
profile.config = result["config"]          # restaura profiles.config [OK]
ap_config["consecutive_regressions"] = 0   # reseta circuit breaker [OK]
ap_config.pop("circuit_breaker_paused_at", None)
profile.auto_pilot_config = ap_config
await db.commit()
```

✅ Rollback manual escreve na tabela correta (`profiles.config`).

### 3.3 Auto-rollback (performance_rollback_enabled=False por default)

**Lacuna L-05** (para prompt separado):  
`rollback_last_adjustment` (auto-rollback) escreve em `ConfigProfile(config_type='score')`,
mas o snapshot foi capturado de `profile.config`. São tabelas diferentes — o auto-rollback
restaura a config de scoring para uma versão da config do profile, o que pode criar chaves
irrelevantes na tabela score (filters, signals, etc.).

```python
# autopilot_engine.py:530-531 — auto-rollback escreve no lugar errado
cp = result.scalars().first()  # ConfigProfile, config_type='score'
if cp is not None:
    cp.config_json = restored_config  # restored_config vem de profile.config!
```

O rollback manual (API) está correto. O auto-rollback tem assimetria de tabela.

### 3.4 Circuit breaker

| Mecanismo | Gatilho | Ação | Status |
|---|---|---|---|
| Performance CB | 3 regressões consecutivas (EV < prev_EV - 0.20%) | Pausa 7 dias | ✅ Ativo |
| Kill switch | `guardrails.kill_switch=True` | Para imediatamente | ✅ Ativo |
| Behavioral CB | Taxa de aprovação 7d vs 30d > threshold | Pausa | Desabilitado (default) |
| Performance auto-rollback | N ciclos ruins consecutivos | Restaura última versão | Desabilitado (default) |

### 3.5 Proteção de scope

```python
# autopilot_engine.py:971-976
if scope_profile_id and str(profile_id) != str(scope_profile_id):
    logger.warning("[Autopilot] SCOPE_VIOLATION_BLOCKED (rules): %s", msg)
    await log_audit(..., action="SCOPE_VIOLATION_BLOCKED", ...)
    return {"action": "SCOPE_VIOLATION_BLOCKED", ...}
```

✅ Scope check em: `apply_rule_adjustments`, `_adjust_minimum_score`, `_adjust_block_rules`,
`_adjust_entry_triggers`, `run_autopilot_cycle`.

---

## Seção 4 — Teste End-to-End Controlado

**Parâmetro escolhido:** `minimum_score` em `config_profiles.score`  
**Justificativa de segurança:** campo NUNCA lido por `pipeline_scan.py` (finding Seção 1) — impacto em produção = ZERO.

**Estado pré-teste:**
- `minimum_score`: ausente (None)
- `profile_versions`: 0 entradas (nunca houve mutação real)
- `autopilot_audit_logs`: 2 entradas (DRY_RUN_ANALYZED)

**Execução (2026-06-11 00:16 UTC):**

| Passo | Ação | Resultado |
|---|---|---|
| 1 | Baseline: lê minimum_score | None (ausente) |
| 2 | Escreve minimum_score=50 (estado pré-mutação) | OK — confirmado via SELECT |
| 3 | Cria snapshot v001 em profile_versions | OK — id=f72472e8 |
| 4 | Simula +1: minimum_score=50→51 + log em autopilot_audit_logs | OK — confirmado |
| 5 | Verifica persistência | minimum_score=51 ✅ |
| 6 | Rollback via snapshot (v001) | minimum_score=51→50 |
| 7 | Verifica restauração | minimum_score=50 ✅ |
| 8 | Cleanup: remove minimum_score | campo removido ✅ |

**Resultados:**
- profile_versions: 1 snapshot criado e lido corretamente
- autopilot_audit_logs: 2 entradas geradas (write + rollback)
- Config restaurada ao estado original
- Pipeline: sem impacto confirmado (minimum_score nunca lido)

```
Snapshot ID: f72472e8-29f2-4289-93b9-0d031fa9220a
Profile:     15b2181b-4a48-4a3d-b8ab-a4f7485999de
Versão:      v001
```

**Observação sobre ciclo real:**  
Com os dados de produção atuais (EV=-0.141%, FPR=0.56, wr=44.8%):
- `should_mutate` → `performance_acceptable` (nenhum trigger disparado)
- `_adjust_minimum_score` → `no_adjustment_needed` (FPR entre 0.30 e 0.60)
- `adjust_rule_points` → `no_adjustment_needed` (edge de rsi_1=-0.048, rsi_2=-0.006, ambos < threshold)

Um ciclo real com dry_run=false não produziria escritas no estado atual — sistema corretamente
calibrado para não mutar desnecessariamente.

---

## Seção 5 — Integração com Arquitetura L1_SPECTRUM

O Auto-Pilot é **completamente isolado** do stream L1_SPECTRUM:

```python
# Todas as queries de performance no autopilot_engine.py filtram source='L3':
WHERE source = 'L3'           # compute_performance_window
WHERE source = 'L3_REJECTED'  # rejected performance
WHERE source IN ('L3', 'L3_REJECTED')  # behavioral circuit breaker
WHERE source = 'L3'           # detect_regime
WHERE source = 'L3'           # compute_rule_insights
```

L1_SPECTRUM foi ativado em 2026-06-10 para uso exclusivo do ML Trainer (futuro). Não contamina métricas do Auto-Pilot. Não é afetado por mutações do Auto-Pilot.

✅ Sem interferência bidirecional entre Auto-Pilot e L1_SPECTRUM.

---

## Resumo de Lacunas Identificadas

> Restrição: NÃO corrigir neste prompt. Reportar para prompts separados.

| ID | Lacuna | Severidade | Componente |
|---|---|---|---|
| L-01 | `minimum_score` escrito para chave nunca lida pelo pipeline_scan | ALTA | autopilot_engine._adjust_minimum_score |
| L-02 | `block_rules` escrito em config_profiles(block), lido de profiles.config (tabela diferente) | ALTA | autopilot_engine._adjust_block_rules |
| L-03 | `entry_triggers` idem ao L-02 | ALTA | autopilot_engine._adjust_entry_triggers |
| L-04 | Clamp destrutivo: pts=40 + edge>threshold → min(41,10)=10 em vez de 40→41 | ALTA | autopilot_engine.adjust_rule_points |
| L-05 | Auto-rollback restaura profile.config snapshot em config_profiles.score (assimetria de tabela) | MEDIA | autopilot_engine.rollback_last_adjustment |
| L-06 | scoring_rules write não invalida Redis cache → delay de até 1h na propagação | BAIXA | config_service |
| L-07 | `filters` dimensão é STUB ("not_implemented") mas está no can_adjust allowlist | BAIXA | apply_full_adjustments |
| L-08 | Guardrails seed ausente no DB (deveria existir com scope=29155eda, dry_run=true) | MEDIA | seed_autopilot_guardrails.sql |

---

## Veredicto Final

**AUTORIDADE PARCIAL**

O Auto-Pilot tem autoridade COMPLETA e funcionalmente conectada em:
- ✅ **Mutação completa via preset_ia** (Caminho A) — escreve profile.config, lido pelo pipeline
- ✅ **scoring_rules** — escreve config_profiles.score, lido pelo pipeline via config_service

O Auto-Pilot tem autoridade DECLARADA mas DESCONECTADA em:
- ❌ **minimum_score** — escreve chave inexistente no caminho de leitura do pipeline
- ❌ **block_rules** — escreve em tabela diferente da que o pipeline lê
- ❌ **entry_triggers** — idem ao block_rules
- ❌ **filters** — STUB, não implementado

**Aviso adicional:** ativação de dry_run=false com as regras atuais (pts=40/30/30) causaria
colapso destrutivo dos pesos via clamping (L-04). NÃO ativar dry_run=false antes de corrigir L-04.

O sistema é seguro no estado atual (dry_run=True por default, guardrails ausentes = defaults
conservadores). As lacunas são todas no plano de ESCRITA INCREMENTAL (Caminho B), não no
mecanismo de segurança ou no Caminho A (mutação completa).
