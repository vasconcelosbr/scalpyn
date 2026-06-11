"""Connectivity regression test — Parte 3.3.

For every key in autopilot_can_adjust (the allowlist), assert that a read point
exists in the pipeline_scan hot path. This test fails when authority is ilusória
(write without a corresponding read), preventing re-introduction of L-01/02/03.

Rules:
  - scoring_rules → pipeline_scan reads score_config from config_profiles(score)
                    and passes to _apply_robust_authoritative_scoring
  - minimum_score → pipeline_scan reads score_config.minimum_score as _wl_min_score gate
  - block_rules   → pipeline_scan loads block_config from config_profiles(block),
                    merges into profile_config → ProfileEngine.block_rules_config
  - entry_triggers → same block_config path → ProfileEngine.entry_triggers_config
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

PIPELINE_SCAN_PATH = pathlib.Path(__file__).parent.parent / "app" / "tasks" / "pipeline_scan.py"
PROFILE_ENGINE_PATH = pathlib.Path(__file__).parent.parent / "app" / "services" / "profile_engine.py"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class TestScoringRulesConnected:
    def test_score_config_loaded_from_config_profiles(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert 'get_config(db, "score"' in src or "get_config(db, 'score'" in src, (
            "pipeline_scan must load score_config from config_profiles via config_service"
        )

    def test_score_config_passed_to_scorer(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert "score_config=score_config" in src, (
            "score_config must be passed to the authoritative scoring function"
        )


class TestMinimumScoreConnected:
    def test_minimum_score_read_from_score_config(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert 'score_config or {}).get("minimum_score")' in src or \
               "score_config or {}).get('minimum_score')" in src, (
            "pipeline_scan must read minimum_score from score_config (config_profiles)"
        )

    def test_gate_uses_autopilot_min(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert "_autopilot_min" in src, (
            "_autopilot_min variable must exist as the primary minimum_score source"
        )


class TestBlockRulesConnected:
    def test_block_config_loaded_from_config_profiles(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert 'get_config(db, "block"' in src or "get_config(db, 'block'" in src, (
            "pipeline_scan must load block_config from config_profiles via config_service"
        )

    def test_block_rules_merged_into_profile_config(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert '_block_cfg.get("block_rules")' in src or "_block_cfg.get('block_rules')" in src, (
            "block_rules from block_config must be merged into profile_config"
        )

    def test_profile_engine_reads_block_rules_from_profile(self):
        src = _read(PROFILE_ENGINE_PATH)
        assert 'block_rules' in src, (
            "ProfileEngine must consume block_rules from the passed profile_config"
        )


class TestEntryTriggersConnected:
    def test_entry_triggers_merged_into_profile_config(self):
        src = _read(PIPELINE_SCAN_PATH)
        assert '_block_cfg.get("entry_triggers")' in src or "_block_cfg.get('entry_triggers')" in src, (
            "entry_triggers from block_config must be merged into profile_config"
        )

    def test_profile_engine_reads_entry_triggers(self):
        src = _read(PROFILE_ENGINE_PATH)
        assert 'entry_triggers' in src, (
            "ProfileEngine must consume entry_triggers from the passed profile_config"
        )


class TestAllowlistHasNoFiltersStub:
    def test_filters_not_in_default_can_adjust(self):
        from app.services.autopilot_engine import _GUARDRAILS_DEFAULTS
        can_adjust = _GUARDRAILS_DEFAULTS.get("autopilot_can_adjust", [])
        assert "filters" not in can_adjust, (
            "'filters' must not be in autopilot_can_adjust until the stub is implemented (L-07)"
        )

    def test_all_can_adjust_keys_have_read_path(self):
        from app.services.autopilot_engine import _GUARDRAILS_DEFAULTS
        can_adjust = _GUARDRAILS_DEFAULTS.get("autopilot_can_adjust", [])
        pipeline_src = _read(PIPELINE_SCAN_PATH)

        connected_keys = {
            "scoring_rules":  'get_config(db, "score"' in pipeline_src,
            "minimum_score":  "_autopilot_min" in pipeline_src,
            "block_rules":    '_block_cfg.get("block_rules")' in pipeline_src,
            "entry_triggers": '_block_cfg.get("entry_triggers")' in pipeline_src,
        }

        for key in can_adjust:
            assert connected_keys.get(key, False), (
                f"'{key}' is in autopilot_can_adjust but has no verified read path in pipeline_scan — "
                f"authority is ilusória (L-01/02/03 pattern)"
            )
