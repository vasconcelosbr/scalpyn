"""Read-side chart and isolated append-only MTF observation persistence."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import json
import logging
import math
import time
from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ..models.profile import Profile
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..schemas.mtf_chart import MTFChartConfig
from .config_service import config_service
from .profile_runtime_config import canonical_hash
from .mtf_indicator_config import effective_indicators
from .mtf_chart_engine import TF_SECONDS, ENGINE_VERSION, timestamp, prepare_frame, evaluate, trend, simulate_step, closed_candles, frame_zones

log = logging.getLogger(__name__)
_market_cache: OrderedDict = OrderedDict()
_frame_cache: OrderedDict = OrderedDict()
_chart_snapshots: OrderedDict = OrderedDict()
_pair_cache: tuple = (0, {})


async def load_config(db, user_id) -> MTFChartConfig:
    raw = await config_service.get_config(db, "mtf_analysis", user_id)
    return MTFChartConfig.model_validate(raw)


async def watchlist_options(db, user_id):
    rows = (await db.execute(select(PipelineWatchlist).where(
        PipelineWatchlist.user_id == user_id, PipelineWatchlist.market_mode == "spot"
    ).order_by(PipelineWatchlist.name))).scalars().all()
    return [{"id": "l3-consolidated", "name": "L3 Consolidado", "virtual": True}] + [
        {"id": str(w.id), "name": w.name, "profile_id": str(w.profile_id) if w.profile_id else None} for w in rows]


async def members(db, user_id, watchlist_id: str) -> list[dict]:
    # These canonical L3 handlers only read persisted authorization. Other
    # watchlists read snapshots directly: never invoke their auto-refresh GET.
    from ..api.watchlists import load_l3_internal_candidates, load_live_l3_candidates, _iso_utc
    if watchlist_id == "l3-consolidated":
        return (await load_l3_internal_candidates(user_id=user_id, db=db))["items"]
    try:
        wid = UUID(watchlist_id)
    except ValueError as exc:
        raise ValueError("WATCHLIST_NOT_FOUND") from exc
    wl = await db.scalar(select(PipelineWatchlist).where(
        PipelineWatchlist.id == wid, PipelineWatchlist.user_id == user_id,
        PipelineWatchlist.market_mode == "spot"))
    if wl is None:
        raise ValueError("WATCHLIST_NOT_FOUND")
    if wl.level == "L3":
        candidates = await load_live_l3_candidates(db, user_id=user_id)
        return [{"symbol": c.symbol, "profile_id": str(p.profile_id), "alpha_score": p.alpha_score,
                 "current_price": p.current_price,
                 "price_snapshot_at": _iso_utc(p.refreshed_at)}
                for c in candidates for p in c.contributors if p.watchlist_id == wid]
    rows = (await db.execute(select(PipelineWatchlistAsset).where(
        PipelineWatchlistAsset.watchlist_id == wid,
        (PipelineWatchlistAsset.level_direction.is_(None)) | (PipelineWatchlistAsset.level_direction == "up")
    ).order_by(PipelineWatchlistAsset.symbol))).scalars().all()
    return [{"symbol": r.symbol, "profile_id": str(wl.profile_id) if wl.profile_id else None,
             "current_price": float(r.current_price) if r.current_price is not None else None,
             "price_snapshot_at": r.refreshed_at.isoformat() if r.refreshed_at else None,
             "alpha_score": float(r.alpha_score) if r.alpha_score is not None else None} for r in rows]


async def display_members(db, user_id, watchlist_id: str) -> list[dict]:
    """Chart membership is profile approval, independent of Shadow admission."""
    if watchlist_id == 'l3-consolidated':
        from .l3_profile_consolidation import load_l3_profile_consolidation
        return (await load_l3_profile_consolidation(db, user_id=user_id))['items']
    return await members(db, user_id, watchlist_id)


async def profile_context(db, user_id, profile_id, include_display=True):
    profile = None
    if profile_id:
        profile = await db.scalar(select(Profile).where(Profile.id == UUID(str(profile_id)),
            Profile.user_id == user_id, Profile.is_active.is_(True)))
        if profile is None:
            raise ValueError("PROFILE_NOT_FOUND")
    from ..models.config_profile import ConfigProfile
    rows = (await db.execute(select(ConfigProfile).where(ConfigProfile.user_id == user_id,
        ConfigProfile.config_type == "indicators", ConfigProfile.pool_id.is_(None), ConfigProfile.is_active.is_(True)))).scalars().all()
    if len(rows) != 1 or not rows[0].config_json:
        raise ValueError("PROFILE_INDICATOR_CONFIG_UNAVAILABLE")
    indicator_config = rows[0].config_json
    identity = {"config_profile_id": str(rows[0].id), "config_user_id": str(user_id), "config_hash": canonical_hash(indicator_config)}
    from .mtf_observation_service import _required_indicator_names
    names = _required_indicator_names(profile.config or {}) if profile else set()
    families = set()
    for name in names:
        if name.startswith("ema"):
            families.add("ema")
        elif name.startswith(("di_", "adx")):
            families.add("adx")
        elif name.startswith("bb_"):
            families.add("bollinger")
        elif name.startswith("stoch_"):
            families.add("stochastic")
        else:
            families.add(name.split("_", 1)[0])
    if include_display:
        families |= {name for name in ("ema", "rsi", "adx") if (indicator_config.get(name) or {}).get("enabled")}
    from .feature_engine import FeatureEngine
    supported = {name for name in families if hasattr(FeatureEngine, f"_calc_{name}")}
    return profile, indicator_config, identity, supported, sorted(names)


async def market_candles(symbol, tf, cfg, limit=None):
    from .market_data_service import market_data_service
    limit = limit or cfg.candle_limits[tf]
    key = (symbol, tf, limit)
    cached = _market_cache.get(key)
    if cached and time.monotonic()-cached[0] < cfg.poll_seconds:
        _market_cache.move_to_end(key)
        return cached[1]
    incremental = cached and time.monotonic()-cached[0] < TF_SECONDS[tf]
    df = await market_data_service.fetch_ohlcv(symbol, tf, limit=2 if incremental else limit, gate_only=True)
    if df is None or df.empty or df.attrs.get("exchange") != "gate.io":
        return []
    rows = []
    for c in df.to_dict("records"):
        values = {key: float(c[key]) for key in ("open", "high", "low", "close", "volume")}
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError("INVALID_OHLCV")
        rows.append({"time": timestamp(c["time"]), "is_closed": bool(c.get("is_closed", False)), **values})
    if incremental:
        by_time = {c['time']: c for c in cached[1]}
        by_time.update({c['time']: c for c in rows})
        rows = sorted(by_time.values(), key=lambda c:c['time'])[-limit:]
    _market_cache[key] = (time.monotonic(), rows)
    # Bounded by the configured universe and three supported timeframes.
    while len(_market_cache) > cfg.max_assets_per_watchlist*len(TF_SECONDS):
        _market_cache.popitem(last=False)
    return rows


def calculation_window(config, tf):
    # Same sample windows as the owning producers; chart zoom/history limits
    # must not change EMA/RSI initialization or silently change pipeline values.
    from ..tasks.compute_indicators import _derive_min_candles, structural_5m_query_limit
    from ..tasks.compute_mtf_indicators import required_warmup_candles
    if tf == "1h":
        return required_warmup_candles(config, tf)
    if tf == "5m":
        return structural_5m_query_limit(config)
    return max(_derive_min_candles(config, tf), max(config.get("ema", {}).get("periods") or [0]))


async def prepared(raw, tf, cutoff, indicators, families, cfg, history=None):
    window = calculation_window(indicators, tf)
    closed = closed_candles(raw, tf, cutoff)
    key = canonical_hash({"candles": closed, "tf": tf, "indicators": indicators,
                          "families": sorted(families), "config": cfg.engine_dump(), "window": window})
    if key not in _frame_cache:
        _frame_cache[key] = await asyncio.to_thread(prepare_frame, closed, tf, cutoff, indicators, families, cfg, window, history)
        while len(_frame_cache) > cfg.max_assets_per_watchlist*len(TF_SECONDS)*2:
            _frame_cache.popitem(last=False)
    _frame_cache.move_to_end(key)
    result = deepcopy(_frame_cache[key])
    result["indicator_sample_count"] = window
    return result


async def chart_data(db, user_id, symbol, profile_id, cfg, include_display=True):
    from .market_data_service import market_data_service
    profile, indicators, identity, families, names = await profile_context(db, user_id, profile_id, include_display)
    indicators, identity = effective_indicators(indicators, identity, cfg)
    # Close snapshots first, then collect live context; observed_at is never
    # presented as a candle-close timestamp or used for historical fills.
    now = int(time.time())
    cutoff = now-now % TF_SECONDS["5m"]
    # MTF_DISPLAY_WARMUP: the added prefix is display-only. The evaluation
    # keeps its original candle domain and producer initialization window.
    full_raw = dict(zip(TF_SECONDS, await asyncio.gather(*(market_candles(symbol, tf, cfg,
        limit=min(1000, cfg.candle_limits[tf] + (calculation_window(indicators, tf) if include_display else 0))) for tf in TF_SECONDS))))
    raw = {tf: candles[-cfg.candle_limits[tf]:] for tf,candles in full_raw.items()}
    closes = [c["time"]+TF_SECONDS["5m"] for c in raw["5m"] if c.get("is_closed") and c["time"]+TF_SECONDS["5m"] <= cutoff]
    if closes:
        cutoff = max(closes)
    frames = {}
    display_frames = {}
    display_errors = {}
    errors = {}
    for tf in TF_SECONDS:
        try:
            display_frames[tf] = await prepared(full_raw[tf], tf, cutoff, indicators, families, cfg)
        except ValueError as exc:
            display_errors[tf] = str(exc)
        try:
            frames[tf] = await prepared(raw[tf], tf, cutoff, indicators, families, cfg,
                                        history=display_frames.get(tf, {}).get('indicators'))
        except ValueError as exc:
            errors[tf] = str(exc)
            frames[tf] = {"candles": [], "indicators": {}, "patterns": [], "pivots": [], "atr": None}
    btc_trend = None
    if symbol == "BTC_USDT":
        btc_trend = trend(frames["4h"]["pivots"])
    else:
        try:
            btc = await market_candles("BTC_USDT", "4h", cfg)
            btc_frame = await prepared(btc, "4h", cutoff, indicators, {"atr"}, cfg)
            if btc_frame["candles"] and cutoff-(btc_frame["candles"][-1]["time"]+TF_SECONDS["4h"]) <= cfg.stale_tf_multiplier*TF_SECONDS["4h"]:
                btc_trend = trend(btc_frame["pivots"])
        except ValueError:
            pass
    book = await market_data_service.fetch_orderbook_metrics(symbol)
    spread = book.get("spread_pct") if book.get("market_data_source") in ("gate.io", "gate") else None
    analysis = evaluate(symbol, frames, cfg, cutoff, spread, btc_trend)
    if now-cutoff > cfg.stale_tf_multiplier*TF_SECONDS["5m"]:
        analysis["vetoes"].append("DATA_STALE:5m")
        analysis["status"] = "NO_TRADE"
        analysis["score"]["total"] = 0
    analysis["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
    analysis["config_hash"] = canonical_hash(cfg.engine_dump())
    analysis["indicator_identity"] = identity
    analysis["profile_id"] = str(profile.id) if profile else None
    analysis["profile_hash"] = canonical_hash(profile.config) if profile else None
    analysis["source"] = "gate.io"
    analysis["spread_pct"] = spread
    analysis["btc_trend"] = btc_trend
    analysis["data_errors"] = errors
    unavailable = [name for name in names if not any(name in f["indicators"] for f in frames.values())]
    analysis["unavailable_profile_indicators"] = unavailable
    from .mtf_trigger_diagnostics import finalize_diagnostics
    finalize_diagnostics(analysis, cfg, now, cutoff)
    # MTF_PUMP_FLOW_SHADOW: additive frozen context, never passed to evaluate().
    from .mtf_pump_flow import load_context
    analysis['pump_flow'] = await load_context(db, user_id, symbol, analysis['candle_close_at_utc'] if analysis.get('candle_close_at_utc') else analysis['evaluated_at_utc'], cfg.pump_flow)
    if not include_display:
        # The scheduled observer persists analysis, never the drawing history.
        # Keep its original work/domain instead of adding UI-only computation.
        return {'symbol': symbol, 'frames': frames, 'analysis': analysis,
                'indicator_config': indicators, 'market_context': book,
                'profile_config': profile.config if profile else None,
                'as_of': analysis['observed_at_utc']}
    for tf, frame in frames.items():
        display_zones = frame_zones(frame, tf, cfg, frames['5m']['candles'][-1]['close']) if frames['5m']['candles'] and frame.get('atr') else []
        try:
            display = display_frames.get(tf)
            if display is None:
                raise ValueError(display_errors.get(tf, 'INSUFFICIENT_HISTORY'))
            first = raw[tf][0]['time'] if raw[tf] else now
            frame['indicators'] = {k: [p for p in v if p['time'] >= first] for k,v in display['indicators'].items()}
            frame['patterns'] = [p for p in display['patterns'] if p['time'] >= first]
            frame['warmup'] = {'required': calculation_window(indicators, tf),
                'available_prefix': sum(c['time'] < first for c in full_raw[tf]),
                'reason': None if sum(c['time'] < first for c in full_raw[tf]) >= calculation_window(indicators,tf)-1 else 'INSUFFICIENT_HISTORY'}
        except ValueError as exc:
            frame['warmup'] = {'reason': str(exc)}
        frame['zones'] = display_zones
        # Display the open candle without including it in any analysis.
        frame["candles"] = [{**c, "closed": c.get("is_closed") is True and c["time"]+TF_SECONDS[tf] <= now} for c in raw[tf]]
        frame["error"] = errors.get(tf)
        frame["source"] = "gate.io"
        frame["stale"] = not raw[tf] or now-(raw[tf][-1]["time"]+TF_SECONDS[tf]) > cfg.stale_tf_multiplier*TF_SECONDS[tf]
    price_format = await pair_price_format(symbol, cfg)
    return {"symbol": symbol, "frames": frames, "analysis": analysis, 'price_format': price_format,
            "profile": {"id": str(profile.id), "name": profile.name, "default_timeframe": (profile.config or {}).get("default_timeframe")} if profile else None,
            "indicator_config": indicators, "as_of": analysis["observed_at_utc"]}


async def pair_price_format(symbol, cfg):
    global _pair_cache
    if not cfg.presentation:
        return None
    try:
        if time.monotonic()-_pair_cache[0] >= cfg.presentation.pair_metadata_ttl_seconds:
            from ..exchange_adapters.gate_adapter import GateAdapter
            pairs = await GateAdapter('', '').list_spot_pairs()
            _pair_cache = (time.monotonic(), {p['id']: p for p in pairs})
        pair = _pair_cache[1].get(symbol)
        if pair is None or pair.get('precision') is None:
            return None
        precision = int(pair['precision'])
        if precision < 0 or precision > 18:
            return None
        return {'precision': precision, 'min_move': 10 ** -precision, 'source': 'gate.io'}
    except Exception:
        log.warning('MTF pair precision unavailable symbol=%s', symbol)
        return None


def chart_transport(data, *, context, since, max_entries):
    """Opaque cursor, owner/version scoped; replace overlay lists, upsert series.

    A cached old snapshot is only a delta base, never an authorization cache.
    Missing/evicted bases reset explicitly instead of silently losing candles.
    """
    identity = canonical_hash({'context': context, 'profile': data['analysis'].get('profile_hash'),
        'indicator': data['analysis'].get('indicator_identity'), 'config': data['analysis']['config_hash'],
        'engine': data['analysis']['engine_version']})
    old = _chart_snapshots.get(since)
    cursor = canonical_hash({'identity': identity, 'frames': data['frames'], 'as_of': data['as_of']})
    result = deepcopy(data)
    result.update(cursor=cursor, transport='full')
    overlapping = old and all(
        not frame.get('error') and bool({c['time'] for c in frame['candles']} &
             {c['time'] for c in old[1]['frames'].get(tf, {}).get('candles', [])})
        for tf, frame in data['frames'].items())
    if old and old[0] == identity and overlapping:
        result['transport'] = 'delta'
        for tf, frame in result['frames'].items():
            before = old[1]['frames'][tf]
            # Zones change only with closed-frame context; absent means retain,
            # while an explicit empty list removes the previous zones.
            if 'zones' in frame and frame['zones'] == before.get('zones'):
                del frame['zones']
            frame['window_start'] = frame['candles'][0]['time'] if frame['candles'] else None
            for field in ('candles',):
                previous = {p['time']: p for p in before[field]}
                frame[field] = [p for p in frame[field] if previous.get(p['time']) != p]
            for name, points in frame['indicators'].items():
                previous = {p['time']: p for p in before['indicators'].get(name, [])}
                frame['indicators'][name] = [p for p in points if previous.get(p['time']) != p]
            for field, discriminator in (('patterns','pattern'),('pivots','kind')):
                previous = {(p['time'],p[discriminator]):p for p in before[field]}
                current = {(p['time'],p[discriminator]):p for p in frame[field]}
                frame[field] = [p for key,p in current.items() if previous.get(key) != p]
                frame[field+'_removed'] = [list(key) for key in previous.keys()-current.keys()]
    _chart_snapshots[cursor] = (identity, deepcopy(data))
    _chart_snapshots.move_to_end(cursor)
    while len(_chart_snapshots) > max_entries:
        _chart_snapshots.popitem(last=False)
    return result


async def latest_evaluations(db, user_id, watchlist_id):
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (symbol) payload FROM mtf_chart_evaluations
        WHERE user_id=:uid AND watchlist_id=:wid ORDER BY symbol, observed_at DESC
    """), {"uid": user_id, "wid": watchlist_id})).mappings().all()
    return {r["payload"]["symbol"]: r["payload"] for r in rows}


