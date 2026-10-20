from datetime import datetime, timedelta, timezone

from scripts.audit_l3_decision_gaps import gap_minutes

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)


def test_contiguous_decisions_have_no_gap():
    assert gap_minutes(T, [T + i * M for i in range(5)]) == []


def test_missing_minutes_are_attributed_to_the_decision_that_skipped_them():
    times = [T, T + M, T + 4 * M, T + 5 * M]
    assert gap_minutes(T, times) == [(T + 2 * M, T + 4 * M), (T + 3 * M, T + 4 * M)]


def test_unaligned_entry_starts_at_the_next_minute_like_certify():
    entry = T + timedelta(seconds=27)
    assert gap_minutes(entry, [T, T + M, T + 2 * M]) == []          # entry minute is the boundary candle
    assert gap_minutes(entry, [T + 2 * M]) == [(T + M, T + 2 * M)]  # first post-entry minute never decided
