"""Tests for the ML Opportunity Ranking producer (audit 2026-06-24, item 7
of the post-VALIDACAO_GERAL punch list).

ml_opportunity_rankings existed since migration 105 but had zero producer —
this wires the existing L3 ML gate in pipeline_scan.py to insert one row per
(run_id, symbol) scored, and threads the resulting ranking_id into the
Shadow lineage (closing the gap left by Fase 8, which wired
ml_model_id/ml_probability/model_lane but not ranking_id).
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestPipelineScanProducerWiring:
    def _source(self) -> str:
        path = Path(__file__).resolve().parents[2] / "backend" / "app" / "tasks" / "pipeline_scan.py"
        return path.read_text(encoding="utf-8")

    def test_record_ml_opportunity_ranking_function_defined(self):
        source = self._source()
        assert "_record_ml_opportunity_ranking" in source

    def test_insert_targets_ml_opportunity_rankings_table(self):
        source = self._source()
        assert "INSERT INTO ml_opportunity_rankings" in source

    def test_insert_never_raises_into_caller(self):
        """Ranking persistence is observability — a DB error here must not
        affect the L3 decision flow (caught and logged, not propagated)."""
        source = self._source()
        idx = source.index("_record_ml_opportunity_ranking")
        snippet = source[idx: idx + 4500]
        assert "except Exception as _rank_exc" in snippet

    def test_run_id_generated_once_per_gate_cycle(self):
        source = self._source()
        assert "_ml_run_id = uuid4()" in source

    def test_ranking_id_threaded_into_ml_gate_scores(self):
        source = self._source()
        assert '"ranking_id": str(_ranking_id) if _ranking_id else None' in source

    def test_source_label_is_l3_ml_gate(self):
        source = self._source()
        idx = source.index("INSERT INTO ml_opportunity_rankings")
        snippet = source[idx: idx + 3000]
        assert '"source": "L3_ML_GATE"' in snippet


class TestShadowLineageRankingIdWiring:
    def _source(self) -> str:
        from backend.app.services import shadow_trade_service
        return inspect.getsource(shadow_trade_service)

    def test_ranking_id_passed_through_dataclasses_replace(self):
        source = self._source()
        idx = source.index("_decision_lineage = _dc.replace(")
        snippet = source[idx: idx + 400]
        assert "ranking_id=_ml_for_symbol.get(\"ranking_id\")" in snippet

    def test_watchlist_lineage_context_has_ranking_id_field(self):
        from backend.app.schemas.watchlist_lineage_context import WatchlistLineageContext
        ctx = WatchlistLineageContext(ranking_id="some-uuid")
        assert ctx.ranking_id == "some-uuid"

    def test_insert_shadow_sql_writes_ranking_id(self):
        from backend.app.services.shadow_trade_service import _INSERT_SHADOW_SQL
        sql_str = str(_INSERT_SHADOW_SQL)
        assert "ranking_id" in sql_str
        assert ":ranking_id" in sql_str


class TestMigration105SchemaSupportsProducer:
    def test_ml_opportunity_rankings_columns_match_producer_insert(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "alembic" / "versions" / "105_ml_opportunity_rankings.py"
        )
        source = path.read_text(encoding="utf-8")
        for col in (
            "run_id", "symbol", "profile_id", "watchlist_id", "model_lane",
            "model_id", "promotion_gate_status", "win_fast_probability",
            "score_status", "reason_code", "source",
        ):
            assert f'"{col}"' in source, f"missing column {col} in migration 105"
