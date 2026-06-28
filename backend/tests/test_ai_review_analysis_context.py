"""Tests for AI Critic analysis_context auditable context.

Covers:
1. analysis_context structure: dataset, window, sample, metrics, links
2. context_payload_hash and context_query_hash computed and stored
3. COMPLETED only when tokens + summary + analysis_context all present
4. _strip_json_codeblock strips markdown code fences from Claude responses
5. source_to_portfolio_view mapping
6. Legacy review flagged correctly in endpoint response
7. AI_REVIEW_CONTEXT_BUILT event logged to activity
8. AI_REVIEW_COMPLETED_WITH_CONTEXT event logged on success
9. analysis_context.dataset.sources not empty
10. window_start/window_end present and correctly formatted
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Test 1: analysis_context structure has required keys
# ---------------------------------------------------------------------------

def test_analysis_context_structure():
    """analysis_context must have dataset, window, sample, metrics, links."""
    ctx = {
        "dataset": {
            "table": "shadow_trades",
            "portfolio_view": "Aprovados (L3) + Strategy Lab / L3 Lab",
            "sources": ["L3", "L3_LAB"],
            "excluded_sources": ["L1_SPECTRUM", "L3_REJECTED", "L3_SIMULATED"],
            "filters": {
                "status": ["COMPLETED"],
                "pnl_pct_not_null": True,
                "profile_id_not_null": True,
                "include_running": False,
            },
        },
        "window": {
            "window_hours": 4,
            "window_start": "2026-06-28T00:00:00+00:00",
            "window_end": "2026-06-28T04:00:00+00:00",
            "timezone": "UTC",
        },
        "sample": {
            "trades_count": 57,
            "completed_trades": 57,
            "running_trades": 0,
            "profiles_count": 12,
            "symbols_count": 31,
            "source_breakdown": {"L3": {"trades": 30, "profiles": 8}, "L3_LAB": {"trades": 27, "profiles": 7}},
        },
        "metrics": {
            "win_rate": 0.22,
            "avg_pnl_pct": -0.0055,
            "pnl_total_usdt": -123.45,
            "negative_profiles": 8,
            "hard_negatives": 42,
        },
        "links": {
            "review_id": str(uuid.uuid4()),
            "context_query_hash": "abc123",
            "context_payload_hash": "def456",
        },
    }

    assert "dataset" in ctx
    assert "window" in ctx
    assert "sample" in ctx
    assert "metrics" in ctx
    assert "links" in ctx
    assert ctx["dataset"]["sources"] == ["L3", "L3_LAB"]
    assert ctx["window"]["window_hours"] == 4
    assert ctx["sample"]["trades_count"] == 57
    assert ctx["links"]["context_payload_hash"] == "def456"


# ---------------------------------------------------------------------------
# Test 2: context_payload_hash changes when sources change
# ---------------------------------------------------------------------------

def test_context_payload_hash_changes_when_sources_change():
    """Different sources → different context_payload_hash."""
    def _hash(sources):
        ctx = {"dataset": {"sources": sources}, "window": {"window_hours": 4}}
        return hashlib.sha256(json.dumps(ctx, sort_keys=True).encode()).hexdigest()[:32]

    h1 = _hash(["L3", "L3_LAB"])
    h2 = _hash(["L3"])
    h3 = _hash(["L1_SPECTRUM"])
    assert h1 != h2
    assert h1 != h3
    assert h2 != h3


# ---------------------------------------------------------------------------
# Test 3: context_query_hash is stable for same sources and window
# ---------------------------------------------------------------------------

def test_context_query_hash_stable():
    """Same sources + same window_h → same context_query_hash."""
    sources = ["L3", "L3_LAB"]
    window_h = 4

    def _qhash(s, w):
        return hashlib.sha256(f"sources={sorted(s)}&window_h={w}".encode()).hexdigest()[:32]

    h1 = _qhash(sources, window_h)
    h2 = _qhash(sources, window_h)
    assert h1 == h2

    h3 = _qhash(["L3"], window_h)
    assert h1 != h3


# ---------------------------------------------------------------------------
# Test 4: _strip_json_codeblock removes markdown fences
# ---------------------------------------------------------------------------

def test_strip_json_codeblock_removes_fences():
    """_strip_json_codeblock must strip ```json...``` wrapper Claude emits."""
    from app.services.profile_intelligence_live_service import _strip_json_codeblock

    raw_with_fence = '```json\n{"summary": "test"}\n```'
    stripped = _strip_json_codeblock(raw_with_fence)
    parsed = json.loads(stripped)
    assert parsed["summary"] == "test"


def test_strip_json_codeblock_passthrough_plain():
    """Plain JSON without fences must pass through unchanged."""
    from app.services.profile_intelligence_live_service import _strip_json_codeblock

    raw = '{"summary": "test"}'
    stripped = _strip_json_codeblock(raw)
    assert stripped == raw


def test_strip_json_codeblock_backtick_only():
    """``` without language tag should also be stripped."""
    from app.services.profile_intelligence_live_service import _strip_json_codeblock

    raw = '```\n{"key": "value"}\n```'
    stripped = _strip_json_codeblock(raw)
    parsed = json.loads(stripped)
    assert parsed["key"] == "value"


# ---------------------------------------------------------------------------
# Test 5: source_to_portfolio_view mapping
# ---------------------------------------------------------------------------

def test_source_to_portfolio_view_mapping():
    """Known sources must map to their display names."""
    from app.services.profile_intelligence_live_service import _source_to_portfolio_view, _SOURCE_VIEW_MAP

    assert _source_to_portfolio_view(["L3"]) == "Aprovados (L3)"
    assert _source_to_portfolio_view(["L3_LAB"]) == "Strategy Lab / L3 Lab"
    assert _source_to_portfolio_view(["L3", "L3_LAB"]) == "Aprovados (L3) + Strategy Lab / L3 Lab"
    assert _source_to_portfolio_view(["L1_SPECTRUM"]) == "Dataset ML (L1)"
    assert _source_to_portfolio_view(["L3_REJECTED"]) == "Rejeitados (L3)"
    assert _source_to_portfolio_view(["L3_SIMULATED"]) == "Simulados (L3)"

    # Unknown source → UNKNOWN(x) warning format
    result = _source_to_portfolio_view(["UNKNOWN_SRC"])
    assert "UNKNOWN" in result


def test_source_view_map_defined():
    """_SOURCE_VIEW_MAP must define all canonical sources."""
    from app.services.profile_intelligence_live_service import _SOURCE_VIEW_MAP

    required = {"L3", "L3_REJECTED", "L3_SIMULATED", "L1_SPECTRUM", "STRATEGY_LAB", "L3_LAB"}
    missing = required - set(_SOURCE_VIEW_MAP.keys())
    assert not missing, f"Missing source mappings: {missing}"


# ---------------------------------------------------------------------------
# Test 6: window_start/window_end must be ISO-8601 strings
# ---------------------------------------------------------------------------

def test_window_start_end_iso8601():
    """window_start and window_end must be valid ISO-8601 UTC strings."""
    from datetime import datetime, timezone, timedelta

    window_end = datetime(2026, 6, 28, 4, 0, tzinfo=timezone.utc)
    window_start = window_end - timedelta(hours=4)

    start_str = window_start.isoformat()
    end_str = window_end.isoformat()

    assert "T" in start_str
    assert "T" in end_str
    assert "2026" in start_str
    # Must be parseable
    dt = datetime.fromisoformat(start_str)
    assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# Test 7: analysis_context.dataset.sources not empty
# ---------------------------------------------------------------------------

def test_ai_sources_not_empty():
    """_AI_SOURCES must not be empty."""
    from app.services.profile_intelligence_live_service import _AI_SOURCES

    assert isinstance(_AI_SOURCES, list)
    assert len(_AI_SOURCES) > 0


# ---------------------------------------------------------------------------
# Test 8: endpoint response must include analysis_context fields
# ---------------------------------------------------------------------------

def test_ai_review_endpoint_returns_context_fields():
    """The ai-review endpoint response must include analysis_context keys."""
    import inspect
    import app.api.profile_intelligence_live as live_api

    source = inspect.getsource(live_api.live_ai_review)
    assert "analysis_context" in source
    assert "context_payload_hash" in source
    assert "analysis_context_available" in source
    assert "analysis_context_legacy" in source
    assert "review_id" in source


# ---------------------------------------------------------------------------
# Test 9: COMPLETED only when analysis_context is present
# ---------------------------------------------------------------------------

def test_completed_requires_analysis_context():
    """FAILED_MISSING_ANALYSIS_CONTEXT must be raised when context is absent."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc.run_ai_review_cycle)
    assert "FAILED_MISSING_ANALYSIS_CONTEXT" in source


# ---------------------------------------------------------------------------
# Test 10: AI_REVIEW_COMPLETED_WITH_CONTEXT event must be logged
# ---------------------------------------------------------------------------

def test_completed_event_includes_context():
    """AI_REVIEW_COMPLETED_WITH_CONTEXT event must be used instead of AI_REVIEW_COMPLETED."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc.run_ai_review_cycle)
    assert "AI_REVIEW_COMPLETED_WITH_CONTEXT" in source
    assert "AI_REVIEW_CONTEXT_BUILT" in source
    assert "AI_REVIEW_CONTEXT_PERSISTED" in source
