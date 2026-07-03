# Execução B2–B8 — Sessão 3 — 2026-07-03

**Prompt-base:** `PROMPT_EXECUCAO_B2_B8.md`  
**Período:** 2026-07-03 (continuação das sessões 1 e 2 de 2026-07-02)  
**Resultado geral:** E1–E11 implementados; E9 executado — candidato gerado e gate avaliado corretamente; promoção **não realizada** (gate REJECTED — regime change confirmado)

---

## 1. Fases implementadas nesta sessão

| Fase | Descrição | Status |
|------|-----------|--------|
| E1.4 | `_transition_model_status()` — ponto único de transição de status | DONE |
| E7 | Contrato de features por lane + row-level rejection | DONE |
| E8 (CatBoost) | Lane CatBoost migrada para `_chronological_split_with_embargo` | DONE |
| E8 (LightGBM métricas) | `train_from`/`train_to` usam created_at pós-purge | DONE |
| E10 | Investigações operacionais — vereditos finais | DONE |
| E11 | Integridade de fechamento de trades | DONE |
| E9 | Retrain L1_SPECTRUM como candidato | DONE (gate REJECTED) |

---

## 2. Commits desta sessão

| Hash | Descrição |
|------|-----------|
| `d908775` | E1.4 / E7 / E8 CatBoost + audit doc |
| `06a3966` | fix: `dataset_valid_from` string→datetime (asyncpg rejeita str para TIMESTAMPTZ) |
| `965889f` | fix: `json.dumps` datetime em `hyperparams_full` |
| `a247e1a` | fix: `_json_default` em todos os `json.dumps` de `_save_to_db` |
| `e9c7031` | fix: `net_ev` incluído em `metrics_json["test"]` para gate evaluation |

---

## 3. Detalhes das implementações

### E1.4 — `_transition_model_status` (`ml_trainer/job.py:35`)

Função module-level criada como único ponto de transição de status de `ml_models`.

```python
_VALID_STATUSES = frozenset({"active", "retired", "rejected", "candidate"})

def _transition_model_status(conn, *, new_status: str, model_id=None) -> int:
    """Single authoritative point for ml_models status transitions.
    Direct SQL UPDATE ml_models SET status=... outside this function is PROHIBITED."""
```

O inline `UPDATE ml_models SET status='retired'...` substituído por:
```python
n_retired = _transition_model_status(conn, new_status="retired")
logger.info("[DB] _transition_model_status retired=%d (global models only)", n_retired)
```

### E7 — Feature Contract (`ml_challenger_service.py`)

Função `_apply_feature_contract(df, lane_contract, feature_ranges, lane_name)` inserida no nível de módulo.

- Rejeita linhas onde qualquer feature em `lane_contract["required"]` está NaN
- Aplica `ml_feature_ranges` (gt/gte/lt/lte) por feature
- Log de `rows_rejected_by_contract` em ambas as lanes
- Parâmetros `lane_contract` e `feature_ranges` passados para `_build_dataset` e `_build_l3_dataset`
- Valores lidos do config (`ml_feature_contract` e `ml_feature_ranges`) — zero hardcode

**Config prod (já existia):**
```json
"ml_feature_contract": {
  "L1_SPECTRUM": {"required": ["taker_ratio","volume_delta","rsi","macd_histogram_pct","adx",...28 total]},
  "L3_PROFILE":  {"required": ["taker_ratio","volume_delta","rsi","adx"]}
},
"ml_feature_ranges": {
  "rsi":        {"gte": 0, "lte": 100},
  "atr_pct":    {"gt": 0},
  "spread_pct": {"gte": 0}
}
```

**Evidência de execução no E9:**
```
Feature contract rejected 64/2179 rows (lane=L1_SPECTRUM)
rows_rejected_by_contract=64
```

### E8 — CatBoost + métricas LightGBM

`_build_l3_dataset` agora retorna `holding_seconds` como 8º valor.

Lane CatBoost migrada de `_chronological_split_with_test` para `_chronological_split_with_embargo`:
```python
_cb_split = self._chronological_split_with_embargo(
    X, y,
    metadata=[returns, created_at, shadow_ids],
    created_at=created_at,
    holding_seconds=holding_seconds,
    val_fraction=VAL_FRACTION,
    embargo_seconds=embargo_seconds,
)
```

`train_from`/`train_to` em ambas as lanes agora usam `_lgbm_split["meta_tr"][1]` / `_cb_split["meta_tr"][1]` (created_at **pós-purge** do split de treino, não do dataset completo).

