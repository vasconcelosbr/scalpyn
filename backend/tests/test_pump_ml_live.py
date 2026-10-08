"""Pump ML live evaluation (v1.14): applied-model probabilities logged per closed candle,
scored against the SAME realised label as training (build_frame)."""
import json
from copy import deepcopy
from datetime import datetime, timezone

import numpy as np
import pytest

from app.services import pump_ml_candles as pc
from app.services import pump_ml_live as live
from app.services import pump_score_v1 as v1
from tests.test_pump_ml_candles import CFG, STEP, synthetic


def _preds(closes, prob, start=400, end=None):
    """One prediction per symbol per decision (close of candle t), probability from ``prob``."""
    frame = pc.build_frame(closes, CFG, horizons_candles=[3])
    end = end or len(frame["grid"])
    out = []
    for t in range(start, end):
        d = datetime.fromtimestamp(int(frame["grid"][t]) + STEP, timezone.utc)
        for i, s in enumerate(frame["syms"]):
            out.append({"decision_at": d, "symbol": s, "p_up": prob(frame, i, t)})
    return out, frame


def test_prediction_rows_are_one_per_symbol_at_the_candle_close():
    rows = live.prediction_rows({"experiment_id": "e1"}, "pump_relative_candle_v1", 15, 1_790_000_100,
                                {"B_USDT": 0.51, "A_USDT": 0.4999999, "C_USDT": None}, applied=True)
    assert [r["s"] for r in rows] == ["A_USDT", "B_USDT"]                      # abstained asset skipped
    assert rows[0]["d"] == datetime.fromtimestamp(1_790_000_100, timezone.utc).isoformat()
    assert rows[0] == {**rows[0], "e": "e1", "o": "pump_relative_candle_v1", "h": 15, "a": True}
    json.dumps(rows, allow_nan=False)
    assert live.prediction_rows({}, "o", 15, 0, {"A": 0.5}, applied=True) == []  # no experiment → nothing
    assert "ON CONFLICT" in live.INSERT_SQL and "UPDATE" not in live.INSERT_SQL  # append-only


def test_oracle_probabilities_score_high_and_noise_scores_half():
    closes = synthetic(n=1400)
    k = 3
    lab = pc.build_frame(closes, CFG, horizons_candles=[k])["labels"][(k, "path_mean")]
    oracle, frame = _preds(closes, lambda f, i, t: float(1 / (1 + np.exp(-np.nan_to_num(lab[i, t])))))
    m = live.evaluate(oracle, closes, CFG, horizon_minutes=15, quantile=0.1)
    assert m["horizon_candles"] == 3 and m["label_mode"] == CFG["label_mode"]
    assert m["pooled_auc"] > 0.95 and m["days_auc_above_half"] == m["days_scored"] >= 1
    assert m["economic"]["top_minus_bottom"] > 0
    rng = np.random.default_rng(3)
    noise, _ = _preds(closes, lambda f, i, t: float(rng.uniform(0.4, 0.6)))
    n = live.evaluate(noise, closes, CFG, horizon_minutes=15, quantile=0.1)
    assert abs(n["pooled_auc"] - 0.5) < 0.03
    assert n["mature"] + n["pending"] == n["predictions"]


def test_predictions_without_a_complete_horizon_stay_pending():
    closes = synthetic(n=600)
    preds, frame = _preds(closes, lambda f, i, t: 0.5, start=590)
    m = live.evaluate(preds, closes, CFG, horizon_minutes=15, quantile=0.1)
    # the last 3 decisions cannot have a 3-candle forward path yet
    last = {p["decision_at"] for p in preds}
    assert m["pending"] >= 3 * len(frame["syms"]) and len(last) == 10


def test_live_log_config_is_validated():
    assert v1.DEFAULT_V1["ml"]["live_log"] == {"enabled": True, "retention_days": 120}
    bad = deepcopy(v1.DEFAULT_V1); bad["ml"]["live_log"]["retention_days"] = 0
    errors = []
    v1.validate(bad, errors)
    assert any("live_log" in e for e in errors)
    ok = []
    v1.validate(deepcopy(v1.DEFAULT_V1), ok)
    assert not any("live_log" in e for e in ok)
