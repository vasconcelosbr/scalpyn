# RELATÓRIO — FIX L3 COMPLETED CONTRACT + PROFILE ELIGIBILITY
**Data:** 2026-06-26  
**Commit:** `4d57d2eb2e90dbc5c3a5604bf7b2de6ed2f0fa5d`  
**Veredito:** `XGB_DUAL_LANE_REJECTED_NO_OPERATING_POINT_AFTER_CONTRACT_FIX`

---

## 1. Pre-flight de Segurança (Fase 0)

| Campo | Valor | Gate |
|---|---|---|
| `live_enabled` | 0 | PASS |
| `autopilot_enabled` | 0 | PASS |
| `possible_live_orders` | 0 | PASS |
| `active_new_candidates` | 0 | PASS |
| `ML_GATE_ENABLED` | false (6 serviços) | PASS |
| git HEAD | `4d57d2eb` | PASS |

---

## 2. Contrato Real de shadow_trades (Fase A)

### A.1 — Status reais em shadow_trades (L3 + L3_LAB)
```
status='COMPLETED'   total=11147  with_pnl=11147  positive=4495
status='CANCELLED'   total=  670  with_pnl=    0
status='RUNNING'     total=  361  with_pnl=    0
status='PENDING'     total=  219  with_pnl=    0
```
**Fonte:** SQL read-only, 2026-06-26.

### A.2 — Contrato correto
```sql
source IN ('L3','L3_LAB')
AND status = 'COMPLETED'
AND pnl_pct IS NOT NULL
AND profile_id IS NOT NULL
```

### A.3 — Prova de que `closed` não existe
Não há nenhuma linha com `status='closed'` em `shadow_trades` para L3+L3_LAB. O status `COMPLETED` já implica `pnl_pct IS NOT NULL` (100% dos COMPLETED têm pnl).

**Código anterior:** o filtro `AND st.pnl_pct IS NOT NULL` (sem `status='COMPLETED'`) era funcionalmente equivalente, mas o contrato estava implícito, não explícito.

---

## 3. Correção do Builder L3 (Fase B)

### Mudança em `load_shadow_rows()` — `backend/scripts/run_xgb_dual_lane_labels.py:247`

Adicionado filtro explícito:
```python
AND st.status = 'COMPLETED'
AND st.pnl_pct IS NOT NULL
```

**Antes:** `AND st.pnl_pct IS NOT NULL` (implicitamente correto, mas contrato frágil)  
**Depois:** `AND st.status = 'COMPLETED'\n  AND st.pnl_pct IS NOT NULL` (contrato explícito)

O dataset L3 resultante: 9,226 rows = COMPLETED + profile_id IS NOT NULL + features_snapshot IS NOT NULL.

---

## 4. Maturidade por profile_id (Fases C + D)

### Maturidade agregada por profile_id (SQL, todos os dados):
```
PROFILE_THRESHOLD_ELIGIBLE (>=500)  profiles= 4  completed=3934  positives=1693
ML_ELIGIBLE         (100-499)       profiles=20  completed=4446  positives=1566
EXCLUDED_NULL_PROFILE               profiles= 1  completed=1921  positives= 936
EVALUATION_ONLY     (50-99)         profiles= 8  completed= 616  positives= 205
OBSERVATION_ONLY    (10-49)         profiles= 9  completed= 218  positives=  90
LOW_N               (<10)           profiles= 4  completed=  12  positives=   5
```
**Fonte:** SQL read-only por `GROUP BY profile_id`, 2026-06-26.

### Bug identificado e corrigido
Em `_profile_thresholds()`, `trade_count = int(len(part))` usava a **contagem do test set (20%)** como proxy de maturidade. Um profile ML_ELIGIBLE com 200 trades completos tem apenas ~40 no test → era classificado como `cold_start` incorretamente.

### Correção cirúrgica (4 mudanças)

**1. `classify_profile_threshold()` — `run_xgb_dual_lane_labels.py:563`**
```python
# Antes:
def classify_profile_threshold(trade_count: int, ...):
    if trade_count < 100:
        return "cold_start", "trade_count < 100"

# Depois:
def classify_profile_threshold(completed_trades_total: int, ...):
    if completed_trades_total < 100:
        return "cold_start", "completed_trades_total < 100"
```

