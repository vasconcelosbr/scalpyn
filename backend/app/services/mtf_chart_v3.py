"""Versioned MTF calculation using only the user's frozen JSON contract."""

from collections import OrderedDict
from copy import deepcopy
import asyncio
import time
from uuid import UUID
from sqlalchemy import select
from ..models.profile import Profile
from ..schemas.mtf_rules import DURATIONS, all_conditions
from .profile_runtime_config import canonical_hash
from .mtf_chart_engine import (
    closed_candles,
    indicator_history,
    prepare_frame,
    evaluate,
    frame_zones,
    iso,
    preview_candles,
)
from .mtf_btc_context import frozen_frame
from .mtf_trigger_diagnostics import finalize_diagnostics
from .mtf_entry_band import candidate_entry_band
from .mtf_pump_flow import load_context
from .mtf_indicator_config import mtf_indicator_identity

_CACHE = OrderedDict()


def calculate_frames(raw, cfg, cutoff, *, forming_at=None):
    frames, series, errors = {}, {}, {}
    required = set(cfg.frame_atr.values()) | {
        key for key, i in cfg.indicators.items() if i.display
    }
    required |= {
        o.ref.split(".", 1)[1]
        for c in all_conditions(cfg.rules)
        if c.enabled
        for o in (c.left, c.right)
        if o.ref and o.ref.startswith("indicators.")
    }

    for tf in cfg.active_timeframes():
        try:
            candles = closed_candles(raw[tf], tf, cutoff) if forming_at is None else preview_candles(raw[tf], tf, forming_at)
            instances = {
                key: i
                for key, i in cfg.indicators.items()
                if i.enabled and i.timeframe == tf and key in required
            }
            grouped, history = {}, {}
            for key, instance in instances.items():
                group = canonical_hash(
                    {
                        "family": instance.family,
                        "parameters": instance.parameters,
                        "window": instance.sample_window,
                    }
                )
                if group not in grouped:
                    settings = {
                        instance.family: {"enabled": True, **instance.parameters}
                    }
                    if instance.family == "rsi":
                        settings["rsi"]["periods"] = [instance.parameters["period"]]
                    grouped[group] = indicator_history(
                        candles,
                        settings,
                        {instance.family},
                        window=instance.sample_window,
                        epoch_seconds=True,
                    )
                valid_windows = {
                    c["time"]: dict(start_at=candles[index + 1 - instance.sample_window]["time"],
                                    end_at=c["time"] + DURATIONS[tf], candle_count=instance.sample_window, scope="indicator_sample")
                    for index, c in enumerate(candles)
                    if index + 1 >= instance.sample_window
                    and c["time"] - candles[index + 1 - instance.sample_window]["time"]
                    == (instance.sample_window - 1) * DURATIONS[tf]
                }
                points = [
                    p
                    for p in grouped[group].get(instance.output, [])
                    if p["time"] in valid_windows
                ]
                configuration = instance.model_dump()
                series["indicators." + key] = [
                    dict(
                        p,
                        close_at=p["time"] + DURATIONS[tf],
                        timeframe=tf,
                        valid_until=p["time"]
                        + DURATIONS[tf] * (1 + cfg.stale_tf_multiplier),
                        configuration=configuration,
                        window=valid_windows[p["time"]],
                    )
                    for p in points
                ]
                history[key] = points
                if instance.display:
                    history.setdefault(instance.output, points)
            atr_key = cfg.frame_atr[tf]
            history["atr"] = history[atr_key]
            core = cfg.indicators[atr_key]
            frame = prepare_frame(
                candles,
                tf,
                cutoff,
                {},
                set(),
                cfg,
                window=core.sample_window,
                history={"atr": history["atr"]},
                forming_at=forming_at,
            )
            frame["indicators"].update(history)
            frame["indicator_sample_count"] = core.sample_window
            # Structural display uses the explicit MTF period and the same sample domain.
            ema_name = "ema" + str(cfg.structure_ema_period)
            if (
                ema_name not in frame["indicators"]
                and core.sample_window >= cfg.structure_ema_period
            ):
                frame["indicators"].update(
                    indicator_history(
                        candles,
                        {
                            "ema": {
                                "enabled": True,
                                "periods": [cfg.structure_ema_period],
                            }
                        },
                        {"ema"},
                        window=core.sample_window,
                    )
                )
            frames[tf] = frame
        except (ValueError, KeyError) as exc:
            errors[tf] = str(exc)
            frames[tf] = {
                "candles": [],
                "indicators": {},
                "pivots": [],
                "patterns": [],
                "atr": None,
            }
    return frames, series, errors