---

## 4. Bugs encontrados e corrigidos durante a execução de E9

### Bug 1 — `dataset_valid_from` string→datetime (`commit 06a3966`)

**Erro:**
```
asyncpg.exceptions.DataError: invalid input for query argument $4:
'2026-06-14 21:33:10.277143+00' (expected a datetime.date or datetime.datetime instance, got 'str')
```

**Causa:** `ml_dataset_valid_from` lido do JSONB como string. asyncpg não converte str→TIMESTAMPTZ.

**Fix em `train_challengers`:**
```python
_dvf_raw = ml_config.get("ml_dataset_valid_from")
if isinstance(_dvf_raw, str):
    from datetime import datetime as _dt, timezone as _tz
    dataset_valid_from = _dt.fromisoformat(_dvf_raw.replace("+00", "+00:00")).replace(tzinfo=_tz.utc)
else:
    dataset_valid_from = _dvf_raw
```

---

### Bug 2 — datetime não serializável em `json.dumps` (`commits 965889f` + `a247e1a`)

**Erro:**
```
TypeError: Object of type datetime is not JSON serializable
when serializing dict item 'train_from'
```

**Causa:** `hyperparams_full = {**metrics}` herda `train_from`, `train_to`, `dataset_query_cutoff` como objetos `datetime`. Três locais em `_save_to_db` usavam `json.dumps` sem encoder customizado.

**Fix — função module-level:**
```python
def _json_default(obj):
    """JSON serializer for types not handled by the standard encoder (e.g. datetime)."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)
```

Aplicada nos três `json.dumps`:
- `json.dumps(_metrics_json_dict, default=_json_default)` — metrics_json JSONB
- `json.dumps(hyperparams_full, default=_json_default)` — hyperparams blob
- `json.dumps(metrics, default=_json_default)` — legacy metrics blob

---

### Bug 3 — `net_ev` ausente do dict `metrics_json["test"]` (`commit e9c7031`)

**Sintoma:** Gate reportava `missing_test_net_ev` mesmo com `test_net_ev` calculado pelo `_calibrate_ev_threshold`.

**Causa:** `_metrics_json_dict["test"]` era construído sem incluir `net_ev`:
```python
"test": {
    "precision": (test_metrics or {}).get("precision"),
    ...
    "roc_auc": (test_metrics or {}).get("roc_auc"),
    "samples": n_test or None,
    # net_ev estava ausente aqui
}
```

O gate lê `metrics_json["test"]["net_ev"]`, mas o valor só existia em `test_metrics["net_ev"]` (retornado por `_train_lightgbm_sync`).

**Fix:**
```python
"test": {
    ...
    "net_ev": (test_metrics or {}).get("net_ev"),  # adicionado
}
```

---

## 5. E10 — Investigações Operacionais

### E10.1 — v52 sem footprint em `ml_opportunity_rankings` (últimos 7 dias)

**Veredito:** `ml_forward_scoring_enabled: false` no config ML.

- 1304 rankings existentes: todos de 2026-06-30 12:23–19:14
- Último ranking: `2026-06-30 19:14:38`
- v52 está `active` + `gate=APPROVED` — o modelo está OK; o scorer está desligado
- **Ação pendente:** habilitar `ml_forward_scoring_enabled: true` quando E9 produzir candidato aprovado

### E10.2 — L3_PROFILE parada em 2026-06-30 19:14

**Veredito:** Dupla causa:
1. Nenhum modelo L3_PROFILE com `status='active'` + `gate=APPROVED` (v53 → gate REJECTED; v50 rebaixado B1)
2. `ml_forward_scoring_enabled: false` bloqueia mesmo que existisse modelo

### E10.3 — Mecanismo de promoção de v52 ("Etapa4 council plan")

**Veredito:** v52 promovido via migration SQL `c001v52activ` — UPDATE direto em `activated_at`. Não passou pelo challenger service nem pelo gate normal. `train_from / train_to / dataset_hash` = NULL no DB.

- E1.4 implementado para prevenir repetição: `_transition_model_status` é o único caminho válido
- Gate avaliado retroativamente: v52 teria REJECTED (`generalization_gap_exceeded:0.0588>0.05`, `missing_test_net_ev`, `missing_train_from`, etc.)

---

## 6. E11 — Integridade de Fechamento de Trades

### Completeness (pós valid_from 2026-06-14)

