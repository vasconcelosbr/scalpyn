"""Tests for Fase 8 — ML lineage written into shadow_trades at creation time
(Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24).

Before this change, shadow_trades.ml_model_id / ml_probability /
final_priority_score were always NULL at INSERT time — they were only ever
populated later by a separate, manually-triggered POST
/api/ml/orchestrator/backfill call. This violated the absolute rule that
every ML score used in a decision or Shadow must have complete lineage from
the moment it exists.

Validates:
  ML-1: WatchlistLineageContext ML fields default to None
  ML-2: WatchlistLineageContext ML fields preserved through construction
  ML-3: _INSERT_SHADOW_SQL includes the 5 ML lineage columns + bind params
  ML-4: create_shadows_for_new_decisions accepts ml_scores_by_symbol
  ML-5: create_shadows_for_new_decisions builds per-symbol lineage via
        dataclasses.replace when ml_scores_by_symbol is provided
  ML-6: pipeline_scan.py threads _ml_gate_scores into create_shadows_for_new_decisions
        and stamps model_lane="L3_PROFILE" on every L3 gate score
  ML-7: migration 106 adds model_lane + ranking_id (FK to ml_opportunity_rankings)
"""

import inspect
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.schemas.watchlist_lineage_context import WatchlistLineageContext


# ---------------------------------------------------------------------------
# ML-1 / ML-2: WatchlistLineageContext ML fields
# ---------------------------------------------------------------------------

class TestWatchlistLineageContextMlFields:
    def test_ml_fields_default_to_none(self):
        ctx = WatchlistLineageContext()
        assert ctx.ml_model_id is None
        assert ctx.ml_probability is None
        assert ctx.final_priority_score is None
        assert ctx.model_lane is None
        assert ctx.ranking_id is None

    def test_ml_fields_preserved_through_construction(self):
        ctx = WatchlistLineageContext(
            ml_model_id="model-uuid-1",
            ml_probability=0.71,
            final_priority_score=0.65,
            model_lane="L3_PROFILE",
            ranking_id="ranking-uuid-1",
        )
        assert ctx.ml_model_id == "model-uuid-1"
        assert ctx.ml_probability == 0.71
        assert ctx.final_priority_score == 0.65
        assert ctx.model_lane == "L3_PROFILE"
        assert ctx.ranking_id == "ranking-uuid-1"

    def test_dataclasses_replace_overrides_only_ml_fields(self):
        import dataclasses
        base = WatchlistLineageContext(
            watchlist_id="wl-1", watchlist_name="WL", watchlist_level="L3",
        )
        derived = dataclasses.replace(
            base, ml_model_id="m-1", ml_probability=0.8, model_lane="L3_PROFILE",
        )
        # watchlist fields untouched
        assert derived.watchlist_id == "wl-1"
        assert derived.watchlist_name == "WL"
        assert derived.watchlist_level == "L3"
        # ml fields applied
        assert derived.ml_model_id == "m-1"
        assert derived.ml_probability == 0.8
        assert derived.model_lane == "L3_PROFILE"


# ---------------------------------------------------------------------------
# ML-3: _INSERT_SHADOW_SQL includes ML lineage columns
# ---------------------------------------------------------------------------

class TestInsertShadowSqlMlLineage:
    def test_insert_shadow_sql_has_ml_lineage_columns(self):
        from backend.app.services.shadow_trade_service import _INSERT_SHADOW_SQL
        sql_str = str(_INSERT_SHADOW_SQL)
        assert "ml_model_id" in sql_str
        assert "ml_probability" in sql_str
        assert "final_priority_score" in sql_str
        assert "model_lane" in sql_str
        assert "ranking_id" in sql_str

    def test_insert_shadow_sql_has_ml_lineage_params(self):
        from backend.app.services.shadow_trade_service import _INSERT_SHADOW_SQL
        sql_str = str(_INSERT_SHADOW_SQL)
        assert ":ml_model_id" in sql_str
        assert ":ml_probability" in sql_str
        assert ":final_priority_score" in sql_str
        assert ":model_lane" in sql_str
        assert ":ranking_id" in sql_str

    def test_create_from_decision_source_reads_ml_fields_from_lineage(self):
        from backend.app.services.shadow_trade_service import _create_from_decision
        source = inspect.getsource(_create_from_decision)
        assert '"ml_model_id"' in source
        assert '"ml_probability"' in source
        assert '"final_priority_score"' in source
        assert '"model_lane"' in source
        assert '"ranking_id"' in source
        # Must never fabricate a score when lineage is absent.
        assert "if lineage else None" in source