async def chart_data_v3(db, uid, symbol, profile_id, cfg, include_display):
    from . import mtf_chart_service as service
    from .market_data_service import market_data_service

    profile = None
    if profile_id:
        profile = await db.scalar(
            select(Profile).where(
                Profile.id == UUID(str(profile_id)),
                Profile.user_id == uid,
                Profile.is_active.is_(True),
            )
        )
        if profile is None:
            raise ValueError("PROFILE_NOT_FOUND")
    now = int(time.time())
    trigger = cfg.stage("trigger")
    cutoff = now - now % DURATIONS[trigger]
    tfs = cfg.active_timeframes()
    raw = dict(
        zip(
            tfs,
            await asyncio.gather(
                *(service.market_candles(symbol, tf, cfg) for tf in tfs)
            ),
        )
    )
    closes = [
        c["time"] + DURATIONS[trigger]
        for c in raw[trigger]
        if c.get("is_closed") and c["time"] + DURATIONS[trigger] <= cutoff
    ]
    if closes:
        cutoff = max(closes)
    identity = canonical_hash(cfg.engine_dump())
    key = canonical_hash(
        {
            "user": str(uid),
            "symbol": symbol,
            "config": cfg.model_dump(),
            "raw": raw,
            "cutoff": cutoff,
        }
    )
    cache_slot = (str(uid), symbol)
    cached = _CACHE.get(cache_slot)
    if cached is None or cached[0] != key:
        calculated = await asyncio.to_thread(calculate_frames, raw, cfg, cutoff)
        _CACHE[cache_slot] = (key, calculated)
        while len(_CACHE) > cfg.max_assets_per_watchlist:
            _CACHE.popitem(last=False)
    else:
        calculated = cached[1]
        _CACHE.move_to_end(cache_slot)
    frames, series, errors = deepcopy(calculated)
    btc_frames = {}
    for tf in dict.fromkeys(["4h", "1h", cfg.btc_trend_timeframe]):
        if symbol == "BTC_USDT":
            frame = frames[tf]
        else:
            btc_raw = await service.market_candles("BTC_USDT", tf, cfg)
            atr = cfg.indicators[cfg.frame_atr[tf]]
            frame = await asyncio.to_thread(
                prepare_frame,
                btc_raw,
                tf,
                cutoff,
                {"atr": {"enabled": True, **atr.parameters}},
                {"atr"},
                cfg,
                atr.sample_window,
            )
        btc_frames[tf] = frozen_frame(
            frame, tf, cutoff, cfg, self_context=symbol == "BTC_USDT"
        )
    btc = btc_frames[cfg.btc_trend_timeframe]["trend"]
    book = await market_data_service.fetch_orderbook_metrics(symbol)
    spread = (
        book.get("spread_pct")
        if book.get("market_data_source") in ("gate.io", "gate")
        else None
    )
    flow = await load_context(db, uid, symbol, iso(cutoff), cfg.pump_flow)
    analysis = evaluate(
        symbol, frames, cfg, cutoff, spread, btc, rule_series=series, flow=flow
    )
    if now - cutoff > cfg.stale_tf_multiplier * DURATIONS[trigger]:
        analysis["vetoes"].append("DATA_STALE:" + trigger)
        analysis["status"] = "NO_TRADE"
        analysis["score"]["total"] = 0
    analysis.update(
        observed_at_utc=iso(now),
        config_hash=identity,
        indicator_identity=mtf_indicator_identity(cfg, uid),
        profile_id=str(profile.id) if profile else None,
        profile_hash=canonical_hash(profile.config) if profile else None,
        source="gate.io",
        spread_pct=spread,
        btc_trend=btc,
        btc_trend_timeframe=cfg.btc_trend_timeframe,
        btc_context={
            "version": "mtf_btc_context_v1",
            "symbol": "BTC_USDT", "source": "gate.io", "market_type": "spot",
            "evaluated_at": iso(cutoff),
            "pivot_left": cfg.pivot_left, "pivot_right": cfg.pivot_right,
            "candle_limits": {tf: cfg.candle_limits[tf] for tf in btc_frames},
            "active_timeframe": cfg.btc_trend_timeframe,
            "veto_enabled": cfg.veto_filters.btc_downtrend,
            "exempt": symbol == "BTC_USDT",
            "frames": btc_frames,
        },
        data_errors=errors,
        unavailable_profile_indicators=[],
        pump_flow=flow,
        stage_timeframes=cfg.timeframes.model_dump(),
    )
    finalize_diagnostics(analysis, cfg, now, cutoff)
    analysis["entry_candidate"] = candidate_entry_band(analysis, frames, cfg, now)
    from .mtf_risk_audit import publication
    publication(analysis, cfg, now)
    data = {
        "symbol": symbol,
        "frames": frames,
        "analysis": analysis,
        "indicator_config": {k: v.model_dump() for k, v in cfg.indicators.items()},
        "market_context": book,
        "profile_config": profile.config if profile else None,
        "as_of": iso(now),
        "profile": (
            {
                "id": str(profile.id),
                "name": profile.name,
                "default_timeframe": (profile.config or {}).get("default_timeframe"),
            }
            if profile
            else None
        ),
    }
    if not include_display:
        data['rule_series'] = series
        data['replay_evaluated_at'] = now
        return data
    from .mtf_forming_preview import forming_preview
    preview_flow = await load_context(db, uid, symbol, iso(now), cfg.pump_flow)
    data['forming_preview'] = await asyncio.to_thread(forming_preview, symbol, raw, frames, cfg, now,
                                                     spread, btc, flow=preview_flow)
    for tf, frame in frames.items():
        frame["zones"] = (
            frame_zones(frame, tf, cfg, frames[trigger]["candles"][-1]["close"])
            if frames[trigger]["candles"] and frame.get("atr")
            else []
        )
        frame.update(
            candles=[
                {
                    **c,
                    "closed": c.get("is_closed") is True
                    and c["time"] + DURATIONS[tf] <= now,
                }
                for c in raw[tf]
            ],
            error=errors.get(tf),
            source="gate.io",
            stale=not frame["candles"]
            or now - (frame["candles"][-1]["time"] + DURATIONS[tf])
            > cfg.stale_tf_multiplier * DURATIONS[tf],
        )
    data["price_format"] = await service.pair_price_format(symbol, cfg)
    return data
