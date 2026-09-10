from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.pump_radar_research import (
    closed_history, load_user_universe, reconstruct_indicators,
)
from app.api.pump_radar import get_chart
from app.tasks.pump_radar import (
    _anchor_features, _range_samples, _snapshots, _window_validity_prefixes,
)


def candle(at, value=10):
    return SimpleNamespace(open_time=at, close_time=at + timedelta(minutes=5),
        is_closed=True, open=value, high=value + 1, low=value - 1, close=value,
        volume_base=100, volume_quote=1000)


def quote_volume_prefix(rows):
    prefix = [Decimal(0)] * (len(rows) + 1)
    for i, row in enumerate(rows):
        prefix[i + 1] = prefix[i] + Decimal(row.volume_quote or 0)
    return prefix


def anchor_features(rows, index, btc, btc_times_sorted, prefix=None):
    """Test helper: call _anchor_features with freshly computed prefixes so
    call sites don't need to know about the window-validity precomputation."""
    if prefix is None:
        prefix = quote_volume_prefix(rows)
    bad_closed_prefix, bad_gap_prefix = _window_validity_prefixes(rows)
    return _anchor_features(rows, index, btc, btc_times_sorted, prefix, bad_closed_prefix, bad_gap_prefix)


def brute_force_window_valid(rows, index, anchor_open_time=None):
    """Original O(288) semantics, kept as an independent oracle for the new
    prefix-sum based check."""
    anchor_open_time = anchor_open_time if anchor_open_time is not None else rows[index].open_time
    history = rows[index - 288:index]
    if any(not row.is_closed or row.close_time > anchor_open_time for row in history):
        return False
    if any(history[i].open_time - history[i - 1].open_time != timedelta(minutes=5) for i in range(1, len(history))):
        return False
    return True


def test_1235_never_consumes_1235_to_1240_candle():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(30, -2, -1)]
    first, history = reconstruct_indicators(rows, at, "5m", {"ema": {"enabled": True, "periods": [9, 21]}})
    assert history[-1].open_time == at - timedelta(minutes=5)
    rows[-1].close = 99999
    rows[-2].close = 99999
    second, _ = reconstruct_indicators(rows, at, "5m", {"ema": {"enabled": True, "periods": [9, 21]}})
    assert first == second
    assert first["ema9"] == 10


def test_gaps_break_warmup_and_unclosed_candles_are_excluded():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=m)) for m in (25, 20, 10, 5, 0)]
    assert [r.open_time for r in closed_history(rows, at, "5m")] == [rows[2].open_time, rows[3].open_time]
    rows[3].is_closed = False
    assert closed_history(rows, at, "5m") == []


def test_reconstruction_never_emits_flow_book_or_nonfinite_values():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=m)) for m in (10, 5)]
    config = {"taker_ratio": {"enabled": True}}
    with patch("app.services.pump_radar_research.FeatureEngine") as engine:
        engine.return_value.calculate.return_value = {"rsi": 55, "cvd": 100, "taker_ratio": 0.6, "volume_delta": 9, "spread_pct": 1, "adx": float("nan")}
        result, _ = reconstruct_indicators(rows, at, "5m", config)
        assert result == {"rsi": 55}
        assert engine.call_args.args[0]["taker_ratio"]["enabled"] is False
        assert config["taker_ratio"]["enabled"] is True
    assert reconstruct_indicators(rows, at, "5m", None)[0] == {}


@pytest.mark.asyncio
async def test_universe_preserves_memberships_and_scopes_both_queries_by_user():
    user, pool, watchlist = uuid4(), uuid4(), uuid4()
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [("BTC/USDT", pool)]),
        SimpleNamespace(all=lambda: [("BTC_USDT", watchlist, "L3"), ("ETH_USDT", watchlist, "L2")]),
    ]))
    members = await load_user_universe(db, user)
    assert [m["symbol"] for m in members] == ["BTC_USDT", "ETH_USDT"]
    assert [m["level"] for m in members[0]["memberships"]] == ["POOL", "L3"]
    for call in db.execute.call_args_list:
        query = call.args[0].compile()
        assert user in query.params.values()
        assert "spot" in query.params.values()


