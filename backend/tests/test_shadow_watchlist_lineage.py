"""Tests for P0 Shadow Watchlist Lineage (migration 103).

Validates:
  LIN-1: WatchlistLineageContext dataclass defaults
  LIN-2: WatchlistLineageContext fields preserved through construction
  LIN-3: _INSERT_SHADOW_SQL includes all 7 lineage columns
  LIN-4: _INSERT_STRATEGY_LAB_SQL includes all 7 lineage columns
  LIN-5: create_shadows_for_new_decisions accepts watchlist kwargs
  LIN-6: create_l3_rejected_inline_shadows accepts watchlist kwargs
  LIN-7: create_l3_simulated_shadows accepts watchlist kwargs
  LIN-8: create_l1_spectrum_shadows accepts watchlist kwargs
  LIN-9: create_strategy_lab_shadows accepts watchlist kwargs
  LIN-10: create_strategy_lab_rejected_shadows accepts watchlist kwargs
  LIN-11: backfill preview returns correct structure
  LIN-12: lineage_confidence values are well-defined
  LIN-13: _wl_to_dict includes profile_name and source_watchlist_level
"""

import inspect
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.schemas.watchlist_lineage_context import WatchlistLineageContext


# ---------------------------------------------------------------------------
# LIN-1: WatchlistLineageContext defaults
# ---------------------------------------------------------------------------

class TestWatchlistLineageContextDefaults:
    def test_default_lineage_confidence(self):
        ctx = WatchlistLineageContext()
        assert ctx.lineage_confidence == "EXACT"

    def test_default_lineage_source(self):
        ctx = WatchlistLineageContext()
        assert ctx.lineage_source == "pipeline_scan"

    def test_default_resolved_at_is_datetime(self):
        ctx = WatchlistLineageContext()
        assert isinstance(ctx.lineage_resolved_at, datetime)
        assert ctx.lineage_resolved_at.tzinfo is not None

    def test_nullable_fields_default_to_none(self):
        ctx = WatchlistLineageContext()
        assert ctx.watchlist_id is None
        assert ctx.watchlist_name is None
        assert ctx.watchlist_level is None
        assert ctx.source_watchlist_id is None
        assert ctx.profile_id is None
        assert ctx.profile_name is None
        assert ctx.profile_version is None


# ---------------------------------------------------------------------------
# LIN-2: WatchlistLineageContext fields preserved
# ---------------------------------------------------------------------------

class TestWatchlistLineageContextFields:
    def test_all_fields_stored(self):
        _ts = datetime.now(timezone.utc)
        ctx = WatchlistLineageContext(
            watchlist_id="wl-uuid-1234",
            watchlist_name="My L3 WL",
            watchlist_level="L3",
            source_watchlist_id="swl-uuid-5678",
            profile_id="prof-uuid-9999",
            profile_name="My Profile",
            profile_version=_ts,
            lineage_confidence="EXACT",
            lineage_source="pipeline_scan",
            lineage_resolved_at=_ts,
        )
        assert ctx.watchlist_id == "wl-uuid-1234"
        assert ctx.watchlist_name == "My L3 WL"
        assert ctx.watchlist_level == "L3"
        assert ctx.source_watchlist_id == "swl-uuid-5678"
        assert ctx.profile_id == "prof-uuid-9999"
        assert ctx.profile_name == "My Profile"
        assert ctx.profile_version == _ts
        assert ctx.lineage_confidence == "EXACT"
        assert ctx.lineage_source == "pipeline_scan"
        assert ctx.lineage_resolved_at == _ts

    def test_backfill_confidence_values_valid(self):
        valid_values = {
            "EXACT", "JOIN_PROFILE_UNIQUE", "AMBIGUOUS_PROFILE",
            "UNRESOLVED", "LEGACY_UNKNOWN",
        }
        for v in valid_values:
            ctx = WatchlistLineageContext(lineage_confidence=v)
            assert ctx.lineage_confidence == v


# ---------------------------------------------------------------------------
# LIN-3: _INSERT_SHADOW_SQL includes lineage columns
# ---------------------------------------------------------------------------

class TestInsertShadowSQL:
    def test_insert_shadow_sql_has_lineage_columns(self):
        from backend.app.services.shadow_trade_service import _INSERT_SHADOW_SQL
        sql_str = str(_INSERT_SHADOW_SQL)
        assert "watchlist_id" in sql_str
        assert "watchlist_name" in sql_str
        assert "watchlist_level" in sql_str
        assert "source_watchlist_id" in sql_str
        assert "lineage_confidence" in sql_str
        assert "lineage_source" in sql_str
        assert "lineage_resolved_at" in sql_str

    def test_insert_shadow_sql_has_lineage_params(self):
        from backend.app.services.shadow_trade_service import _INSERT_SHADOW_SQL
        sql_str = str(_INSERT_SHADOW_SQL)
        assert ":watchlist_id" in sql_str
        assert ":watchlist_name" in sql_str
        assert ":watchlist_level" in sql_str
        assert ":source_watchlist_id" in sql_str
        assert ":lineage_confidence" in sql_str
        assert ":lineage_source" in sql_str
        assert ":lineage_resolved_at" in sql_str