**2. `_profile_thresholds()` — `run_xgb_dual_lane_labels.py:591`**
```python
# Aceita full_profile_counts dict; usa total do df completo, não do test
def _profile_thresholds(test, y_true, score, pnl, full_profile_counts: dict[str, int]):
    ...
    completed_trades_total = full_profile_counts.get(str(profile_id), 0)
```

**3. Output de `_profile_thresholds()` — campo `completed_trades_total` adicionado**  
Campo `trade_count` renomeado para `trade_count_test` (mantém contagem do test).

**4. `train_lane()` call site — `run_xgb_dual_lane_labels.py:729`**
```python
"profile_thresholds": _profile_thresholds(
    test, y_test, test_score, test_pnl,
    full_profile_counts=(
        bundle.df["_profile_id"].dropna().astype(str).value_counts().to_dict()
        if "_profile_id" in bundle.df.columns else {}
    ),
),
```

---

## 5. Testes (Fase D)

**22/22 passed** após atualização.

Testes atualizados/adicionados:
| Teste | Verifica |
|---|---|
| `test_classify_profile_cold_start_total_below_100` | completed_trades_total=14 → cold_start |
| `test_classify_profile_cold_start_insufficient_positives` | completed_trades_total=101, positive_count=10 → cold_start |
| `test_classify_profile_rejected_high_fpr` | fpr=0.714 → rejected |
| `test_classify_profile_rejected_negative_ev` | ev=-0.1 → rejected |
| `test_classify_profile_rejected_low_precision` | precision=0.40 → rejected |
| `test_classify_profile_approved_candidate` | passes all criteria → approved |
| `test_classify_profile_ml_eligible_513_completed` | **NEW**: completed=513 → approved_candidate |
| `test_classify_profile_ml_eligible_301_completed` | **NEW**: completed=301 → approved_candidate |
| `test_classify_profile_500_completed_approved_lt_30_is_insufficient_not_cold_start` | **NEW**: completed=500, approved<30 → insufficient_operating_sample |

**Fonte:** `pytest backend/tests/test_xgb_dual_lane_labels.py -q`, 2026-06-26.

---

## 6. L3 Feature Coverage (Fase E)

Dataset L3 com contrato correto (`status='COMPLETED'`, `profile_id IS NOT NULL`):
- `sample_count: 9226` (L3: 6381, L3_LAB: 2845)
- `feature_count: 24` (incluídas), 33 excluídas por `low_coverage`
- `excluded_count: 0` (por profile_id null — já filtrado em `load_shadow_rows`)

**Features críticas técnicas:**
```
TECHNICAL_FEATURES_NOT_AVAILABLE_IN_L3_COMPLETED_CONTRACT
```
`atr_pct`, `spread_pct`, `orderbook_depth_usdt`, `vwap_distance_pct`, `macd_histogram_pct`, `bb_width`, `ema9_gt_ema21`, `ema50_gt_ema200` — coverage=0% no `features_snapshot` de L3. Não disponíveis em `decisions_log.metrics` para L3_LAB. Modelo opera com 24 features técnicas + macro + profile (todas ≥ 60% coverage).

---

## 7. Correção de Notes Obsoletas (Fase F)

Modelos `0ae6df1a` e `c9244b53` tinham `notes` contradizendo `status=retired`:

```sql
UPDATE ml_models
SET notes = regexp_replace(regexp_replace(notes,
    'status=candidate', 'status=retired'),
    'promotion_gate_status=PENDING_EVIDENCE',
    'promotion_gate_status=REJECTED_NO_OPERATING_POINT')
WHERE id IN ('0ae6df1a...', 'c9244b53...')
```

Notes agora consistentes com `status=retired` e `gate=REJECTED_NO_OPERATING_POINT`.

---

## 8. Dry-run Pós-Correção (Fase G)

**Executado em:** 2026-06-26T17:45:54Z  
**Commit:** `4d57d2eb`  
**Verdict:** `XGB_DUAL_LANE_REJECTED_NO_OPERATING_POINT`  
**persisted:** false

### L1 (XGB_L1_SPECTRUM)
- `operating_point_status: NO_VALID_OPERATING_POINT`
- EV negativo em toda faixa com approved_count ≥ 30 (mercado desfavorável ao label)