async def persist_evaluation(db, user_id, watchlist_id, data, cfg):
    payload = dict(data["analysis"])
    if not payload.get("candle_closed_5m"):
        return False
    identity = canonical_hash({k: payload.get(k) for k in (
        "symbol", "profile_id", "profile_hash", "config_hash", "engine_version", "candle_closed_5m")}|{
        "user_id": str(user_id), "watchlist_id": watchlist_id, "indicator_identity": payload["indicator_identity"]})
    eid = uuid4()
    payload["evaluation_id"] = str(eid)
    payload["watchlist_id"] = watchlist_id
    created = await db.scalar(text("""INSERT INTO mtf_chart_evaluations
        (id,user_id,watchlist_id,symbol,identity,observed_at,payload,config_snapshot)
        VALUES (:id,:uid,:wid,:symbol,:identity,clock_timestamp(),CAST(:payload AS jsonb),CAST(:config AS jsonb))
        ON CONFLICT (identity) DO NOTHING RETURNING id"""),
        {"id": eid, "uid": user_id, "wid": watchlist_id, "symbol": payload["symbol"], "identity": identity,
         "payload": json.dumps(payload, allow_nan=False), "config": json.dumps(cfg.model_dump())})
    if not created or payload["status"] != "TRIGGERED":
        return bool(created)
    # User-level worker advisory lock serializes this check and event insertion.
    active = await db.scalar(text("""SELECT EXISTS(SELECT 1 FROM (
        SELECT DISTINCT ON (e.id) s.state FROM mtf_chart_evaluations e
        JOIN mtf_chart_shadow_events s ON s.evaluation_id=e.id
        WHERE e.user_id=:uid AND e.symbol=:symbol ORDER BY e.id,s.id DESC
        ) x WHERE x.state->>'status' IN ('PENDING','OPEN'))"""), {"uid": user_id, "symbol": payload["symbol"]})
    if not active:
        plan = payload["plan"]
        observed = timestamp(payload["observed_at_utc"])
        state = {**plan, "status": "PENDING", "observed_at": observed,
                 "invalidation_zone_low": (payload.get("zone_1h") or {}).get("low"),
                 "expires_at": timestamp(plan["expires_at"]), "last_candle": -1,
                 "pnl_usdt": None, "pnl_pct": None}
        await append_shadow(db, eid, state)
    return True