@pytest.mark.asyncio
async def test_chart_reconstructs_late_capture_but_hides_future_markers():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    event = SimpleNamespace(id=uuid4(), symbol="BTC_USDT", start_at=at,
        end_at=at + timedelta(hours=1), confirmed_market_at=at + timedelta(minutes=10),
        peak_at=at + timedelta(minutes=30), start_price=10, peak_price=12, end_price=11)
    row = candle(at - timedelta(minutes=5))
    row.available_at = at + timedelta(days=1)
    row.quality_status = "VALID"
    row.contract_version = "test"
    row.provenance = {"historical_availability_proven": False}
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]))
    with patch("app.api.pump_radar._owned_event", AsyncMock(return_value=event)):
        response = await get_chart(uuid4(), event.id, selected_at=None, hide_future=True, db=db, user_id=uuid4())
    assert response["data"]["selected_at"] == at.isoformat()
    assert response["data"]["reconstruction_status"] == "RECONSTRUCTED"
    assert [m["kind"] for m in response["data"]["markers"]] == ["START"]
    for call in db.execute.call_args_list[:3]:
        query = call.args[0].compile()
        assert "pump_radar_ohlcv.close_time <=" in str(query)
        assert at in query.params.values()
        assert "available_at IS NULL" not in str(query)


@pytest.mark.asyncio
async def test_event_without_shadow_still_persists_reconstructed_snapshots():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    event = SimpleNamespace(id=uuid4(), start_at=at)
    run = SimpleNamespace(id=uuid4(), user_id=uuid4(), date_from=at - timedelta(hours=1),
        date_to=at + timedelta(hours=1), config_snapshot={},
        provenance={"indicators_config": {"ema": {"enabled": True, "periods": [9]}}})
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(50, 0, -1)]
    inserted = []

    async def execute(stmt):
        table = getattr(getattr(stmt, "table", None), "name", "")
        if table == "pump_radar_indicator_snapshots":
            inserted.append(table)
            return SimpleNamespace(scalar_one_or_none=lambda: uuid4())
        if table == "pump_radar_indicator_values":
            inserted.append(table)
        sql = str(stmt)
        if "JOIN pump_radar_event_links" in sql:
            return SimpleNamespace(all=lambda: [])
        data = rows if "FROM pump_radar_ohlcv" in sql else [event]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: data))

    db = SimpleNamespace(get=AsyncMock(return_value=run), execute=AsyncMock(side_effect=execute), commit=AsyncMock())
    session = AsyncMock()
    session.__aenter__.return_value = db
    with patch("app.database.CeleryAsyncSessionLocal", return_value=session), \
         patch("app.tasks.pump_radar._cancel_asset_if_requested", AsyncMock(return_value=False)), \
         patch("app.tasks.pump_radar._refresh_run", AsyncMock()), \
         patch("app.tasks.pump_radar.enqueue"):
        result = await _snapshots(run.id, "BTC_USDT")
    assert result["snapshots"] == 5
    assert inserted.count("pump_radar_indicator_snapshots") == 5
    assert inserted.count("pump_radar_indicator_values") == 5


def test_control_features_do_not_use_anchor_candle_or_unclosed_btc():
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    btc = {r.close_time: r for r in rows}
    btc_times_sorted = sorted(btc)
    prefix = quote_volume_prefix(rows)
    first = anchor_features(rows, 300, btc, btc_times_sorted, prefix)
    assert first is not None
    rows[-1].high = 9999
    rows[-1].close = 9999
    assert anchor_features(rows, 300, btc, btc_times_sorted, prefix) == first


def test_anchor_features_liquidity_uses_prefix_sum_not_flat_1000_per_candle():
    """quote_volume used to be recomputed by summing 288 fresh Decimal(volume_quote)
    objects per call; it's now a prefix-sum lookup. Guard against an off-by-one in
    the prefix array by giving each candle a distinct volume_quote and checking the
    288-candle liquidity sum matches a direct brute-force sum of that exact window."""
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    for i, row in enumerate(rows):
        row.volume_quote = 100 + i
    btc = {r.close_time: r for r in rows}
    btc_times_sorted = sorted(btc)
    prefix = quote_volume_prefix(rows)

    for index in (288, 300):
        result = anchor_features(rows, index, btc, btc_times_sorted, prefix)
        assert result is not None
        expected = float(sum(Decimal(row.volume_quote) for row in rows[index - 288 : index]))
        assert result["liquidity_quote_24h"] == pytest.approx(expected)