### L3 (XGB_L3_PROFILE)
- `operating_point_status: OPERATIONAL` (threshold_global=0.75)
- test precision=0.3333, recall=0.0109, fpr=0.0144

### Profile Status (39 profiles no test set)

| Status | Count | Razão |
|---|---|---|
| `cold_start` | 11 | `completed_trades_total < 100` (legítimo) |
| `cold_start` | 23 | `positive_count < 30` no test set |
| `insufficient_operating_sample` | 5 | `approved_count < 30` (completed_total: 404–1318) |
| `approved_candidate` | 0 | — |

**Nenhum cold_start por fragmentação de watchlist.** Os cold_starts têm razão explícita e verificável.

Os 5 `insufficient_operating_sample` são os profiles mais maduros (completed_trades_total = 404, 664, 714, 1238, 1318). Precisam de threshold onde approved_count ≥ 30 — limitação estatística real.

---

## 9. Queries Finais de Segurança (Fase I)

```
active_new_candidates: 0
```
**Fonte:** SQL read-only, 2026-06-26T17:46Z.

---

## 10. Veredito Final

```
XGB_DUAL_LANE_REJECTED_NO_OPERATING_POINT_AFTER_CONTRACT_FIX
```

**O contrato foi corrigido:**
- `status='COMPLETED'` explícito em `load_shadow_rows()` ✓
- `profile_id IS NOT NULL` mantido ✓
- Maturidade por `profile_id` agregado implementada (`completed_trades_total` do full df) ✓
- Nenhum cold_start por fragmentação de watchlist ✓
- 22/22 testes passando ✓
- `active_new_candidates=0` ✓

**Mas os modelos continuam rejeitados:**
- L1: EV negativo em toda faixa operacional
- L3: 5 profiles maduros com `insufficient_operating_sample` (approved_count<30); 0 approved_candidate

**Próximos passos para approved_candidate:**
- Os 4 PROFILE_THRESHOLD_ELIGIBLE profiles (≥500 completed) precisam de um threshold onde approved_count ≥ 30 com ev>0, fpr≤0.20, precision≥0.50
- Isso requer mais trades onde o modelo score alto e os trades fecham com PnL positivo

---

## 11. Ledger de Evidências

| Afirmação | Origem | Valor literal |
|---|---|---|
| status='COMPLETED' é o contrato real | SQL GROUP BY | `COMPLETED total=11147 with_pnl=11147` |
| status='closed' não existe | SQL resultado | sem linhas |
| profile_id NULL excluído | `load_shadow_rows()` | `AND st.profile_id IS NOT NULL` |
| 4 PROFILE_THRESHOLD_ELIGIBLE | SQL maturity query | `profiles=4 completed=3934` |
| 20 ML_ELIGIBLE | SQL maturity query | `profiles=20 completed=4446` |
| Bug: trade_count usava test set | `run_xgb_dual_lane_labels.py:600` (antes) | `trade_count = int(len(part))` |
| Fix: completed_trades_total do full df | `run_xgb_dual_lane_labels.py:729` | `bundle.df["_profile_id"].value_counts()` |
| Commit do fix | git log | `4d57d2eb2e90dbc5c3a5604bf7b2de6ed2f0fa5d` |
| 22/22 testes | pytest output | `22 passed in 1.44s` |
| Dry-run verdict | script JSON | `XGB_DUAL_LANE_REJECTED_NO_OPERATING_POINT` |
| Dry-run commit | script JSON | `4d57d2eb` |
| 0 cold_start por watchlist | profile_thresholds output | razões: `completed_trades_total < 100` ou `positive_count < 30` |
| 5 insufficient_operating_sample | profile_thresholds output | completed_total 404–1318 |
| active_new_candidates=0 | SQL COUNT | `0` |
| Notes corrigidas | SQL UPDATE + verify | `status=retired promotion_gate_status=REJECTED_NO_OPERATING_POINT` |
| L3 feature coverage: 24 OK | leakage_audit | todas 24 incluídas ≥ 60% |
| Técnicas não disponíveis | leakage_audit | `TECHNICAL_FEATURES_NOT_AVAILABLE_IN_L3_COMPLETED_CONTRACT` |
