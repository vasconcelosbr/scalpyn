from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks import shadow_trade_monitor


@pytest.mark.asyncio
async def test_run_best_effort_budgeted_stops_after_deadline():
    seen = []

    async def _worker(item):
        seen.append(item)

    monotonic_values = iter([0.0, 0.2, 1.1])
    with patch.object(
        shadow_trade_monitor.time,
        "monotonic",
        side_effect=lambda: next(monotonic_values),
    ):
        processed, skipped = await shadow_trade_monitor._run_best_effort_budgeted(
            [1, 2, 3],
            _worker,
            deadline=1.0,
            item_label="test_items",
        )

    assert seen == [1, 2]
    assert processed == 2
    assert skipped == 1


@pytest.mark.asyncio
async def test_run_best_effort_budgeted_drains_all_items_when_budget_allows():
    seen = []

    async def _worker(item):
        seen.append(item)

    monotonic_values = iter([0.0, 0.1, 0.2])
    with patch.object(
        shadow_trade_monitor.time,
        "monotonic",
        side_effect=lambda: next(monotonic_values),
    ):
        processed, skipped = await shadow_trade_monitor._run_best_effort_budgeted(
            ["a", "b", "c"],
            _worker,
            deadline=1.0,
            item_label="test_items",
        )

    assert seen == ["a", "b", "c"]
    assert processed == 3
    assert skipped == 0