# ---------------------------------------------------------------------------
# LIN-4: _INSERT_STRATEGY_LAB_SQL includes lineage columns
# ---------------------------------------------------------------------------

class TestInsertStrategyLabSQL:
    def test_insert_strategy_lab_sql_has_lineage_columns(self):
        from backend.app.services.shadow_trade_service import _INSERT_STRATEGY_LAB_SQL
        sql_str = str(_INSERT_STRATEGY_LAB_SQL)
        assert "watchlist_id" in sql_str
        assert "watchlist_name" in sql_str
        assert "watchlist_level" in sql_str
        assert "source_watchlist_id" in sql_str
        assert "lineage_confidence" in sql_str
        assert "lineage_source" in sql_str
        assert "lineage_resolved_at" in sql_str


# ---------------------------------------------------------------------------
# LIN-5 to LIN-10: function signatures accept watchlist kwargs
# ---------------------------------------------------------------------------

class TestFunctionSignatures:
    def _get_params(self, func) -> set:
        return set(inspect.signature(func).parameters.keys())

    def test_create_shadows_for_new_decisions_accepts_lineage(self):
        from backend.app.services.shadow_trade_service import create_shadows_for_new_decisions
        params = self._get_params(create_shadows_for_new_decisions)
        assert "watchlist_id" in params
        assert "watchlist_name" in params
        assert "watchlist_level" in params
        assert "source_watchlist_id" in params
        assert "profile_id" in params
        assert "profile_name" in params
        assert "profile_version" in params

    def test_l3_rejected_threads_profile_lineage_to_context(self):
        from backend.app.services.shadow_trade_service import create_l3_rejected_inline_shadows
        source = inspect.getsource(create_l3_rejected_inline_shadows)
        assert "profile_id=str(profile_id)" in source
        assert "profile_name=profile_name" in source
        assert "profile_version=profile_version" in source

    def test_pipeline_scan_passes_profile_lineage_to_l3_rejected(self):
        source = Path("backend/app/tasks/pipeline_scan.py").read_text(encoding="utf-8")
        call_start = source.index("await create_l3_rejected_inline_shadows(")
        call_end = source.index("\n                            )", call_start)
        call = source[call_start:call_end]
        assert "profile_id=str(wl.profile_id)" in call
        assert "profile_name=_wl_profile_name" in call
        assert "profile_version=_wl_profile_version" in call

    def test_create_l3_rejected_accepts_lineage(self):
        from backend.app.services.shadow_trade_service import create_l3_rejected_inline_shadows
        params = self._get_params(create_l3_rejected_inline_shadows)
        assert "watchlist_id" in params
        assert "watchlist_name" in params
        assert "watchlist_level" in params
        assert "source_watchlist_id" in params
        assert "profile_id" in params
        assert "profile_name" in params
        assert "profile_version" in params

    def test_pipeline_scan_passes_profile_lineage_to_l1_spectrum(self):
        source = Path("backend/app/tasks/pipeline_scan.py").read_text(encoding="utf-8")
        call_start = source.index("await create_l1_spectrum_shadows(")
        call_end = source.index("\n                                )", call_start)
        call = source[call_start:call_end]
        assert 'profile_id=str(wl.profile_id)' in call
        assert 'profile_name=_l1_profile_meta.get("name")' in call
        assert 'profile_version=_l1_profile_meta.get("version")' in call

    def test_on_demand_l3_decisions_persist_profile_lineage(self):
        source = Path("backend/app/api/watchlists.py").read_text(encoding="utf-8")
        block_start = source.index("_dl_rows.append(_DecisionLog(")
        block_end = source.index("\n                ))", block_start)
        block = source[block_start:block_end]
        assert "profile_id=wl.profile_id" in block
        assert "profile_name=profile_name" in block
        assert "profile_version=profile_version" in block

        metrics_start = source.index("_metrics: dict = {", block_start - 1500)
        metrics_block = source[metrics_start:block_start]
        assert '"watchlist_id": str(wl.id)' in metrics_block

    def test_create_l3_simulated_accepts_lineage(self):
        from backend.app.services.shadow_trade_service import create_l3_simulated_shadows
        params = self._get_params(create_l3_simulated_shadows)
        assert "watchlist_id" in params
        assert "watchlist_name" in params
        assert "watchlist_level" in params
        assert "source_watchlist_id" in params

    def test_create_l1_spectrum_accepts_lineage(self):
        from backend.app.services.shadow_trade_service import create_l1_spectrum_shadows
        params = self._get_params(create_l1_spectrum_shadows)
        assert "watchlist_id" in params
        assert "watchlist_name" in params
        assert "watchlist_level" in params
        assert "source_watchlist_id" in params
        assert "profile_id" in params
        assert "profile_name" in params
        assert "profile_version" in params

    def test_create_strategy_lab_shadows_accepts_lineage(self):
        from backend.app.services.shadow_trade_service import create_strategy_lab_shadows
        params = self._get_params(create_strategy_lab_shadows)
        assert "watchlist_id" in params
        assert "watchlist_name" in params
        assert "watchlist_level" in params
        assert "source_watchlist_id" in params

    def test_create_strategy_lab_rejected_shadows_accepts_lineage(self):
        from backend.app.services.shadow_trade_service import create_strategy_lab_rejected_shadows
        params = self._get_params(create_strategy_lab_rejected_shadows)
        assert "watchlist_id" in params
        assert "watchlist_name" in params
        assert "watchlist_level" in params
        assert "source_watchlist_id" in params


