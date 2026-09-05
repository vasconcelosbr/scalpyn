from datetime import datetime, timezone

import pytest

from scripts.audit_r1_invariants import (
    STATIC_INVARIANTS,
    _parse_cutoff,
    _terminal_shadow_trades_invariant,
)


def test_r1_invariants_name_each_distinct_population() -> None:
    by_name = {item["name"]: item for item in STATIC_INVARIANTS}

    assert "FROM config_profiles" in by_name["active_config_profiles"]["sql"]
    assert "FROM score_engine_versions" in by_name["baseline_score_engine_versions"]["sql"]
    assert "FROM profiles" in by_name["active_strategy_profiles"]["sql"]
    assert "FROM profile_versions" in by_name["governed_profile_versions"]["sql"]
    assert "status = 'CHAMPION' AND is_active = true" in by_name["governed_profile_versions"]["sql"]
    assert "status = 'SHADOW'" in by_name["governed_profile_versions"]["sql"]


def test_terminal_population_is_frozen_by_terminal_write_time() -> None:
    cutoff = "2026-09-04T21:26:20.598431+00:00"
    invariant = _terminal_shadow_trades_invariant(cutoff)

    assert f"completed_at < '{cutoff}'" in invariant["predicate"]
    assert f"completed_at < '{cutoff}'" in invariant["sql"]
    assert "label_resolved_at <" not in invariant["sql"]
    assert "'outcome', outcome" in invariant["sql"]
    assert "'pnl_usdt', pnl_usdt" in invariant["sql"]
    assert "'completed_at', completed_at" in invariant["sql"]


def test_fixed_cutoff_requires_timezone_and_normalizes_to_utc() -> None:
    parsed = _parse_cutoff("2026-09-04T18:26:20.598431-03:00")
    assert parsed == datetime(2026, 9, 4, 21, 26, 20, 598431, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="explicit timezone"):
        _parse_cutoff("2026-09-04T21:26:20.598431")

    with pytest.raises(ValueError, match="cannot be later"):
        _parse_cutoff(
            "2026-09-05T00:00:01Z",
            not_after=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
