from types import SimpleNamespace

import pytest

from app.copilot.action_service import apply_changes, profile_state_hash
from app.copilot.router import router
from app.copilot.skill_service import requires_approval
from app.copilot.sql_guard import SqlGuardError, classify_sql


@pytest.mark.parametrize("sql", [
    "SELECT id FROM profiles LIMIT 10",
    "WITH recent AS (SELECT id FROM profiles) SELECT * FROM recent",
    "EXPLAIN (FORMAT JSON) SELECT * FROM shadow_trades",
    "SELECT 'update profiles set config = {}' AS harmless_text",
])
def test_sql_guard_allows_read_only_queries(sql):
    result = classify_sql(sql)
    assert result.classification == "READ_ONLY"
    assert len(result.query_hash) == 64


@pytest.mark.parametrize("sql", [
    "UPDATE profiles SET config = '{}'",
    "WITH changed AS (DELETE FROM profiles RETURNING id) SELECT * FROM changed",
    "SELECT 1; SELECT 2",
    "DROP TABLE profiles",
    "COPY profiles TO PROGRAM 'curl example.com'",
    "SELECT pg_sleep(10)",
    "SELECT * FROM ai_provider_keys",
    "SELECT * FROM profiles FOR UPDATE",
])
def test_sql_guard_blocks_mutating_or_sensitive_queries(sql):
    with pytest.raises(SqlGuardError):
        classify_sql(sql)


def test_sql_guard_rejects_unclosed_literal():
    with pytest.raises(SqlGuardError, match="não finalizado"):
        classify_sql("SELECT 'unterminated")


def test_apply_changes_builds_candidate_without_mutating_source():
    source = {"signals": {"conditions": [{"field": "adx", "value": 20}]}}
    candidate, diff = apply_changes(source, [{
        "path": "signals.conditions.0.value", "old_value": 20,
        "new_value": 25, "reason": "reduzir falsos positivos",
    }])
    assert source["signals"]["conditions"][0]["value"] == 20
    assert candidate["signals"]["conditions"][0]["value"] == 25
    assert diff[0]["old_value"] == 20


def test_apply_changes_fails_closed_on_stale_value():
    with pytest.raises(ValueError, match="Estado divergente"):
        apply_changes({"scoring": {"minimum": 60}}, [{
            "path": "scoring.minimum", "old_value": 55,
            "new_value": 65, "reason": "calibrar",
        }])


def test_profile_state_hash_changes_with_config():
    base = SimpleNamespace(id="p1", config={"x": 1}, updated_at=None, profile_version=None)
    changed = SimpleNamespace(id="p1", config={"x": 2}, updated_at=None, profile_version=None)
    assert profile_state_hash(base) != profile_state_hash(changed)


def test_critical_skills_require_approval():
    assert requires_approval("RISK_RULE") is True
    assert requires_approval("COLUMN_DICTIONARY") is False


def test_router_exposes_required_contracts():
    paths = {(route.path, method) for route in router.routes for method in route.methods}
    required = {
        ("/api/copilot/chat", "POST"),
        ("/api/copilot/query", "POST"),
        ("/api/copilot/schema-map", "GET"),
        ("/api/copilot/patterns", "POST"),
        ("/api/copilot/actions/dry-run", "POST"),
        ("/api/copilot/actions/{plan_id}/approve", "POST"),
        ("/api/copilot/actions/{plan_id}/execute", "POST"),
        ("/api/copilot/skills", "GET"),
        ("/api/copilot/skills", "POST"),
        ("/api/copilot/skills/{skill_id}", "PATCH"),
        ("/api/copilot/skills/{skill_id}/approve", "POST"),
    }
    assert required <= paths