async def append_shadow(db, eid, state):
    await db.execute(text("""INSERT INTO mtf_chart_shadow_events(evaluation_id,candle_time,state)
        VALUES (:id,:time,CAST(:state AS jsonb)) ON CONFLICT (evaluation_id,candle_time) DO NOTHING"""),
        {"id": eid, "time": state["last_candle"], "state": json.dumps(state, allow_nan=False)})


async def advance_shadows(db, user_id):
    rows = (await db.execute(text("""SELECT * FROM (
        SELECT DISTINCT ON (e.id) e.id,e.symbol,e.config_snapshot,s.state
        FROM mtf_chart_evaluations e JOIN mtf_chart_shadow_events s ON s.evaluation_id=e.id
        WHERE e.user_id=:uid ORDER BY e.id,s.id DESC
        ) x WHERE state->>'status' IN ('PENDING','OPEN')"""), {"uid": user_id})).mappings().all()
    for row in rows:
        cfg = MTFChartConfig.model_validate(row["config_snapshot"])
        candles = closed_candles(await market_candles(row["symbol"], "5m", cfg), "5m", int(time.time()))
        hourly = closed_candles(await market_candles(row["symbol"], "1h", cfg), "1h", int(time.time()))
        hourly_closes = {c["time"]+TF_SECONDS["1h"]: c["close"] for c in hourly}
        state = row["state"]
        for candle in candles:
            next_state = simulate_step(state, candle, cfg, hourly_closes.get(candle["time"]+TF_SECONDS["5m"]))
            if next_state is not None:
                await append_shadow(db, row["id"], next_state)
                state = next_state


