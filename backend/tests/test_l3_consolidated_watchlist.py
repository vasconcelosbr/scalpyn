from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.watchlists import list_l3_consolidated_assets, router


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    def __init__(self, rows):
        self._rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _ScalarResult(self._rows)


def test_l3_consolidated_static_route_precedes_dynamic_assets_route():
    paths = [route.path for route in router.routes]
    assert paths.index("/api/watchlists/l3-consolidated/assets") < paths.index(
        "/api/watchlists/{watchlist_id}/assets"
    )


def _shadow(
    *,
    symbol: str,
    created_at: datetime,
    direction: str = "SPOT",
    enforced: bool = True,
    config_snapshot=None,
):
    return SimpleNamespace(
        id=uuid4(),
        symbol=symbol,
        direction=direction,
        status="RUNNING",
        profile_id=uuid4(),
        profile_name="Fallback Profile",
        entry_price=123.45,
        created_at=created_at,
        entry_timestamp=created_at + timedelta(seconds=5),
        config_snapshot=config_snapshot,
        l3_consolidation_enforced=enforced,
    )


@pytest.mark.asyncio
async def test_l3_consolidated_assets_exposes_winner_lineage_and_deduplicates_owner():
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    canonical = _shadow(
        symbol="BTC_USDT",
        created_at=now,
        config_snapshot={
            "consolidation": {
                "event_id": "evt-1",
                "rule_version": "single_profile_per_symbol_v1",
                "candidate_count": 3,
                "primary_profile_id": "profile-winner",
                "primary_profile_name": "Winner Profile",
                "candidate_profile_ids": ["profile-winner", "profile-b", "profile-c"],
                "candidate_profile_names": ["Winner Profile", "Profile B", "Profile C"],
                "suppressed_profile_ids": ["profile-b", "profile-c"],
                "suppressed_profile_names": ["Profile B", "Profile C"],
                "selection_rule": ["decision_score_desc"],
                "selection_metrics": {"decision_score": 87.5},
            }
        },
    )
    duplicate = _shadow(symbol="BTC_USDT", created_at=now + timedelta(minutes=1))
    second_symbol = _shadow(symbol="ETH_USDT", created_at=now + timedelta(minutes=2))
    db = _StubSession([canonical, duplicate, second_symbol])
    user_id = uuid4()

    response = await list_l3_consolidated_assets(user_id=user_id, db=db)

    assert response["id"] == "l3-consolidated"
    assert response["virtual"] is True
    assert response["read_only"] is True
    assert response["total"] == 2
    assert [item["symbol"] for item in response["items"]] == ["BTC_USDT", "ETH_USDT"]

    winner = response["items"][0]
    assert winner["shadow_id"] == str(canonical.id)
    assert winner["profile_id"] == "profile-winner"
    assert winner["profile_name"] == "Winner Profile"
    assert winner["candidate_count"] == 3
    assert winner["suppressed_count"] == 2
    assert winner["selection_metrics"] == {"decision_score": 87.5}
    assert winner["consolidation_enforced"] is True

    sql = str(db.statement)
    assert "shadow_trades.user_id" in sql
    assert "shadow_trades.source" in sql
    assert "shadow_trades.status" in sql


@pytest.mark.asyncio
async def test_l3_consolidated_assets_keeps_legacy_active_owner_visible():
    row = _shadow(
        symbol="SOL_USDT",
        created_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        enforced=False,
        config_snapshot=None,
    )
    response = await list_l3_consolidated_assets(
        user_id=uuid4(),
        db=_StubSession([row]),
    )

    item = response["items"][0]
    assert item["profile_name"] == "Fallback Profile"
    assert item["candidate_count"] is None
    assert item["candidate_profile_names"] == []
    assert item["suppressed_count"] == 0
    assert item["consolidation_enforced"] is False
