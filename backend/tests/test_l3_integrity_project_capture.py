import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ml.l3_integrity import project_capture


# ── 2026-09-25: bb_upper_distance_pct / bb_middle_distance_pct capture gap ──
#
# These two fields are computed live (price_position.py) and used live by
# profile filters/signals (e.g. PUMP3's "price near upper Bollinger band,
# middle band as support" condition), but were absent from
# feature_extractor.FEATURE_COLUMNS — project_capture()'s `allowed` whitelist
# is built from FEATURE_COLUMNS, so it silently dropped both fields from the
# persisted shadow_trades.features_snapshot even though the live decision
# read a real value for each. The trade itself evaluated correctly; only the
# post-hoc record (features_snapshot, indicator_analysis.entry_metrics in
# the shadow-trade export) was missing them. Fixed by appending both to
# FEATURE_COLUMNS.


def test_bb_upper_and_middle_distance_pct_survive_project_capture():
    snapshot = {
        "close": 0.22437,
        "bb_upper_distance_pct": -0.42,
        "bb_middle_distance_pct": 4.40,
        "rsi": 68.0,
    }
    source_snapshot = {
        "bb_upper_distance_pct": {"ts": "2026-09-25T10:00:00Z", "value": -0.42},
        "bb_middle_distance_pct": {"ts": "2026-09-25T10:00:00Z", "value": 4.40},
        "close": {"ts": "2026-09-25T10:00:00Z", "value": 0.22437},
        "rsi": {"ts": "2026-09-25T10:00:00Z", "value": 68.0},
    }

    projected, meta = project_capture(snapshot, source_snapshot, config={})

    assert projected["bb_upper_distance_pct"] == -0.42
    assert projected["bb_middle_distance_pct"] == 4.40
    assert "bb_upper_distance_pct" not in meta["excluded_observational_fields"]
    assert "bb_middle_distance_pct" not in meta["excluded_observational_fields"]
    # Neither is in the score-component neutralization set, so a missing
    # timestamp (if it ever happens) must never null them out the way
    # liquidity_score/momentum_score/etc. can be neutralized.
    assert "bb_upper_distance_pct" not in meta["optional_without_provenance"]
    assert "bb_middle_distance_pct" not in meta["optional_without_provenance"]


def test_bb_distance_fields_are_in_feature_columns():
    from app.ml.feature_extractor import FEATURE_COLUMNS

    assert "bb_upper_distance_pct" in FEATURE_COLUMNS
    assert "bb_middle_distance_pct" in FEATURE_COLUMNS


def test_bb_distance_fields_appended_after_existing_schema_prefix():
    """Regression guard for prediction_service.py's model-compatibility check
    (FEATURE_COLUMNS[:len(model_feature_names)] == model.feature_names_in_):
    the two new fields must be appended at the END of FEATURE_COLUMNS, never
    inserted earlier, so an already-trained model's expected prefix is
    unaffected by this change."""
    from app.ml.feature_extractor import FEATURE_COLUMNS

    assert FEATURE_COLUMNS[-2:] == ["bb_upper_distance_pct", "bb_middle_distance_pct"]