| Source | Closed | exit_snapshot | mae_mfe |
|--------|--------|---------------|---------|
| L1_SPECTRUM | 2171 | 92.4% | 98.2% |
| L3 | 15053 | 90.6% | 100% |
| L3_LAB | 4906 | 91.7% | 100% |
| L3_REJECTED | 17950 | 97.0% | 100% |
| L3_SIMULATED | 1956 | 90.7% | 100% |

### Trades abertos (NULL outcome) — L1+L3+L3_LAB, pós valid_from

| Métrica | Valor |
|---------|-------|
| Total abertos | 864 |
| Mais recente | 2026-07-03 14:04 |
| Mais antigo | 2026-06-17 17:00 (**381h = 15.9 dias**) |

Trade de 381h: candidato a stuck/orphan — sugestão de TIMEOUT forçado se símbolo ainda ativo.

### Distribuição de outcomes

| Outcome | N | Avg holding |
|---------|---|-------------|
| SL_HIT | 12952 | 7.9h |
| TP_HIT | 8887 | 4.5h |
| TIMEOUT | 294 | 36.9h |

**Win rate geral: 40.2%** — consistente com observações anteriores.

`volume_24h_usdt`: sem death date — ausência esporádica, já classificada como `optional` no contrato.

---

## 7. E9 — Resultado do candidato definitivo

### `lightgbm_v20260703_1421` | `id=1c2cf2f4-1992-4d97-8db0-1e6328f32400`

```
status=candidate
lane=L1_SPECTRUM
label=is_tp_4h_v2_sim_outcome
win_threshold_s=14400
```

### Pipeline de construção

| Etapa | Resultado |
|-------|-----------|
| Records carregados | 2179 L1_SPECTRUM (pós valid_from) |
| Rows rejeitadas (E7 contrato) | 64 |
| Rows usadas | 2115 |
| Split treino | 1257 (purge: 12, embargo: 39) |
| Split val | 384 |
| Split test (holdout) | 423 |
| Optuna trials | 30 / best=28 |

### Métricas

| Métrica | Validação | Teste (holdout) |
|---------|-----------|-----------------|
| AUC ROC | 0.6896 | 0.5784 |
| AUC PR | 0.5242 | 0.4704 |
| Precision | 63.8% | 46.9% |
| Recall | 22.6% | 13.9% |
| F1 | 0.333 | 0.215 |
| FPR | 6.8% | 10.1% |
| Net EV | — | −0.373 |
| Samples | 384 | 423 |

### Gate evaluation

```
status=REJECTED
reasons=[
  'test_roc_auc_below_min_threshold:0.5784<0.6',
  'generalization_gap_exceeded:0.1111>0.05',
  'test_net_ev_not_positive:-0.372892'
]
```

### Diagnóstico

O modelo discrimina razoavelmente na validação (AUC 0.69) mas colapsa no test set (AUC 0.578). Gap de 11pp + EV negativo no teste indicam **regime change**: o test set (~julho) tem distribuição diferente do treino+val (~junho). O modelo memorizou padrões de junho que não generalizam para julho.

**Promoção para `active`: NÃO realizada.** Gate cumpriu sua função.

**Próximas ações recomendadas:**
1. Acumular mais dados L1_SPECTRUM (target: 3000+ pós valid_from)
2. Investigar quais features são instáveis entre junho e julho (volume/liquidez vs momentum)
3. Considerar reduzir `lookback_days` para treinar em janela mais recente
4. Retreinar quando win rate se estabilizar (atualmente 31.7% base, era 35.7% h antes — flutuação intraday)

---

## 8. Estado final do pipeline ML

| Componente | Estado |
|-----------|--------|
| `promotion_gate.py` | Fail-closed, lê de config, inclui `net_ev` |
| `feature_extractor.py` | Label v2 `is_tp_4h_v2_sim_outcome`, sem ttt_* |
| `ml_challenger_service.py` | E6+E7+E8 ativos; `_json_default` para serialização; `net_ev` no gate dict |
| `ml_trainer/job.py` | `_transition_model_status` como ponto único de status |
| Config prod | `ml_label_version`, `ml_split_embargo_seconds=14400`, `ml_feature_contract`, `ml_dataset_valid_from` |
| v52 (active) | AUC val=0.712 — ainda ativo; gate legado APPROVED; sem proveniência completa |
| v20260703_1421 (candidate) | Gate REJECTED — não promovido; salvo para auditoria |
| Forward scoring | `ml_forward_scoring_enabled=false` — desabilitado até novo candidato aprovado |
