from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.shadow_trades import _tp_sl_win_rate, shadow_trades_summary


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


def test_win_rate_uses_only_tp_and_sl_outcomes() -> None:
    assert _tp_sl_win_rate(tp_count=33, sl_count=31) == 51.56


def test_win_rate_is_zero_without_tp_or_sl_outcomes() -> None:
    assert _tp_sl_win_rate(tp_count=0, sl_count=0) == 0.0


@pytest.mark.asyncio
async def test_summary_excludes_trailing_and_timeout_from_win_rate() -> None:
    row = SimpleNamespace(
        total=79,
        pending=7,
        completed=72,
        win=33,
        loss=31,
        trailing=8,
        timeout=0,
        total_pnl_usdt=0,
        avg_pnl_pct=0,
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

    assert summary.completed == 72
    assert summary.trailing == 8
    assert summary.timeout == 0
    assert summary.win_rate == 51.56
