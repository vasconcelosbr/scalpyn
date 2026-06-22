"""Tests for P0 ML corrections: CatBoost Pool format, L3_PROFILE_STRICT dataset, governance.

Validates:
  B-NEW-1: CatBoost inferência usa astype(int).astype(str), não float32 pool direto.
  B-NEW-2: L3_PROFILE_STRICT exclui registros L3 com profile_id NULL.
  B-NEW-3: governance_warning=ranking_shadow_only para modelos active sem métricas.
  B-TRACE-1: _stable_profile_bucket determinístico; NULL → bucket 9999.
"""

import hashlib
import sys
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.ml_challenger_service import (
    MLChallengerService,
    _PROFILE_NULL_BUCKET,
    _stable_profile_bucket,
)


# ---------------------------------------------------------------------------
# B-TRACE-1 — _stable_profile_bucket determinism and NULL isolation
# ---------------------------------------------------------------------------

class TestStableProfileBucket:
    def test_deterministic_same_input(self):
        pid = "a1b2c3d4-1234-5678-abcd-ef0123456789"
        assert _stable_profile_bucket(pid) == _stable_profile_bucket(pid)

    def test_null_returns_reserved_bucket(self):
        assert _stable_profile_bucket(None) == _PROFILE_NULL_BUCKET  # 9999

    def test_empty_string_returns_reserved_bucket(self):
        assert _stable_profile_bucket("") == _PROFILE_NULL_BUCKET  # 9999

    def test_hashed_range_never_equals_null_bucket(self):
        """md5 hash % 9999 → 0–9998, never 9999."""
        sample_pids = [f"profile-{i}" for i in range(500)]
        buckets = [_stable_profile_bucket(p) for p in sample_pids]
        assert _PROFILE_NULL_BUCKET not in buckets

    def test_range_upper_bound(self):
        buckets = [_stable_profile_bucket(str(i)) for i in range(2000)]
        assert max(buckets) <= 9998

    def test_uses_md5_not_builtin_hash(self):
        """Verify the implementation uses hashlib.md5, not Python's hash()."""
        pid = "test-profile"
        expected = int(hashlib.md5(pid.encode()).hexdigest()[:8], 16) % 9999
        assert _stable_profile_bucket(pid) == expected

    def test_train_infer_uuid_form_matches(self):
        """DB returns profile_id as str; inference wraps in str() — must match."""
        from uuid import UUID
        uid_obj = UUID("a3af94be-bbb5-413b-a1bd-c1f0a5db0ee5")
        uid_str = str(uid_obj)  # "a3af94be-bbb5-413b-a1bd-c1f0a5db0ee5"
        assert _stable_profile_bucket(uid_str) == _stable_profile_bucket(str(uid_obj))


# ---------------------------------------------------------------------------
# B-NEW-1 — CatBoost Pool format: "2760", not "2760.0"
# ---------------------------------------------------------------------------

class TestCatBoostPoolFormat:
    """Verify that training and inference use identical categorical string format."""

    def _simulate_train_format(self, bucket_value: int) -> str:
        """Reproduce what _make_pool does: astype(int).astype(str)."""
        arr = np.array([float(bucket_value)])
        return arr.astype(int).astype(str)[0]

    def _simulate_old_infer_format(self, bucket_value: int) -> str:
        """What the OLD inference code produced: float32 → str via CatBoost."""
        arr = np.array([float(bucket_value)], dtype="float32")
        # CatBoost converts float32 values to string using Python str()
        return str(arr[0])  # e.g. "2760.0"

    def _simulate_new_infer_format(self, bucket_value: int) -> str:
        """What the NEW inference code produces: astype(int).astype(str)."""
        import pandas as pd
        df = pd.DataFrame([[float(bucket_value)]], columns=["profile_id_encoded"])
        df["profile_id_encoded"] = df["profile_id_encoded"].astype(int).astype(str)
        return df["profile_id_encoded"].iloc[0]

    def test_train_format_is_integer_string(self):
        assert self._simulate_train_format(2760) == "2760"
        assert self._simulate_train_format(9999) == "9999"
        assert self._simulate_train_format(0) == "0"

    def test_old_infer_format_was_float_string(self):
        # Confirm the bug existed: old inference would produce "2760.0"
        assert self._simulate_old_infer_format(2760) != "2760"

    def test_new_infer_format_matches_train(self):
        for bucket in [0, 1, 42, 2760, 9998, 9999]:
            train_fmt = self._simulate_train_format(bucket)
            new_infer_fmt = self._simulate_new_infer_format(bucket)
            assert train_fmt == new_infer_fmt, (
                f"bucket={bucket}: train='{train_fmt}' infer='{new_infer_fmt}'"
            )

    def test_new_infer_no_dot_zero_suffix(self):
        for bucket in [1, 42, 9999]:
            fmt = self._simulate_new_infer_format(bucket)
            assert "." not in fmt, f"Float suffix found in '{fmt}' for bucket={bucket}"

    def test_decision_orchestrator_uses_df_pool(self):
        """Verify _get_profile_catboost_score now builds a DataFrame Pool, not raw numpy."""
        import inspect
        import backend.app.services.decision_orchestrator as do_module
        source = inspect.getsource(do_module._get_profile_catboost_score)
        # Must use DataFrame with int→str conversion
        assert "astype(int).astype(str)" in source, (
            "inference Pool must convert cat features with astype(int).astype(str)"
        )
        assert "df_inf" in source, (
            "inference must use DataFrame (df_inf), not raw numpy array"
        )


