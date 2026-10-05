"""Pump Score v1 — structure, gates, non-compensatory score, hysteresis, engine selection.

Fixtures are synthetic; they prove mechanics only, never predictive accuracy.
"""
from copy import deepcopy

import pytest

from app.services import pump_monitor_engine as eng
from app.services import pump_monitor_service as svc
from app.services import pump_score_v1 as v1

M5 = 300_000
M1 = 60_000


def spec(**overrides):
    s = deepcopy(v1.DEFAULT_V1)
    for path, value in overrides.items():
        node = s
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return s


def candles(closes, *, start_ms=0, vol=100.0, vols=None, wick=0.0):
    """Closed 5m candles: open = previous close, small symmetric range."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"time_ms": start_ms + i * M5, "open": o, "high": max(o, c) + wick + 0.01,
                    "low": min(o, c) - 0.01, "close": c, "volume": (vols[i] if vols else vol)})
        prev = c
    return out


def now_after(series):
    return series[-1]["time_ms"] + M5 + 10_000


# ── 1. Structure ─────────────────────────────────────────────────────────────

def test_staircase_trend_is_efficient_consistent_and_not_concentrated():
    closes = [100.0] * 36 + [100 + 0.25 * i for i in range(1, 13)]  # 3h base, then 1h steady climb
    s = candles(closes)
    st = v1.structure_metrics(s, spec(), now_after(s))
    assert st["reason"] is None
    assert st["progress_atr"] > 0 and st["efficiency_short"] == pytest.approx(1.0)
    assert st["consistency"] == 1.0 and st["higher_lows"] == 1.0
    assert st["concentration"] == pytest.approx(1 / 6)
    assert st["compression_ratio"] < 1.0  # the base before the move was quieter than the window


def test_micro_pump_is_concentrated_in_one_candle():
    closes = [100.0] * 42 + [100.0, 100.0, 100.0, 100.0, 103.0, 103.0]
    s = candles(closes)
    st = v1.structure_metrics(s, spec(), now_after(s))
    assert st["concentration"] == pytest.approx(1.0)


def test_stale_or_short_series_is_null_never_zero():
    s = candles([100.0] * 48)
    assert v1.structure_metrics(s, spec(), now_after(s) + 3_600_000)["reason"] == "stale_candles"
    short = candles([100.0] * 5)
    out = v1.structure_metrics(short, spec(), now_after(short))
    assert out["reason"] == "insufficient_candles" and out["progress_atr"] is None


# ── 2/3. Gates, condition and score ──────────────────────────────────────────

def good_values(**kw):
    v = {"progress_atr": 1.2, "ret_pct": 0.9, "atr_pct": 0.3, "efficiency_short": 0.9, "efficiency_long": 0.8,
         "consistency": 0.83, "higher_lows": 0.83, "concentration": 0.3, "wick": 0.1, "rvol_5m": 2.0,
         "volume_spike_max": 2.5, "compression_ratio": 0.5, "window_delta_norm": 0.3, "cvd_slope": 0.3,
         "progress_1m_atr": 0.5, "extension_atr": 1.0, "slippage_buy_pct": 0.05, "ask_depth_1pct": 50_000.0,
         "rs_atr": 0.8}
    v.update(kw)
    return v


def test_rising_asset_passes_every_gate_and_scores():
    v = good_values()
    gates = v1.evaluate_gates(v, "favoravel", spec())
    assert all(g["result"] is True for g in gates)
    assert v1.classify(v, gates, spec()) == "subindo"
    out = v1.strength_score(v, {}, spec())
    assert out["score"] is not None and 0 < out["score"] <= 100


@pytest.mark.parametrize("change,expected", [
    ({"progress_atr": -0.8, "extension_atr": -2.5}, "caindo"),        # the v0 failure: falling with buying
    ({"progress_atr": -0.1, "window_delta_norm": 0.6}, "absorcao"),   # buying absorbed, price not moving
    ({"extension_atr": 6.4}, "esticado"),
    ({"wick": 0.6}, "esticado"),
])
def test_falling_absorbed_or_stretched_assets_cannot_score(change, expected):
    v = good_values(**change)
    gates = v1.evaluate_gates(v, "favoravel", spec())
    assert v1.classify(v, gates, spec()) == expected


@pytest.mark.parametrize("field,value", [
    ("concentration", 0.8), ("volume_spike_max", 6.0), ("efficiency_short", 0.1),
    ("slippage_buy_pct", 0.5), ("cvd_slope", -0.1), ("ask_depth_1pct", None),
])
def test_micro_pump_chop_cost_and_missing_inputs_fail_closed(field, value):
    v = good_values(**{field: value})
    gates = v1.evaluate_gates(v, "favoravel", spec())
    assert v1.classify(v, gates, spec()) != "subindo"


def test_regime_needs_relative_strength_when_market_is_not_favourable():
    assert v1.regime({"progress_atr": -0.5, "ret_pct": -0.4}, [-1, -0.5, 0.2], spec())["state"] == "desfavoravel"
    assert v1.regime({"progress_atr": 0.5, "ret_pct": 0.4}, [1, 0.5, -0.2], spec())["state"] == "favoravel"
    assert v1.regime(None, [1, 1], spec())["state"] == "desconhecido"
    weak = good_values(rs_atr=0.2)
    assert v1.classify(weak, v1.evaluate_gates(weak, "desfavoravel", spec()), spec()) == "neutro"
    strong = good_values(rs_atr=0.9)
    assert v1.classify(strong, v1.evaluate_gates(strong, "desfavoravel", spec()), spec()) == "subindo"
    unknown = v1.evaluate_gates(good_values(), "desconhecido", spec())
    assert unknown[-1]["result"] is None  # no BTC data: never a silent pass


def test_relative_strength_beta_one():
    assert v1.relative_strength_atr(0.9, 0.5, 0.4, 1.0) == pytest.approx(1.0)
    assert v1.relative_strength_atr(0.7, 0.5, 0.4, 1.4) == pytest.approx(0.0)
    assert v1.relative_strength_atr(None, 0.5, 0.4, 1.0) is None


def test_score_is_non_compensatory():
    strong = v1.strength_score(good_values(), {}, spec())
    broken = v1.strength_score(good_values(efficiency_short=0.0, consistency=0.0, higher_lows=0.0), {}, spec())
    blocks, weights = broken["ledger"]["blocks"], broken["ledger"]["weights"]
    arithmetic = 100 * sum(weights[k] * blocks[k] for k in weights) / sum(weights.values())
    # one dead block cannot be compensated: it is held at block_floor and still halves the score,
    # while a weighted sum of the same blocks would barely move
    assert strong["score"] > 50 and broken["score"] < strong["score"] / 2 and broken["score"] < arithmetic


def test_penalties_only_lower_the_score():
    base = v1.strength_score(good_values(extension_atr=0.5, slippage_buy_pct=0.05), {}, spec())["score"]
    stretched = v1.strength_score(good_values(extension_atr=2.9, slippage_buy_pct=0.19), {}, spec())["score"]
    assert stretched < base


def test_adaptive_normalisation_warms_up_then_uses_asset_percentile():
    s = spec(normalization__min_observations=3)
    stats = None
    for x in (0.1, 0.2, 0.3, 0.2):
        stats = v1.update_stats(stats, x, s)
    assert stats["n"] == 4 and stats["var"] > 0
    hi = v1.normalise("cvd_slope", 0.6, stats, s)
    assert hi["method"] == "asset_percentile" and hi["normalized"] > 0.9
    cold = v1.normalise("cvd_slope", 0.25, {"mean": 0.1, "var": 0.01, "n": 1}, s)
    assert cold["method"] == "absolute" and cold["normalized"] == pytest.approx(0.5)


# ── 5. Stability ─────────────────────────────────────────────────────────────

def run(states, steps, s=None):
    s = s or spec()
    st, out = states, []
    for i, (condition, raw) in enumerate(steps):
        st = v1.step_state(st, condition=condition, gates_ok=condition == "subindo", raw_score=raw,
                           fast_exit=False, minute_ms=(i + 1) * M1, spec=s)
        out.append(st["state"])
    return st, out


def test_entry_needs_consecutive_confirmations():
    s = spec(stability__ema_alpha=1.0)
    _, states = run(None, [("subindo", 80)] * 3, s)
    assert states == ["candidato", "candidato", "ativo"]


def test_one_weak_minute_does_not_drop_an_active_asset():
    s = spec(stability__ema_alpha=1.0)
    st, _ = run(None, [("subindo", 80)] * 3, s)
    st = v1.step_state(st, condition="neutro", gates_ok=False, raw_score=None, fast_exit=False,
                       minute_ms=10 * M1, spec=s)
    assert st["state"] == "enfraquecendo"
    st = v1.step_state(st, condition="subindo", gates_ok=True, raw_score=80, fast_exit=False,
                       minute_ms=11 * M1, spec=s)
    assert st["state"] == "ativo"


def test_soft_exit_needs_cycles_and_minimum_hold():
    s = spec(stability__ema_alpha=1.0, stability__min_hold_minutes=15)
    st, _ = run(None, [("subindo", 80)] * 3, s)          # active at minute 3
    for minute in range(4, 10):                           # weak for 6 minutes, held < 15
        st = v1.step_state(st, condition="neutro", gates_ok=False, raw_score=None, fast_exit=False,
                           minute_ms=minute * M1, spec=s)
    assert st["state"] == "enfraquecendo"
    st = v1.step_state(st, condition="neutro", gates_ok=False, raw_score=None, fast_exit=False,
                       minute_ms=18 * M1, spec=s)
    assert st["state"] == "fora" and st["exit_reason"] == "soft:weakening"


@pytest.mark.parametrize("condition,fast", [("caindo", False), ("esticado", False), ("neutro", True)])
def test_hard_invalidation_exits_immediately_and_starts_cooldown(condition, fast):
    s = spec(stability__ema_alpha=1.0, stability__cooldown_minutes=10)
    st, _ = run(None, [("subindo", 80)] * 3, s)
    st = v1.step_state(st, condition=condition, gates_ok=False, raw_score=None, fast_exit=fast,
                       minute_ms=4 * M1, spec=s)
    assert st["state"] == "fora" and st["exit_reason"].startswith("hard:")
    st = v1.step_state(st, condition="subindo", gates_ok=True, raw_score=90, fast_exit=False,
                       minute_ms=5 * M1, spec=s)
    assert st["state"] == "fora"  # cooldown
    st = v1.step_state(st, condition="subindo", gates_ok=True, raw_score=90, fast_exit=False,
                       minute_ms=15 * M1, spec=s)
    assert st["state"] == "candidato"


def test_a_pause_with_buying_is_soft_not_a_hard_exit():
    s = spec(stability__ema_alpha=1.0)
    st, _ = run(None, [("subindo", 80)] * 3, s)
    st = v1.step_state(st, condition="absorcao", gates_ok=False, raw_score=None, fast_exit=False,
                       minute_ms=4 * M1, spec=s)
    assert st["state"] == "enfraquecendo"


def test_state_steps_once_per_closed_minute():
    s = spec(stability__ema_alpha=1.0)
    st = v1.step_state(None, condition="subindo", gates_ok=True, raw_score=80, fast_exit=False,
                       minute_ms=M1, spec=s)
    again = v1.step_state(st, condition="subindo", gates_ok=True, raw_score=80, fast_exit=False,
                          minute_ms=M1, spec=s)
    assert again["in_count"] == st["in_count"] == 1


# ── Orchestration and engine selection ───────────────────────────────────────

def row(symbol, **values):
    cells = {k: {"value": v} for k, v in {
        "window_delta_norm": 0.3, "cvd_slope": 0.3, "price_progress_atr": 0.5, "price_extension_atr": 0.5,
        "estimated_slippage_buy_pct": 0.05, "ask_depth_usdt_1pct": 50_000.0, "price": 104.5, **values}.items()}
    cells["pump_monitor_score"] = {"value": 42.0, "color_state": None}
    return {"symbol": symbol, "indicators": cells, "pump_monitor_score": 42.0,
            "score_components": {"delta_norm": {"contribution": 1.0}}, "only_rising": False,
            "opportunity_source_values": {"atr": 0.5, "vwap": 104.0}}


def test_evaluate_universe_and_engine_display_keep_v0_contract_intact():
    up = candles([100.0] * 30 + [100 + 0.25 * i for i in range(1, 19)])
    btc = candles([200.0] * 40 + [200 + 0.1 * i for i in range(1, 9)])
    now = now_after(up)
    s = spec(stability__ema_alpha=1.0, stability__enter_score=1.0, stability__enter_cycles=1,
             gates__volume_spike_max=10.0)
    structures = {"AAA_USDT": v1.structure_metrics(up, s, now), "BTC_USDT": v1.structure_metrics(btc, s, now)}
    rows = [row("AAA_USDT")]
    state = {}
    out = v1.evaluate_universe(rows, structures, structures["BTC_USDT"], state, minute_ms=M1, spec=s)
    res = out["results"]["AAA_USDT"]
    assert out["regime"]["state"] == "favoravel"
    assert res["condition"] == "subindo", res["gates"]
    assert res["state"] == "ativo" and res["score"] is not None
    assert v1.members(out["results"]) == ["AAA_USDT"]

    v0_rows = deepcopy(rows)
    svc.apply_engines(v0_rows, out, "v0", s)
    assert v0_rows[0]["indicators"]["pump_monitor_score"]["value"] == 42.0
    assert v0_rows[0]["indicators"]["pump_score_v1"]["value"] == res["score"]

    svc.apply_engines(rows, out, "v1", s)
    shown = rows[0]["indicators"]["pump_monitor_score"]["value"]
    assert shown == res["score"] and rows[0]["only_rising"] is True
    # v0 contract untouched for research / continuity
    assert rows[0]["pump_monitor_score"] == 42.0 and rows[0]["indicators"]["pump_score_v0"]["value"] == 42.0


def test_sort_uses_displayed_cell_and_projects_v1_ledger():
    a, b = row("AAA_USDT"), row("BBB_USDT")
    a["indicators"]["pump_monitor_score"]["value"] = None    # v1 says not rising
    b["indicators"]["pump_monitor_score"]["value"] = 70.0
    for r in (a, b):
        r["score_v1"] = {"state": "fora", "condition": "neutro", "raw_score": None, "gates": [], "strength": None}
    envelope = {"rows": [a, b], "active_engine": "v1", "score_version": v1.VERSION,
                "generated_at": "2026-10-05T00:00:00+00:00"}
    out = svc.select_rows(envelope, limit=0, sort="pump_monitor_score", order="desc", only_rising=False,
                          columns=None, config=eng.DEFAULT_CONFIG)
    assert [r["symbol"] for r in out["rows"]] == ["BBB_USDT", "AAA_USDT"]  # null last, never zero
    assert out["rows"][0]["score_version"] == v1.VERSION and "estado" in out["rows"][0]["score_components"]


def test_engine_config_defaults_to_v0_validates_and_keeps_producer_hash():
    stored = {k: v for k, v in eng.DEFAULT_CONFIG.items() if k not in eng.ENGINE_ONLY_KEYS}
    cfg = eng.effective_config(stored)
    assert eng.active_engine(cfg) == "v0"
    from app.services.profile_runtime_config import canonical_hash
    assert cfg["_meta"]["producer_config_hash"] == canonical_hash(stored)  # no ML cohort split
    flipped = eng.effective_config({**stored, "engines": {"active": "v1"}})
    assert flipped["_meta"]["producer_config_hash"] == cfg["_meta"]["producer_config_hash"]
    assert flipped["_meta"]["config_hash"] != cfg["_meta"]["config_hash"]
    with pytest.raises(ValueError, match="engines.active"):
        eng.next_config_version(cfg, {"engines": {"active": "v9"}}, changed_by="t", now_iso="x")
    with pytest.raises(ValueError, match="stay_score"):
        eng.next_config_version(cfg, {"score_v1": {"stability": {"stay_score": 90}}}, changed_by="t", now_iso="x")
    ok = eng.next_config_version(cfg, {"engines": {"active": "v1"}}, changed_by="t", now_iso="x")
    assert ok["engines"]["active"] == "v1"


def test_candle_series_query_binds_datetime_not_text_interval():
    """asyncpg binds timestamptz/interval only from Python datetime/timedelta (prod incident 2026-10-05)."""
    import asyncio
    from datetime import datetime

    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class DB:
        async def execute(self, statement, params):
            captured.update(params)
            return Result()

    out = asyncio.run(svc._load_candle_series(DB(), ["AAA_USDT"], "5m", 48))
    assert out == {}
    assert isinstance(captured["since"], datetime) and captured["since"].tzinfo is not None
    assert all(not isinstance(v, str) or k == "tf" for k, v in captured.items() if k != "s")


def test_extension_uses_rolling_60min_vwap_of_closed_candles():
    closes = [100.0] * 36 + [100 + 0.25 * i for i in range(1, 13)]
    s = candles(closes, wick=0.2)  # realistic ranges: wicks wider than the net move per candle
    st = v1.structure_metrics(s, spec(), now_after(s))
    typical = [((max(o, c) + 0.21) + (min(o, c) - 0.01) + c) / 3
               for o, c in zip([closes[i - 1] for i in range(36, 48)], closes[36:48])]
    assert st["vwap"] == pytest.approx(sum(typical) / 12)
    assert st["extension_atr"] == pytest.approx((closes[-1] - st["vwap"]) / st["atr"])
    assert 0 < st["extension_atr"] < spec()["gates"]["extension_atr_max"]  # steady hour is not "stretched"


def test_participation_averages_recent_candles_not_only_the_last():
    vols = [100.0] * 45 + [150.0, 150.0, 30.0]  # last candle thin, previous two active
    s = candles([100.0] * 48, vols=vols)
    st = v1.structure_metrics(s, spec(), now_after(s))
    assert st["rvol_5m"] == pytest.approx((150 + 150 + 30) / 3 / 100)


def test_ema_restarts_from_the_first_passing_minute_after_being_out():
    s = spec(stability__ema_alpha=0.2, stability__enter_cycles=1, stability__enter_score=50)
    st = v1.step_state(None, condition="neutro", gates_ok=False, raw_score=None, fast_exit=False,
                       minute_ms=M1, spec=s)
    assert st["score_s"] is None and st["state"] == "fora"
    st = v1.step_state(st, condition="subindo", gates_ok=True, raw_score=70, fast_exit=False,
                       minute_ms=2 * M1, spec=s)
    assert st["score_s"] == 70 and st["state"] == "ativo"  # not dragged towards 0 by past minutes


def test_concentration_is_share_of_gross_up_movement_and_stays_bounded_on_noisy_paths():
    noisy = [100.0] * 42 + [100.0, 100.4, 100.1, 100.5, 100.2, 100.6, 100.7]  # zig-zag up, small net
    s = candles(noisy[-48:])
    st = v1.structure_metrics(s, spec(), now_after(s))
    ups = [0.4, 0.4, 0.4, 0.1]
    assert st["concentration"] == pytest.approx(max(ups) / sum(ups))
    assert 0 < st["concentration"] <= 1
