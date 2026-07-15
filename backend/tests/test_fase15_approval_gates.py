"""Fase 1.5 P3 — gates estatísticos de aprovação (opção degradada explícita:
split único leak-free + gate duro no test, em vez de CV walk-forward).

Cobre a extensão do promotion_gate (CI bootstrap do AUC exclui 0.5, cobertura
mínima de dias distintos no test), o determinismo do IC bootstrap, e o
fail-closed com chave ausente.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.promotion_gate import evaluate_promotion_gate, APPROVED, REJECTED, BLOCKED
from app.services.ml_challenger_service import _bootstrap_auc_ci_low


_FULL_CONFIG = {
    "ml_promotion_min_test_auc": 0.6,
    "ml_promotion_min_test_samples": 300,
    "ml_promotion_max_val_test_gap": 0.05,
    "ml_promotion_max_test_fpr": 0.5,
    "ml_promotion_require_positive_net_ev": True,
    "ml_approval_test_auc_ci_excludes_half": True,
    "ml_approval_min_distinct_days": 5,
}


def _model_row(**test_overrides):
    """Modelo sintético que passa TODOS os gates, salvo o que o teste sobrescreve."""
    test = {
        "roc_auc": 0.72, "fpr": 0.2, "samples": 400, "net_ev": 0.5,
        "roc_auc_ci_low": 0.61, "distinct_days": 7,
    }
    test.update(test_overrides)
    return {
        "metrics_json": {
            "test": test,
            "validation": {"roc_auc": 0.74},
        },
        "feature_count": 48,
        "label_version": "positive_net_return_v1",
        "model_lane": "L1_SPECTRUM",
        "source_filter": "L1_SPECTRUM",
        "dataset_contract_id": "ds_l1_spectrum_atrdyn_v2",
        "label_contract_id": "lc",
        "feature_contract_id": "fc",
        "train_from": "2026-07-15T00:00:00Z",
        "train_to": "2026-07-20T00:00:00Z",
        "dataset_query_cutoff": "2026-07-20T00:00:00Z",
        "dataset_hash": "deadbeef",
    }


def test_valid_candidate_is_approved():
    res = evaluate_promotion_gate(_model_row(), promotion_config=_FULL_CONFIG)
    assert res["status"] == APPROVED, res["reasons"]


def test_ci_low_at_or_below_half_is_rejected():
    """AUC alto no ponto mas IC inclui 0.5 (mecanismo v80) → REJECTED."""
    res = evaluate_promotion_gate(
        _model_row(roc_auc=0.72, roc_auc_ci_low=0.49),
        promotion_config=_FULL_CONFIG,
    )
    assert res["status"] == REJECTED
    assert any("test_auc_ci_includes_half" in r for r in res["reasons"])


def test_missing_ci_low_is_rejected_when_gate_enabled():
    res = evaluate_promotion_gate(
        _model_row(roc_auc_ci_low=None), promotion_config=_FULL_CONFIG
    )
    assert res["status"] == REJECTED
    assert "missing_test_roc_auc_ci_low" in res["reasons"]


def test_distinct_days_below_minimum_is_rejected():
    res = evaluate_promotion_gate(
        _model_row(distinct_days=3), promotion_config=_FULL_CONFIG
    )
    assert res["status"] == REJECTED
    assert any("test_distinct_days_below_minimum" in r for r in res["reasons"])


def test_missing_distinct_days_is_rejected():
    res = evaluate_promotion_gate(
        _model_row(distinct_days=None), promotion_config=_FULL_CONFIG
    )
    assert res["status"] == REJECTED
    assert "missing_test_distinct_days" in res["reasons"]


def test_fail_closed_when_new_approval_keys_absent():
    """Chave dos gates novos ausente → BLOCKED (fail-closed, não silencioso)."""
    cfg = {k: v for k, v in _FULL_CONFIG.items()
           if k != "ml_approval_test_auc_ci_excludes_half"}
    res = evaluate_promotion_gate(_model_row(), promotion_config=cfg)
    assert res["status"] == BLOCKED
    assert any("missing_promotion_config" in r for r in res["reasons"])


def test_ci_gate_disabled_skips_ci_check():
    """Com o gate desligado, IC baixo não reprova (mas o teste degradado é
    decisão explícita — o default de produção é True)."""
    cfg = dict(_FULL_CONFIG, ml_approval_test_auc_ci_excludes_half=False)
    res = evaluate_promotion_gate(
        _model_row(roc_auc_ci_low=0.40), promotion_config=cfg
    )
    assert res["status"] == APPROVED, res["reasons"]


def test_bootstrap_auc_ci_low_deterministic_and_bounded():
    rng = np.random.default_rng(0)
    n = 400
    y = (rng.random(n) < 0.4).astype(int)
    # preds correlacionados ao label (sinal real) → IC deve ficar > 0.5
    p = np.clip(y * 0.6 + rng.random(n) * 0.5, 0, 1)
    a = _bootstrap_auc_ci_low(y, p, 0.95, 500, 42)
    b = _bootstrap_auc_ci_low(y, p, 0.95, 500, 42)
    assert a == b  # determinístico (mesmo seed)
    assert 0.5 < a < 1.0


def test_bootstrap_auc_ci_low_none_on_single_class():
    y = np.ones(50, dtype=int)
    p = np.random.default_rng(1).random(50)
    assert _bootstrap_auc_ci_low(y, p, 0.95, 200, 42) is None