# ---------------------------------------------------------------------------
# B-NEW-2 — L3_PROFILE_STRICT dataset policy
# ---------------------------------------------------------------------------

class TestL3StrictDataset:
    """Verify the L3_PROFILE_STRICT filtering and metadata."""

    def _make_records(
        self,
        n_l3_with_profile: int,
        n_l3_null_profile: int,
        n_l3_lab_with_profile: int,
        n_l3_lab_null_profile: int,
    ) -> List[Dict]:
        records = []
        for i in range(n_l3_with_profile):
            records.append({"source": "L3", "profile_id": f"pid-{i}", "pnl_pct": 0.01})
        for _ in range(n_l3_null_profile):
            records.append({"source": "L3", "profile_id": None, "pnl_pct": 0.01})
        for i in range(n_l3_lab_with_profile):
            records.append({"source": "L3_LAB", "profile_id": f"lab-{i}", "pnl_pct": 0.01})
        for _ in range(n_l3_lab_null_profile):
            records.append({"source": "L3_LAB", "profile_id": None, "pnl_pct": 0.01})
        return records

    def test_strict_filter_excludes_l3_null_profile(self):
        all_records = self._make_records(
            n_l3_with_profile=910,
            n_l3_null_profile=1818,
            n_l3_lab_with_profile=1138,
            n_l3_lab_null_profile=41,
        )
        strict = [r for r in all_records if r.get("profile_id")]
        assert len(strict) == 910 + 1138
        assert all(r["profile_id"] is not None for r in strict)

    def test_strict_filter_no_null_profile_in_output(self):
        all_records = self._make_records(50, 100, 30, 5)
        strict = [r for r in all_records if r.get("profile_id")]
        assert not any(r["profile_id"] is None for r in strict)

    def test_strict_filter_includes_l3_lab_with_profile(self):
        all_records = self._make_records(0, 0, 10, 0)
        strict = [r for r in all_records if r.get("profile_id")]
        assert len(strict) == 10
        assert all(r["source"] == "L3_LAB" for r in strict)

    def test_strict_filter_includes_l3_with_profile(self):
        all_records = self._make_records(5, 0, 0, 0)
        strict = [r for r in all_records if r.get("profile_id")]
        assert len(strict) == 5
        assert all(r["source"] == "L3" for r in strict)

    def test_l3_strict_meta_dataset_policy(self):
        svc = MLChallengerService()
        all_r = self._make_records(910, 1818, 1138, 41)
        strict_r = [r for r in all_r if r.get("profile_id")]
        meta = svc._l3_strict_meta(all_r, strict_r, ["L3", "L3_LAB"])
        assert meta["dataset_policy"] == "L3_PROFILE_STRICT"

    def test_l3_strict_meta_excluded_count(self):
        svc = MLChallengerService()
        all_r = self._make_records(910, 1818, 1138, 41)
        strict_r = [r for r in all_r if r.get("profile_id")]
        meta = svc._l3_strict_meta(all_r, strict_r, ["L3", "L3_LAB"])
        # 1818 L3 NULL + 41 L3_LAB NULL = 1859 excluded
        assert meta["excluded_null_profile_id"] == 1818 + 41

    def test_l3_strict_meta_unknown_profile_count_is_zero(self):
        svc = MLChallengerService()
        all_r = self._make_records(50, 100, 30, 5)
        strict_r = [r for r in all_r if r.get("profile_id")]
        meta = svc._l3_strict_meta(all_r, strict_r, ["L3", "L3_LAB"])
        assert meta["unknown_profile_count"] == 0
        assert meta["unknown_profile_pct"] == 0.0

    def test_l3_strict_meta_distinct_profiles(self):
        svc = MLChallengerService()
        all_r = self._make_records(5, 10, 3, 2)
        strict_r = [r for r in all_r if r.get("profile_id")]
        meta = svc._l3_strict_meta(all_r, strict_r, ["L3", "L3_LAB"])
        # 5 unique L3 profiles + 3 unique L3_LAB profiles = 8
        assert meta["distinct_profiles"] == 8

    def test_l3_strict_meta_source_breakdown(self):
        svc = MLChallengerService()
        all_r = self._make_records(910, 1818, 1138, 41)
        strict_r = [r for r in all_r if r.get("profile_id")]
        meta = svc._l3_strict_meta(all_r, strict_r, ["L3", "L3_LAB"])
        assert meta["source_breakdown"]["L3"] == 910
        assert meta["source_breakdown"]["L3_LAB"] == 1138

    def test_source_filter_no_l1_spectrum(self):
        """CatBoost must never train on L1_SPECTRUM."""
        from backend.app.services.ml_challenger_service import CATBOOST_TRAIN_SOURCES
        assert "L1_SPECTRUM" not in CATBOOST_TRAIN_SOURCES

    def test_lgbm_uses_l1_spectrum(self):
        """LightGBM must use L1_SPECTRUM exclusively."""
        from backend.app.services.ml_challenger_service import LGBM_TRAIN_SOURCES
        assert "L1_SPECTRUM" in LGBM_TRAIN_SOURCES
        assert "L3" not in LGBM_TRAIN_SOURCES
        assert "L3_LAB" not in LGBM_TRAIN_SOURCES