# ---------------------------------------------------------------------------
# LIN-11: backfill preview structure
# ---------------------------------------------------------------------------

class TestLineageBackfillPreview:
    def test_preview_returns_expected_keys(self):
        import asyncio
        from backend.app.services.shadow_lineage_backfill import preview_lineage_backfill
        from unittest.mock import AsyncMock, MagicMock

        mock_row = MagicMock()
        mock_row.l3_lab_resolvable = 5
        mock_row.l3_legacy_unknown = 10
        mock_row.l3_with_profile = 2
        mock_row.total_unresolved = 17

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = asyncio.run(preview_lineage_backfill(mock_db))

        assert "dry_run" in result
        assert result["dry_run"] is True
        assert "total_unresolved" in result
        assert result["total_unresolved"] == 17
        assert "l3_lab_resolvable" in result
        assert result["l3_lab_resolvable"] == 5
        assert "l3_legacy_unknown" in result
        assert result["l3_legacy_unknown"] == 10
        assert "updated_l3_lab" in result
        assert result["updated_l3_lab"] == 0
        assert "errors" in result


# ---------------------------------------------------------------------------
# LIN-12: lineage_confidence valid enum values
# ---------------------------------------------------------------------------

class TestLineageConfidenceValues:
    def test_pipeline_scan_uses_exact(self):
        """New trades created by pipeline_scan must use EXACT confidence."""
        from backend.app.services.shadow_trade_service import create_shadows_for_new_decisions
        source = inspect.getsource(create_shadows_for_new_decisions)
        assert '"EXACT"' in source or "'EXACT'" in source

    def test_backfill_uses_join_profile_unique(self):
        """Backfill of L3_LAB must use JOIN_PROFILE_UNIQUE."""
        from backend.app.services.shadow_lineage_backfill import _run_backfill
        source = inspect.getsource(_run_backfill)
        assert "JOIN_PROFILE_UNIQUE" in source

    def test_backfill_marks_legacy_unknown(self):
        """Backfill of canonical L3 must use LEGACY_UNKNOWN."""
        from backend.app.services.shadow_lineage_backfill import _run_backfill
        source = inspect.getsource(_run_backfill)
        assert "LEGACY_UNKNOWN" in source


# ---------------------------------------------------------------------------
# LIN-13: _wl_to_dict includes profile_name and source_watchlist_level
# ---------------------------------------------------------------------------

class TestWlToDictSerializer:
    def test_wl_to_dict_accepts_profile_name(self):
        from backend.app.api.watchlists import _wl_to_dict
        params = set(inspect.signature(_wl_to_dict).parameters.keys())
        assert "profile_name" in params

    def test_wl_to_dict_accepts_source_watchlist_level(self):
        from backend.app.api.watchlists import _wl_to_dict
        params = set(inspect.signature(_wl_to_dict).parameters.keys())
        assert "source_watchlist_level" in params

    def test_wl_to_dict_includes_profile_name_in_output(self):
        from backend.app.api.watchlists import _wl_to_dict
        import uuid
        wl = MagicMock()
        wl.id = uuid.uuid4()
        wl.name = "Test WL"
        wl.level = "L3"
        wl.market_mode = "spot"
        wl.source_pool_id = None
        wl.source_watchlist_id = None
        wl.profile_id = None
        wl.auto_refresh = True
        wl.filters_json = {}
        wl.last_scanned_at = None
        wl.created_at = datetime.now(timezone.utc)
        wl.updated_at = datetime.now(timezone.utc)

        result = _wl_to_dict(wl, profile_name="My Profile", source_watchlist_level="L2")
        assert result["profile_name"] == "My Profile"
        assert result["source_watchlist_level"] == "L2"

    def test_wl_to_dict_profile_name_none_by_default(self):
        from backend.app.api.watchlists import _wl_to_dict
        import uuid
        wl = MagicMock()
        wl.id = uuid.uuid4()
        wl.name = "Test WL"
        wl.level = "L3"
        wl.market_mode = "spot"
        wl.source_pool_id = None
        wl.source_watchlist_id = None
        wl.profile_id = None
        wl.auto_refresh = True
        wl.filters_json = {}
        wl.last_scanned_at = None
        wl.created_at = datetime.now(timezone.utc)
        wl.updated_at = datetime.now(timezone.utc)

        result = _wl_to_dict(wl)
        assert result["profile_name"] is None
        assert result["source_watchlist_level"] is None