# ---------------------------------------------------------------------------
# ML-4 / ML-5: create_shadows_for_new_decisions threads ml_scores_by_symbol
# ---------------------------------------------------------------------------

class TestCreateShadowsForNewDecisionsMlScores:
    def test_accepts_ml_scores_by_symbol_param(self):
        from backend.app.services.shadow_trade_service import create_shadows_for_new_decisions
        params = set(inspect.signature(create_shadows_for_new_decisions).parameters.keys())
        assert "ml_scores_by_symbol" in params

    def test_default_is_none_backward_compatible(self):
        from backend.app.services.shadow_trade_service import create_shadows_for_new_decisions
        sig = inspect.signature(create_shadows_for_new_decisions)
        assert sig.parameters["ml_scores_by_symbol"].default is None

    def test_source_builds_per_symbol_lineage_via_replace(self):
        from backend.app.services.shadow_trade_service import create_shadows_for_new_decisions
        source = inspect.getsource(create_shadows_for_new_decisions)
        assert "ml_scores_by_symbol" in source
        assert "dataclasses" in source
        assert "_dc.replace(" in source or "dataclasses.replace(" in source

    def test_source_never_fabricates_lineage_when_no_ml_score(self):
        """When ml_scores_by_symbol has no entry for the symbol, the original
        watchlist-only lineage (possibly None) must be used unchanged —
        never invented."""
        from backend.app.services.shadow_trade_service import create_shadows_for_new_decisions
        source = inspect.getsource(create_shadows_for_new_decisions)
        assert "_decision_lineage = _lineage" in source


# ---------------------------------------------------------------------------
# ML-6: pipeline_scan.py wiring
# ---------------------------------------------------------------------------

class TestPipelineScanMlLineageWiring:
    def _pipeline_scan_source(self) -> str:
        path = Path(__file__).resolve().parents[2] / "backend" / "app" / "tasks" / "pipeline_scan.py"
        return path.read_text(encoding="utf-8")

    def test_ml_gate_scores_stamped_with_model_lane(self):
        source = self._pipeline_scan_source()
        assert '"model_lane": "L3_PROFILE"' in source

    def test_create_shadows_call_passes_ml_scores_by_symbol(self):
        source = self._pipeline_scan_source()
        assert "ml_scores_by_symbol=_ml_gate_scores" in source


# ---------------------------------------------------------------------------
# ML-7: migration 106 schema
# ---------------------------------------------------------------------------

class TestMigration106:
    def _migration_source(self) -> str:
        path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "alembic" / "versions" / "legacy" / "106_shadow_ml_lineage.py"  # R2 2026-07-05: movida para legacy/ (baseline 000)
        )
        return path.read_text(encoding="utf-8")

    def test_revision_chain(self):
        source = self._migration_source()
        assert 'revision = "106_shadow_ml_lineage"' in source
        assert 'down_revision = "105_ml_opp_rankings"' in source

    def test_adds_model_lane_and_ranking_id(self):
        source = self._migration_source()
        assert '"model_lane"' in source
        assert '"ranking_id"' in source

    def test_creates_fk_to_ml_opportunity_rankings(self):
        source = self._migration_source()
        assert "ml_opportunity_rankings" in source
        assert "create_foreign_key" in source

    def test_downgrade_drops_fk_before_column(self):
        source = self._migration_source()
        downgrade_body = source.split("def downgrade")[1]
        drop_fk_idx = downgrade_body.index("drop_constraint")
        drop_col_idx = downgrade_body.index('drop_column("shadow_trades", "ranking_id")')
        assert drop_fk_idx < drop_col_idx