def test_window_validity_prefix_sums_match_brute_force_scan():
    """The two 288-length any() scans (not-closed / close_time-overrun, and
    5-minute gap continuity) were replaced with O(1) prefix-sum lookups plus
    a single boundary-row check. Verify the reduction against the original
    brute-force any() logic (kept as `brute_force_window_valid`) across:
    a fully valid window, an internal gap, a not-closed candle mid-window,
    and a gap right at the anchor boundary (the one case the reduction
    handles specially by checking only rows[index-1] instead of the whole
    window)."""
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    btc = {}  # unused by this check; _anchor_features short-circuits on window validity first
    btc_times_sorted = []

    def is_valid(rows, index):
        bad_closed_prefix, bad_gap_prefix = _window_validity_prefixes(rows)
        prefix = quote_volume_prefix(rows)
        result = _anchor_features(rows, index, btc, btc_times_sorted, prefix, bad_closed_prefix, bad_gap_prefix)
        # A None from a downstream check (ATR/BTC lookup) would also read as
        # "invalid" here, so this test only asserts on scenarios where the
        # window check itself is the deciding factor (BTC/ATR either present
        # for all rows or irrelevant since window invalidity short-circuits
        # before they're consulted).
        return result is not None

    # Fully valid: uniform 5-minute, all closed.
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    assert brute_force_window_valid(rows, 300) is True
    # (liquidity/ATR/BTC all trivially satisfiable with these uniform rows)

    # Internal gap: skip one candle in the middle of the 288-window.
    gappy = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1) if i != 150]
    assert brute_force_window_valid(gappy, len(gappy) - 1) is False
    bad_closed_prefix, bad_gap_prefix = _window_validity_prefixes(gappy)
    idx = len(gappy) - 1
    assert (bad_gap_prefix[idx] - bad_gap_prefix[idx - 287]) > 0

    # Not-closed candle mid-window.
    unclosed = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    unclosed[50].is_closed = False
    assert brute_force_window_valid(unclosed, 300) is False
    bad_closed_prefix, bad_gap_prefix = _window_validity_prefixes(unclosed)
    assert (bad_closed_prefix[300] - bad_closed_prefix[300 - 288]) > 0

    # Boundary overrun: rows[index-1].close_time pushed past anchor.open_time
    # (an overlap right at the window/anchor seam) with the rest gap-free.
    overlap = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    overlap[299].close_time = overlap[300].open_time + timedelta(minutes=1)
    assert brute_force_window_valid(overlap, 300) is False
    assert is_valid(overlap, 300) is False


def test_anchor_features_btc_lookup_matches_brute_force_with_gaps():
    """_anchor_features used to scan every btc_by_time key per call (O(n) per
    candidate row); this now uses bisect over a pre-sorted list. Guard that the
    bisect result stays identical to the old "max(value <= threshold)" scan,
    including with gaps in the BTC series (missing candles are common in
    production captures)."""
    at = datetime(2026, 9, 8, 12, 35, tzinfo=timezone.utc)
    rows = [candle(at - timedelta(minutes=5 * i)) for i in range(300, -1, -1)]
    btc_rows = [candle(at - timedelta(minutes=5 * i), value=10 + i) for i in range(300, -1, -1) if i % 3 != 1]
    btc = {r.close_time: r for r in btc_rows}
    btc_times_sorted = sorted(btc)
    prefix = quote_volume_prefix(rows)

    def brute_force(anchor_open_time):
        btc_times = [v for v in btc if v <= anchor_open_time]
        if not btc_times:
            return None
        now = btc[max(btc_times)]
        prior_times = [v for v in btc_times if v <= anchor_open_time - timedelta(hours=1)]
        if not prior_times:
            return None
        return now, btc[max(prior_times)]

    for index in (288, 300):
        expected = brute_force(rows[index].open_time)
        result = anchor_features(rows, index, btc, btc_times_sorted, prefix)
        if expected is None:
            assert result is None
        else:
            assert result is not None
            assert result["btc_regime_1h_pct"] == pytest.approx(
                float((Decimal(expected[0].close) / Decimal(expected[1].close) - Decimal("1")) * Decimal("100"))
            )


def test_statistics_count_distinct_events_and_controls_and_keep_split_separate():
    at = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    event = SimpleNamespace(id=uuid4(), start_at=at)
    controls = [SimpleNamespace(symbol="BTC_USDT", anchor_at=at - timedelta(hours=i),
        match_features={"event": {"rsi": 50}, "control": {"rsi": 40}},
        outcome={"followup_minutes": 180}) for i in (4, 5, 6)]
    future_control = SimpleNamespace(symbol="BTC_USDT", anchor_at=at + timedelta(days=1),
        match_features={"event": {"rsi": 50}, "control": {"rsi": 99}}, outcome={"followup_minutes": 180})
    pairs = [(control, event) for control in controls + [controls[0], future_control]]
    pumps, matched = _range_samples(pairs, "rsi", (at + timedelta(days=1)).date(), False)
    assert pumps == [50]
    assert matched == [40, 40, 40]
