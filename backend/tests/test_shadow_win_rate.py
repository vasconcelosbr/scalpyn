from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.shadow_trades import _finalized_positive_win_rate, shadow_trades_summary


class _SummaryResult:
    def __init__(self, row: SimpleNamespace) -> None:
        self._row = row

    def one(self) -> SimpleNamespace:
        return self._row


class _SummaryDb:
    def __init__(self, row: SimpleNamespace) -> None:
        self._row = row

    async def execute(self, _statement: object) -> _SummaryResult:
        return _SummaryResult(self._row)


def test_win_rate_counts_every_finalized_positive_result() -> None:
    assert _finalized_positive_win_rate(positive_count=3, measured_count=4) == 75.0


def test_win_rate_is_zero_without_measured_finalized_results() -> None:
    assert _finalized_positive_win_rate(positive_count=0, measured_count=0) == 0.0


@pytest.mark.asyncio
async def test_summary_includes_positive_trailing_in_win_rate() -> None:
    row = SimpleNamespace(
        total=8,
        pending=4,
        completed=4,
        win=1,
        loss=1,
        trailing=2,
        timeout=0,
        positive=3,
        measured=4,
        total_pnl_usdt=16.55,
        avg_pnl_pct=0.4125,
        period_start=None,
        period_end=None,
    )

    summary = await shadow_trades_summary(
        status=None,
        symbol=None,
        min_date=None,
        max_date=None,
        source=None,
        profile_id=None,
        profile_version=None,
        db=_SummaryDb(row),  # type: ignore[arg-type]
        user_id=uuid4(),
    )

    assert summary.completed == 4
    assert summary.trailing == 2
    assert summary.timeout == 0
    assert summary.win_rate == 75.0