async def run_cycle():
    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    count = 0
    async with CeleryAsyncSessionLocal() as db:
        users = (await db.execute(select(ConfigProfile.user_id).where(
            ConfigProfile.config_type == "mtf_analysis", ConfigProfile.is_active.is_(True),
            ConfigProfile.pool_id.is_(None)))).scalars().all()
    for user_id in users:
        async with CeleryAsyncSessionLocal() as db:
            try:
                locked = await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": f"mtf-chart:{user_id}"})
                if not locked:
                    continue
                cfg = await load_config(db, user_id)
                # Continue existing simulations even when new observations stop.
                await advance_shadows(db, user_id)
                await db.commit()
                if cfg.enabled:
                    for wid in cfg.monitored_watchlists:
                        items = await members(db, user_id, wid)
                        for item in items[:cfg.max_assets_per_watchlist]:
                            try:
                                async with db.begin_nested():
                                    locked = await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": f"mtf-chart:{user_id}"})
                                    if not locked:
                                        continue
                                    data = await chart_data(db, user_id, item["symbol"], item.get("profile_id"), cfg, include_display=False)
                                    count += await persist_evaluation(db, user_id, wid, data, cfg)
                                await db.commit()
                            except Exception:
                                await db.rollback()
                                log.exception("MTF chart observation failed symbol=%s", item["symbol"])
                await db.commit()
            except Exception:
                await db.rollback()
                log.exception("MTF chart cycle failed user=%s", user_id)
    return {"evaluations_created": count, "mode": "SHADOW", "operational_effect": False}
