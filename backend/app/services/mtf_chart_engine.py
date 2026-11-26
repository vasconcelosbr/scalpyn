"""Pure price-action analysis. No database, exchange credentials or order APIs."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any
import pandas as pd

from ..schemas.mtf_chart import MTFChartConfig
from .feature_engine import FeatureEngine

from ..schemas.mtf_rules import DURATIONS
TF_SECONDS = DURATIONS
ENGINE_VERSION = "mtf_chart_v1"


def timestamp(value) -> int:
    return int(pd.Timestamp(value).timestamp())


def iso(seconds: int | float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def closed_candles(candles: list[dict], timeframe: str, as_of: int) -> list[dict]:
    result = []
    for c in sorted(candles, key=lambda c: c["time"]):
        if c["time"] + TF_SECONDS[timeframe] > as_of or c.get("is_closed") is False:
            continue
        values = [c.get(key) for key in ("open", "high", "low", "close", "volume")]
        if any(v is None or not isfinite(v) for v in values):
            raise ValueError("INVALID_OHLCV")
        if min(values[:4]) <= 0 or c["volume"] < 0 or c["high"] < max(c["open"], c["close"]) or c["low"] > min(c["open"], c["close"]) or c["low"] > c["high"]:
            raise ValueError("INVALID_OHLCV")
        if result and c["time"] - result[-1]["time"] != TF_SECONDS[timeframe]:
            raise ValueError("CANDLE_GAP_OR_DUPLICATE")
        result.append(c)
    return result


def patterns(c: dict, previous: dict | None, atr: float | None, cfg: MTFChartConfig) -> list[dict]:
    from .mtf_trigger_diagnostics import pattern_conditions, passed
    rules = pattern_conditions(c, previous, atr, cfg)
    body = abs(c['close']-c['open'])
    return [{'time': c['time'], 'pattern': name,
             'metrics': {'body': body, 'range': c['high']-c['low'],
                         'upper_wick': c['high']-max(c['open'], c['close']),
                         'lower_wick': min(c['open'], c['close'])-c['low'], 'atr': atr}}
            for name, conditions in rules.items() if passed(conditions)]


def pivots(candles: list[dict], timeframe: str, cfg: MTFChartConfig) -> list[dict]:
    result = []
    prior = {}
    for i in range(cfg.pivot_left, len(candles) - cfg.pivot_right):
        c = candles[i]
        neighbours = candles[i-cfg.pivot_left:i] + candles[i+1:i+cfg.pivot_right+1]
        for key, kind in (("high", "H"), ("low", "L")):
            confirmed = all(c[key] > n[key] for n in neighbours) if kind == "H" else all(c[key] < n[key] for n in neighbours)
            if not confirmed:
                continue
            label = kind if kind not in prior else ("H" if c[key] > prior[kind] else "L") + kind
            result.append({"time": c["time"], "price": c[key], "kind": kind, "label": label,
                           "confirmed_at": candles[i+cfg.pivot_right]["time"] + TF_SECONDS[timeframe]})
            prior[kind] = c[key]
    return result


def trend(points: list[dict]) -> str:
    highs = [p for p in points if p["kind"] == "H"]
    lows = [p for p in points if p["kind"] == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return "RANGE"
    if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
        return "UP"
    if highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
        return "DOWN"
    return "RANGE"


def zones(points: list[dict], atr: float, cfg: MTFChartConfig, reference: float | None = None) -> list[dict]:
    result = []
    for kind, zone_type in (("H", "RESISTANCE"), ("L", "SUPPORT")):
        clusters: list[list[dict]] = []
        for point in sorted((p for p in points if p["kind"] == kind), key=lambda p: p["price"]):
            if not clusters or point["price"] - clusters[-1][0]["price"] > cfg.zone_atr_mult * atr:
                clusters.append([point])
            else:
                clusters[-1].append(point)
        for group in clusters:
            if (cfg.schema_version == 'mtf_chart_v2' or cfg.zone_algorithm == 'mtf_chart_v2') or len(group) >= cfg.zone_min_touches:
                center = sum(p["price"] for p in group) / len(group)
                result.append({"type": zone_type, "low": center-cfg.zone_atr_mult*atr/2,
                               "high": center+cfg.zone_atr_mult*atr/2, "touches": len(group),
                               "available_at": max(p["confirmed_at"] for p in group),
                               **({"origins": [zone_type], "pivot_times": sorted({p['time'] for p in group})}
                                  if (cfg.schema_version == 'mtf_chart_v2' or cfg.zone_algorithm == 'mtf_chart_v2') else {})})
    if (cfg.schema_version == 'mtf_chart_v2' or cfg.zone_algorithm == 'mtf_chart_v2') and reference is not None:
        merged = []
        for zone in sorted(result, key=lambda z: z['low']):
            if merged and zone['low'] - merged[-1]['high'] <= cfg.zone_atr_mult * atr:
                prior = merged[-1]
                prior['high'] = max(prior['high'], zone['high'])
                prior['available_at'] = max(prior['available_at'], zone['available_at'])
                prior['origins'] = sorted(set(prior['origins'] + zone['origins']))
                prior['pivot_times'] = sorted(set(prior['pivot_times'] + zone['pivot_times']))
                prior['touches'] = len(prior['pivot_times'])
            else:
                merged.append(dict(zone))
        for zone in merged:
            zone['touches'] = len(zone['pivot_times'])
            zone['type'] = 'SUPPORT' if zone['high'] < reference else 'RESISTANCE' if zone['low'] > reference else 'TEST'
            zone['reference_price'] = reference
        return [zone for zone in merged if zone['touches'] >= cfg.zone_min_touches]
    return result


def frame_zones(frame, tf, cfg, reference):
    points = frame['pivots']
    if (cfg.schema_version == 'mtf_chart_v2' or cfg.zone_algorithm == 'mtf_chart_v2'):
        candles = frame['candles'][-cfg.zone_lookback_candles[tf]:]
        points = [p for p in points if candles and p['time'] >= candles[0]['time']
                  and p['confirmed_at'] <= candles[-1]['time'] + TF_SECONDS[tf]]
    return zones(points, frame['atr'], cfg, reference) if frame.get('atr') else []


def indicator_history(candles: list[dict], config: dict, families: set[str], window: int | None = None, *, epoch_seconds: bool = False) -> dict[str, list[dict]]:
    """Exact prefix evaluation of existing functions; no parallel TA formulas."""
    engine = FeatureEngine(config)
    df = pd.DataFrame(candles)
    series: dict[str, list[dict]] = {}
    if epoch_seconds and not df.empty:
        df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    if df.empty:
        return series
    for family in sorted(families | {"atr"}):
        setting = config.get(family)
        calculate = getattr(engine, f"_calc_{family}", None)
        if not setting or not setting.get("enabled") or not calculate:
            continue
        # A config must explicitly declare parameters; do not inherit the
        # legacy FeatureEngine's fallback periods in this fail-closed consumer.
        required = {"ema": ("periods",), "rsi": ("period",), "adx": ("period",),
                    "atr": ("period",), "macd": ("fast", "slow", "signal"),
                    "bollinger": ("period", "deviation"), "stochastic": ("k", "d", "smooth")}.get(family, ())
        if any(k not in setting for k in required):
            raise ValueError(f"INDICATOR_CONFIG_REQUIRED:{family}")
        for i in range(1, len(candles)):
            if window is not None and i+1 < window:
                continue
            # Warmup is represented as a missing sample, never zero.
            if family == "adx" and i+1 < int(setting["period"])*2:
                continue
            try:
                prefix = df.iloc[max(0, i+1-window):i+1] if window else df.iloc[:i+1]
                values = calculate(prefix)
            except (IndexError, ValueError, KeyError):
                continue
            for key, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
                    series.setdefault(key, []).append({"time": candles[i]["time"], "value": value})
    return series


def preview_candles(candles, tf, now):
    """UI-only working copy; never mark source candles closed or persist this domain."""
    duration = TF_SECONDS[tf]
    domain = [dict(c, is_closed=True) if c['time'] <= now < c['time'] + duration else dict(c)
              for c in candles if c['time'] <= now]
    return closed_candles(domain, tf, now - now % duration + duration)


def prepare_frame(candles, tf, as_of, indicator_config, families, cfg, window=None, history=None, *, forming_at=None):
    closed = closed_candles(candles, tf, as_of) if forming_at is None else preview_candles(candles, tf, forming_at)
    if history is None:
        series = indicator_history(closed, indicator_config, families, window=window)
    else:
        # Supplied samples use the same fixed producer window. Restrict their
        # availability to this frame's original warmup/domain before evaluation.
        ready = closed[window-1]['time'] if window and len(closed) >= window else None
        end = closed[-1]['time'] if closed else None
        series = {name: [dict(p) for p in points if ready is not None and ready <= p['time'] <= end]
                  for name, points in history.items() if name != 'volume_ma'}
        series = {name: points for name, points in series.items() if points}
    series["volume_ma"] = [{"time": closed[i]["time"], "value": sum(c["volume"] for c in closed[i-cfg.vol_lookback+1:i+1])/cfg.vol_lookback}
                           for i in range(cfg.vol_lookback-1, len(closed))]
    atr_map = {p["time"]: p["value"] for p in series.get("atr", [])}
    marks = [p for i, c in enumerate(closed) for p in patterns(c, closed[i-1] if i else None, atr_map.get(c["time"]), cfg)]
    points = pivots(closed, tf, cfg)
    # Shooting star requires context; a bearish pin by itself is not one.
    for mark in list(marks):
        if mark["pattern"] == "PIN_BEAR" and trend([p for p in points if p["confirmed_at"] <= mark["time"]]) == "UP":
            marks.append({**mark, "pattern": "SHOOTING_STAR"})
    return {"candles": closed, "indicators": series, "patterns": marks, "pivots": points,
            "atr": atr_map.get(closed[-1]["time"]) if closed else None}


def evaluate(symbol: str, frames: dict, cfg: MTFChartConfig, as_of: int, spread_pct: float | None, btc_trend: str | None, *, rule_series=None, flow=None, preview_at=None) -> dict[str, Any]:
    structure_tf, zone_tf, trigger_tf = (cfg.stage(k) for k in ('structure','zone','trigger'))
    result: dict[str, Any] = {"symbol": symbol, "market": "spot", "mode": "SHADOW", "operational_effect": False,
        "engine_version": cfg.schema_version, "evaluated_at_utc": iso(as_of), "candle_closed_5m": None,
        "context_4h": {"trend": "RANGE", "last_pivots": [], "strength": "NEUTRAL", "last_candle_pattern": None},
        "zone_1h": None, "zones": [], "trigger_5m": None, "plan": None,
        "score": {"total": 0, "raw": None, "maximum": sum(cfg.score_weights.values()), "components": {}, "calibrated": False},
        "vetoes": [], "status": "NO_TRADE", "explanation": []}
    from .mtf_trigger_diagnostics import build_diagnostics
    from .mtf_diagnostic_explanations import explain_diagnostics
    vetoes = result["vetoes"]
    closed_5m = frames.get(trigger_tf, {}).get("candles", [])
    if closed_5m:
        result["candle_closed_5m"] = iso(closed_5m[-1]["time"])
        result["candle_close_at_utc"] = iso(closed_5m[-1]["time"] + TF_SECONDS[trigger_tf])
    for tf in dict.fromkeys((structure_tf, zone_tf, trigger_tf)):
        frame = frames.get(tf, {})
        candles = frame.get("candles", [])
        if not candles or not frame.get("atr"):
            vetoes.append(f"DATA_MISSING:{tf}")
        elif as_of - (candles[-1]["time"] + TF_SECONDS[tf]) > cfg.stale_tf_multiplier * TF_SECONDS[tf]:
            vetoes.append(f"DATA_STALE:{tf}")
    if vetoes:
        result["explanation"] = ["SEM TRADE: candles fechados ou ATR indisponíveis/desatualizados."]
        result["trigger_diagnostics"] = build_diagnostics(result, frames, cfg, as_of, spread_pct, btc_trend)
        if cfg.context_audit.enabled:
            result['risk_audit'] = {'version': 'mtf_context_risk_v1', 'status': 'BLOCK', 'checks': [],
                                    'reasons': list(vetoes), 'confirmed_at': as_of, 'preview': preview_at is not None}
        explain_diagnostics(result, frames, cfg, as_of, preview=preview_at is not None)
        return result
    f4, f1, f5 = (frames[tf] for tf in (structure_tf, zone_tf, trigger_tf))
    c = f5["candles"][-1]
    result["candle_closed_5m"] = iso(c["time"])
    result["candle_close_at_utc"] = iso(c["time"] + TF_SECONDS[trigger_tf])
    direction = trend(f4["pivots"])
    last4 = [p["pattern"] for p in f4["patterns"] if p["time"] == f4["candles"][-1]["time"]]
    force = any(p in cfg.force_patterns for p in last4)
    result["context_4h"] = {"trend": direction, "last_pivots": [p["label"] for p in f4["pivots"][-4:]],
                            "last_candle_pattern": last4[0] if last4 else None, "strength": "FORCE" if force else "NEUTRAL"}
    zs = frame_zones(f1, zone_tf, cfg, c['close'])
    result["zones"] = zs
    supports = [z for z in zs if z["low"] <= c["close"]]
    supports = [z for z in supports if z["type"] == "SUPPORT" or
                (z['type'] == 'TEST' and 'SUPPORT' in z.get('origins', []))]
    zone = min(supports, key=lambda z: max(z["low"]-c["close"], c["close"]-z["high"], 0)) if supports else None
    rejection = False
    near = False
    if zone:
        distance = max(zone["low"]-c["close"], c["close"]-zone["high"], 0) / f1["atr"]
        near = distance <= cfg.zone_proximity_atr
        recent = f1["candles"][-cfg.rejection_lookback:]
        rejection_times = {x["time"] for x in recent if x["low"] <= zone["high"] and x["high"] >= zone["low"]}
        rejection = any(p["time"] in rejection_times and p["pattern"] in cfg.rejection_patterns for p in f1["patterns"])
        result["zone_1h"] = {**zone, "distance_atr": distance, "in_zone": near, "rejection": rejection}
    trigger = next((p for name in cfg.trigger_patterns for p in f5["patterns"] if p["time"] == c["time"] and p["pattern"] == name), None)
    preceding = f5["candles"][-cfg.vol_lookback-1:-1]
    average = sum(x["volume"] for x in preceding) / len(preceding) if len(preceding) == cfg.vol_lookback else None
    ratio = c["volume"] / average if average else None
    if trigger:
        result["trigger_5m"] = {**trigger, "type": "CONTINUATION" if trigger["pattern"] == "MARUBOZU_BULL" else "REVERSAL", "volume_ratio": ratio}
    if direction == "DOWN" and cfg.veto_filters.asset_downtrend:
        vetoes.append("SPOT_NOT_BUYING")
    elif direction == "RANGE" and cfg.veto_filters.asset_range:
        vetoes.append("TREND_RANGE")
    if spread_pct is None:
        vetoes.append("SPREAD_UNAVAILABLE")
    elif cfg.veto_filters.spread and spread_pct > cfg.max_spread_pct:
        vetoes.append("SPREAD_TOO_WIDE")
    if cfg.veto_filters.btc_downtrend and symbol != "BTC_USDT" and btc_trend is None:
        vetoes.append("BTC_CONTEXT_UNAVAILABLE")
    if cfg.veto_filters.btc_downtrend and symbol != "BTC_USDT" and btc_trend == "DOWN":
        vetoes.append("BTC_DOWNTREND")
    rule_checks, group_checks, extra_components = [], [], {}
    armed, confirmed = near, bool(trigger)
    if cfg.rules is not None:
        from .mtf_rules_engine import evaluate_group
        values = {'structure':{'trend':direction,'force':force},
                  'zone':{'near':near,'rejection':rejection,'distance_atr':distance if zone else None},
                  'trigger':{'present':bool(trigger),'pattern':trigger['pattern'] if trigger else ''},
                  'volume':{'ratio':ratio},'btc':{'trend':btc_trend},
                  'market':{'spread_pct':spread_pct},'flow':flow or {}}
        # Validate each referenced source independently, never use partial aggregates.
        values['flow'] = dict(flow or {})
        if values['flow'].get('confirmation') in ('unavailable','disabled'):
            values['flow']['confirmation'] = None
        values['flow']['asset'] = dict((flow or {}).get('asset') or {})
        if values['flow']['asset'].get('state') == 'unavailable': values['flow']['asset'] = {}
        values['flow']['capital'] = {tf: reading if reading.get('state') != 'unavailable' else {}
            for tf, reading in (flow or {}).get('capital', {}).items()}
        def run(root, role, key, points=None):
            trace = {}
            state, checks = evaluate_group(root, values, rule_series or {}, as_of, role=role, preview_at=preview_at, trace=trace)
            rule_checks.extend(checks)
            from .mtf_trigger_diagnostics import check
            row = check('rules:'+key, key, None if state=='UNAVAILABLE' else state=='PASS', '=', role!='veto', role=role, enabled=state!='DISABLED')
            row['rule_tree'] = trace
            if points is not None: row.update(points=points if state=='PASS' else 0, maximum_points=points)
            group_checks.append(row)
            if state == 'UNAVAILABLE': vetoes.append('RULE_DATA_UNAVAILABLE:'+checks[0]['id'])
            return state == 'PASS'
        armed = run(cfg.rules.armed, 'required', 'ARMADO')
        confirmed = run(cfg.rules.trigger, 'required', 'GATILHO')
        for index, root in enumerate(cfg.rules.vetoes):
            before = len(rule_checks)
            if run(root, 'veto', 'VETO '+str(index+1)): vetoes.append('RULE_VETO:'+rule_checks[before]['id'])
        for rule in cfg.rules.scoring:
            extra_components['rule:'+rule.id] = rule.points if run(rule.when, 'score', rule.id, rule.points) else 0
        result['rules_diagnostics'] = rule_checks
        result['stage_timeframes'] = cfg.timeframes.model_dump()
        result['stage_results'] = {'armed':armed,'trigger':confirmed}
    rr = None
    plan_inputs = None
    if confirmed and armed:
        entry = c["close"]
        stop = c["low"] - max(cfg.stop_buffer_atr * f5["atr"], entry*(spread_pct or 0)/100)
        if cfg.stop_sizing.enabled:
            stop = entry * (1 - cfg.stop_sizing.target_pct / cfg.stop_sizing.gross_rr / 100)
        targets = sorted({z["low"] for z in zs if z["type"] == "RESISTANCE" and z["low"] > entry})
        targets = targets or sorted({p["price"] for p in f1["pivots"] if p["kind"] == "H" and p["price"] > entry})
        plan_inputs = dict(stop=stop, stop_distance=entry-stop, target_count=len(targets))
        if stop <= 0 or stop >= entry:
            vetoes.append("INVALID_PLAN")
        elif cfg.veto_filters.stop_distance and entry-stop > cfg.max_stop_atr*f5["atr"]:
            vetoes.append("STOP_TOO_WIDE")
        if len(targets) < 2:
            vetoes.append("STRUCTURAL_TARGETS_UNAVAILABLE")
        else:
            fee = cfg.fee_per_side_pct / 100
            slip = cfg.slippage_pct / 100
            cost_entry = entry*(1+slip)*(1+fee)
            loss = cost_entry-stop*(1-slip)*(1-fee)
            plan_inputs["loss"] = loss
            reward = targets[0]*(1-slip)*(1-fee)-cost_entry
            rr = reward/loss if loss > 0 else None
            if rr is None:
                vetoes.append("INVALID_PLAN")
            elif cfg.veto_filters.risk_reward and rr < cfg.min_rr_net:
                vetoes.append("RR_BELOW_MIN")
            qty = min(cfg.capital_usdt/cost_entry, cfg.capital_usdt*cfg.risk_per_trade_pct/100/loss) if loss > 0 else None
            result["plan"] = {"side": "LONG", "entry_type": "MARKET_ON_CLOSE", "entry": entry,
                "stop": stop, "tp1": targets[0], "tp2": targets[1], "rr_net": rr,
                "rr_gross": (targets[0]-entry)/(entry-stop) if entry > stop else None,
                "quantity": qty, "notional_usdt": qty*entry if qty else None,
                "expires_after_candles": cfg.expires_after_candles,
                "expires_at": iso(as_of+cfg.expires_after_candles*TF_SECONDS[trigger_tf])}
            if cfg.stop_sizing.enabled:
                result['plan']['stop_sizing'] = {
                    'mode': 'gross_rr', 'reference_entry': entry,
                    'target_pct': cfg.stop_sizing.target_pct,
                    'reference_target': entry * (1 + cfg.stop_sizing.target_pct / 100),
                    'gross_rr': cfg.stop_sizing.gross_rr,
                    'stop_pct': cfg.stop_sizing.target_pct / cfg.stop_sizing.gross_rr,
                }
    flags = {"trend": direction == "UP", "force": force and direction == "UP", "zone": near,
             "rejection": rejection and near, "trigger": bool(trigger), "volume": ratio is not None and ratio >= cfg.vol_mult,
             "rr": rr is not None and rr >= cfg.rr_bonus_threshold}
    components = {k: cfg.score_weights[k] if v else 0 for k, v in flags.items()}
    components.update(extra_components)
    result["score"]["maximum"] += sum(r.points for r in cfg.rules.scoring) if cfg.rules else 0
    score = 0 if vetoes else sum(components.values())
    result["score"].update(total=score, raw=sum(components.values()), components=components)
    result["status"] = "NO_TRADE" if vetoes else "TRIGGERED" if armed and confirmed and result["plan"] and score >= cfg.min_score else "ARMED" if armed else "WAIT"
    result["explanation"] = [
        f"Estrutura do {structure_tf.upper()}: {dict(UP='alta', DOWN='baixa', RANGE='lateral')[direction]}; pivôs confirmados: {', '.join(result['context_4h']['last_pivots']) or 'indisponíveis'}.",
        f"Preço próximo do suporte do {zone_tf.upper()}." if near else f"Aguardando aproximação de uma zona confirmada do {zone_tf.upper()}.",
        f"Gatilho fechado: {trigger['pattern']} em {iso(c['time'])}." if trigger else "Sem gatilho de compra no último candle fechado.",
        "Plano experimental; stops e perdas pertencem somente à simulação.",
    ]
    result["trigger_diagnostics"] = build_diagnostics(result, frames, cfg, as_of, spread_pct, btc_trend,
        ratio=ratio, flags=flags, plan_inputs=plan_inputs)
    if cfg.rules is not None:
        result.update(context=result['context_4h'], zone=result['zone_1h'], trigger=result['trigger_5m'],
                      candle_closed= result['candle_closed_5m'])
        # Native proximity/pattern checks describe context, not mandatory custom rules.
        diagnostic = result['trigger_diagnostics']
        diagnostic['checks'] = [c for c in diagnostic['checks'] if c['id'] not in ('zone','trigger')]
        diagnostic['checks'].extend(group_checks)
        diagnostic['checks'].extend({**row, 'role':'context'} for row in rule_checks)
        result['explanation'].insert(0, 'Regras JSON: ARMADO '+str(armed)+'; GATILHO '+str(confirmed)+'.')
    from .mtf_risk_audit import finalize
    finalize(result, frames, cfg, as_of, preview=preview_at is not None)
    explain_diagnostics(result, frames, cfg, as_of, preview=preview_at is not None)
    return result


def simulate_step(state: dict, candle: dict, cfg: MTFChartConfig, hourly_close: float | None = None) -> dict | None:
    """Append-only event reducer. Never fills inside/before the decision candle."""
    trigger_tf = cfg.stage('trigger')
    s = dict(state)
    if s["status"] in ("TP", "SL", "EXPIRED", "UNRESOLVED") or candle["time"] <= s.get("last_candle", -1):
        return None
    if candle["time"] <= s["observed_at"]:
        return None
    expected = s["last_candle"]+TF_SECONDS[trigger_tf] if s["status"] == "OPEN" else (s["observed_at"]//TF_SECONDS[trigger_tf]+1)*TF_SECONDS[trigger_tf]
    if candle["time"] > expected and (s["status"] == "OPEN" or candle["time"] < s["expires_at"]):
        return {**s, "last_candle": candle["time"], "status": "UNRESOLVED", "reason": "CANDLE_GAP", "pnl_usdt": None, "pnl_pct": None}
    fee, slip = cfg.fee_per_side_pct/100, cfg.slippage_pct/100
    s["last_candle"] = candle["time"]
    if s["status"] == "PENDING":
        if candle["time"] >= s["expires_at"]:
            return {**s, "status": "EXPIRED", "reason": "ENTRY_EXPIRED", "pnl_usdt": 0, "pnl_pct": None}
        fill = candle["open"]*(1+slip)
        if fill <= s["stop"] or fill >= s["tp1"]:
            return {**s, "status": "EXPIRED", "reason": "GAP_INVALIDATED", "pnl_usdt": 0, "pnl_pct": None}
        risk = fill*(1+fee)-s["stop"]*(1-slip)*(1-fee)
        reward = s["tp1"]*(1-slip)*(1-fee)-fill*(1+fee)
        if risk <= 0 or (cfg.veto_filters.risk_reward and reward/risk < cfg.min_rr_net):
            return {**s, "status": "EXPIRED", "reason": "FILL_RR_BELOW_MIN", "pnl_usdt": 0, "pnl_pct": None}
        qty = min(cfg.capital_usdt/(fill*(1+fee)), cfg.capital_usdt*cfg.risk_per_trade_pct/100/risk)
        s.update(status="OPEN", fill=fill, filled_at=candle["time"], quantity=qty, remaining=qty,
                 invested=qty*fill*(1+fee), proceeds=0, partial=False, ambiguous=False)
    stop = s["stop"]
    hit_stop = candle["low"] <= stop
    target = s["tp2"] if s["partial"] else s["tp1"]
    hit_target = candle["high"] >= target
    s["ambiguous"] = s["ambiguous"] or (hit_stop and hit_target)
    if hit_stop:
        s["proceeds"] += s["remaining"]*min(stop, candle["open"])*(1-slip)*(1-fee)
        s.update(status="SL", remaining=0)
    elif hit_target:
        if not s["partial"]:
            sold = s["quantity"]*cfg.partial_tp1_pct/100
            s["proceeds"] += sold*s["tp1"]*(1-slip)*(1-fee)
            s["remaining"] -= sold
            s["partial"] = True
            # Fee/slippage adjusted breakeven applies from the NEXT candle.
            s["stop"] = s["fill"]*(1+fee)/((1-slip)*(1-fee))
        if candle["high"] >= s["tp2"]:
            s["proceeds"] += s["remaining"]*s["tp2"]*(1-slip)*(1-fee)
            s.update(status="TP", remaining=0)
    if s["status"] == "OPEN" and candle["time"]+TF_SECONDS[trigger_tf] >= s["filled_at"]+cfg.holding_candles*TF_SECONDS[trigger_tf]:
        s["proceeds"] += s["remaining"]*candle["close"]*(1-slip)*(1-fee)
        s.update(status="EXPIRED", remaining=0, reason="HOLDING_EXPIRED")
    if s["status"] == "OPEN" and hourly_close is not None and s.get("invalidation_zone_low") is not None and hourly_close < s["invalidation_zone_low"]:
        s["proceeds"] += s["remaining"]*candle["close"]*(1-slip)*(1-fee)
        s.update(status="EXPIRED", remaining=0, reason="ZONE_INVALIDATED")
    if s["status"] != "OPEN":
        s["pnl_usdt"] = s["proceeds"]-s["invested"]
        s["pnl_pct"] = 100*s["pnl_usdt"]/s["invested"]
    return s
