"""Atomic on-demand L3 evaluation, outbox and watchlist projection."""
from datetime import datetime, timezone

from sqlalchemy import select

from ..models.pipeline_watchlist import PipelineWatchlistAsset
from ..models.profile import Profile
from ..schemas.spot_engine_config import SpotEngineConfig
from .config_service import config_service
from .l3_on_demand_decisions import evaluate_on_demand_l3
from .l3_public_authorization import authorization_expiry
from .l3_trade_consolidation import selection_thresholds
from .score_engine import merge_score_config


async def publish_l3_watchlist(db, *, user_id, watchlist, symbols):
    from ..tasks.pipeline_scan import _persist_decision_logs

    # Scanner locks the same profile before writing its snapshot. Serialize the
    # on-demand producer too; an older approval must not overwrite a newer block.
    profile = (await db.execute(select(Profile).where(
        Profile.id == watchlist.profile_id, Profile.user_id == user_id,
        Profile.is_active.is_(True),
    ).with_for_update())).scalar_one_or_none()
    if profile is None:
        return []
    global_score = await config_service.get_config(db, "score", user_id)
    score = merge_score_config(global_score or {}, profile.config or {})
    spot = SpotEngineConfig.from_config_json(
        await config_service.get_config(db, "spot_engine", user_id) or {})
    decisions = await evaluate_on_demand_l3(
        db, user_id=user_id, watchlist=watchlist, symbols=list(symbols), score_config=score)
    now = datetime.now(timezone.utc)
    consolidate = bool(spot.scanner.l3_single_profile_per_symbol_enabled)
    buy, strong = selection_thresholds(
        profile_config=profile.config or {}, score_config=score,
        spot_buy_threshold=float(spot.scanner.buy_threshold_score),
        spot_strong_buy_threshold=float(spot.scanner.strong_buy_threshold))
    for decision in decisions:
        contract = (decision.get("metrics") or {}).get("l3_authorization_contract_v3") or {}
        if not contract or not (decision.get("metrics") or {}).get("l3_gate_v2"):
            raise ValueError("L3_ON_DEMAND_EVALUATION_ENVELOPE_MISSING")
        expiry = authorization_expiry(contract)
        authorized = (decision.get("decision") == "ALLOW" and contract.get("valid") is True
                      and contract.get("authorization_status") == "ALLOW"
                      and contract.get("contract_technical_decision") == "ALLOW"
                      and expiry is not None and expiry > now)
        minimum = float((getattr(watchlist, "filters_json", None) or {}).get("min_alpha_score") or 0)
        frozen_score = (decision.get("metrics") or {}).get("final_score", decision.get("score"))
        authorized = authorized and (minimum <= 0 or (frozen_score is not None and float(frozen_score) >= minimum))
        decision.update({
            "event_type": "L3_PUBLIC_AUTHORIZATION_EVALUATED",
            "_profile_id": profile.id, "_profile_name": profile.name,
            "_profile_version": profile.profile_version,
            "_shadow_creation_required": authorized and not consolidate,
            "_consolidation_required": authorized and consolidate,
            # Adjacent on-demand profile evaluations share a collection window.
            "_scan_run_id": f"on-demand:{user_id}:{int(now.timestamp()) // 120}",
            "_consolidation_rule_version": spot.scanner.l3_profile_consolidation_rule_version,
            "_buy_threshold": buy, "_strong_buy_threshold": strong,
        })
    persisted = await _persist_decision_logs(db, user_id, decisions)
    rows = (await db.execute(select(PipelineWatchlistAsset).where(
        PipelineWatchlistAsset.watchlist_id == watchlist.id))).scalars().all()
    existing = {row.symbol: row for row in rows}
    approved = []
    for decision in persisted:
        contract = decision["metrics"]["l3_authorization_contract_v3"]
        row = existing.get(decision["symbol"])
        authorized = decision["shadow_creation_required"] or decision["consolidation_required"]
        if not authorized:
            continue
        if row is None:
            row = PipelineWatchlistAsset(watchlist_id=watchlist.id, symbol=decision["symbol"], entered_at=now)
            db.add(row)
        metrics = decision["metrics"]
        row.current_price = metrics.get("price")
        row.price_change_24h = metrics.get("change_24h")
        row.volume_24h = metrics.get("volume_24h")
        row.market_cap = metrics.get("market_cap")
        row.alpha_score = metrics.get("final_score") if metrics.get("final_score") is not None else decision.get("score")
        row.refreshed_at = now
        row.level_change_at = now if row.level_direction == "down" or row.level_direction is None else row.level_change_at
        row.level_direction = "up"
        row.analysis_snapshot = {
            "status": "approved", "stage": "L3", "symbol": row.symbol,
            "profile_id": str(profile.id), "timestamp": contract["evaluated_at"],
            "decision_id": decision["id"],
            "authorization_id": contract["authorization_contract_hash"],
            "expires_at": authorization_expiry(contract).isoformat(),
            "evaluation_trace": contract.get("feature_evaluations") or [],
            "alpha_score": row.alpha_score,
        }
        approved.append(row)
    approved_symbols = {row.symbol for row in approved}
    for row in rows:
        if row.symbol not in approved_symbols:
            row.level_direction = "down"
            row.level_change_at = now
    watchlist.last_scanned_at = now
    # Decision, contract, outbox and snapshot either all commit or all roll back.
    await db.commit()
    return [{"symbol": row.symbol, "alpha_score": row.alpha_score,
             "current_price": row.current_price, "analysis_snapshot": row.analysis_snapshot}
            for row in approved]
