from app.tasks.shadow_trade_monitor import allocate_monitor_batch_shares


# 2026-09-26 incident: a single created_at-ASC FIFO across every non-L3
# source let one large, slow-draining backlog (L3_LAB: 426 active rows,
# oldest from 2026-09-11) permanently starve a much smaller one
# (L3_REJECTED: 49 rows, oldest from 2026-09-20) sharing the same pool --
# L3_LAB's older rows always won the ORDER BY and consumed every remaining
# slot, so L3_REJECTED never got a single monitor pass in 6 days.


def test_no_sources_or_no_slots_returns_empty():
    assert allocate_monitor_batch_shares([], 40) == {}
    assert allocate_monitor_batch_shares(["L3_LAB"], 0) == {}
    assert allocate_monitor_batch_shares(["L3_LAB"], -5) == {}


def test_single_source_gets_all_remaining_slots():
    assert allocate_monitor_batch_shares(["L3_LAB"], 40) == {"L3_LAB": 40}


def test_regression_large_backlog_no_longer_starves_a_small_one():
    """The actual incident shape: L3_LAB (huge, oldest) and L3_REJECTED
    (small, newer) sharing the same remaining_slots pool. Both must get a
    share -- neither can be reduced to zero purely because the other has
    more (or older) rows."""
    shares = allocate_monitor_batch_shares(["L3_LAB", "L3_REJECTED"], 40)
    assert shares["L3_LAB"] == 20
    assert shares["L3_REJECTED"] == 20
    assert shares["L3_REJECTED"] > 0


def test_uneven_split_is_deterministic_and_favors_earlier_alphabetically():
    shares = allocate_monitor_batch_shares(["L3_REJECTED", "L1_SPECTRUM", "L3_LAB"], 10)
    assert sum(shares.values()) == 10
    # sorted order: L1_SPECTRUM, L3_LAB, L3_REJECTED -- remainder of 1 goes
    # to the first source alphabetically.
    assert shares == {"L1_SPECTRUM": 4, "L3_LAB": 3, "L3_REJECTED": 3}


def test_more_sources_than_slots_still_gives_everyone_at_least_a_chance():
    shares = allocate_monitor_batch_shares(
        ["L1_SPECTRUM", "L3_LAB", "L3_REJECTED", "L3_SIMULATED"], 3
    )
    assert sum(shares.values()) == 3
    assert sum(1 for v in shares.values() if v > 0) == 3
    assert sum(1 for v in shares.values() if v == 0) == 1