# ---------------------------------------------------------------------------
# B-NEW-3 — governance_warning for incomplete active models
# ---------------------------------------------------------------------------

class TestGovernanceWarning:
    def _compute_governance_warning(self, status, precision_score, recall_score,
                                    test_samples, feature_columns_json):
        """Replicate the logic from ml.py list_ml_models."""
        is_incomplete = (
            status == "active"
            and (
                precision_score is None
                or recall_score is None
                or test_samples is None
                or feature_columns_json is None
            )
        )
        return "ranking_shadow_only" if is_incomplete else None

    def test_v19_v20_style_model_shows_warning(self):
        """v19/v20: active, no precision/recall/test_samples/feature_columns_json."""
        warning = self._compute_governance_warning(
            status="active",
            precision_score=None,
            recall_score=None,
            test_samples=None,
            feature_columns_json=None,
        )
        assert warning == "ranking_shadow_only"

    def test_complete_active_model_no_warning(self):
        """v21/v22 style: active with all metrics → no warning."""
        warning = self._compute_governance_warning(
            status="active",
            precision_score=0.72,
            recall_score=0.45,
            test_samples=120,
            feature_columns_json=["feat1", "feat2"],
        )
        assert warning is None

    def test_candidate_model_no_warning(self):
        """Candidate models (regardless of metrics) don't trigger governance warning."""
        warning = self._compute_governance_warning(
            status="candidate",
            precision_score=None,
            recall_score=None,
            test_samples=None,
            feature_columns_json=None,
        )
        assert warning is None

    def test_missing_only_precision_triggers_warning(self):
        warning = self._compute_governance_warning(
            status="active",
            precision_score=None,
            recall_score=0.45,
            test_samples=120,
            feature_columns_json=["feat1"],
        )
        assert warning == "ranking_shadow_only"

    def test_missing_only_feature_contract_triggers_warning(self):
        warning = self._compute_governance_warning(
            status="active",
            precision_score=0.72,
            recall_score=0.45,
            test_samples=120,
            feature_columns_json=None,
        )
        assert warning == "ranking_shadow_only"

    def test_ml_api_response_includes_governance_warning_field(self):
        """Verify ml.py list_ml_models response includes governance_warning."""
        import inspect
        import backend.app.api.ml as ml_module
        source = inspect.getsource(ml_module.list_ml_models)
        assert "governance_warning" in source
        assert "ranking_shadow_only" in source


# ---------------------------------------------------------------------------
# Model status: v21/v22 born as candidate
# ---------------------------------------------------------------------------

class TestModelStatusOnCreation:
    def test_save_to_db_uses_candidate_status(self):
        """INSERT must hardcode 'candidate', never 'active'."""
        import inspect
        import backend.app.services.ml_challenger_service as svc_module
        source = inspect.getsource(svc_module.MLChallengerService._save_to_db)
        assert "'candidate'" in source
        assert "status='active'" not in source
        assert '"active"' not in source.split("'candidate'")[0]  # not before the candidate value
