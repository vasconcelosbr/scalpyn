"""Refresh authored-rule evidence for candidates removed by pre-L3 gates."""
from datetime import datetime, timezone

from .l3_authorization_contract_v3 import build_authorization_contract


def build_rejected_profile_audits(assets, passed, *, profile_config, watchlist,
                                profile_meta, evaluated_at=None):
    # L3_PRE_GATE_PROFILE_AUDIT: exclusion from execution must not exclude the
    # current profile diagnostic. Reuse prepared inputs; no MTF/ML/flow fetches.
    accepted = {asset.get("symbol") for asset in passed}
    now = evaluated_at or datetime.now(timezone.utc)
    decisions = []
    for asset in assets:
        if asset.get("symbol") in accepted:
            continue
        contract = build_authorization_contract(
            asset=asset, profile_config=profile_config or {},
            legacy_decision="BLOCK", evaluated_at=now,
            profile_id=watchlist.profile_id, profile_name=profile_meta.get("name"),
            profile_version=profile_meta.get("version"), watchlist_id=watchlist.id,
            watchlist_name=watchlist.name, watchlist_level=watchlist.level,
            source_watchlist_id=watchlist.source_watchlist_id,
            market_type="futures" if asset.get("is_futures") else "spot",
        )
        decisions.append({
            "symbol": asset["symbol"], "strategy": "L3",
            "timeframe": (profile_config or {}).get("default_timeframe"),
            "decision": "BLOCK", "l3_pass": False,
            "score": asset.get("_score", asset.get("alpha_score")),
            "direction": "NEUTRAL" if asset.get("is_futures") else "SPOT",
            "event_type": "L3_REJECTED_PROFILE_AUDIT",
            "reasons": {"pre_l3_gate": "Excluded before operational L3 evaluation"},
            "metrics": {"l3_authorization_contract_v3": contract,
                        "indicators_snapshot": asset.get("indicators") or {},
                        "price": asset.get("price")},
            "created_at": now, "_profile_id": watchlist.profile_id,
            "_profile_name": profile_meta.get("name"),
            "_profile_version": profile_meta.get("version"),
            "_shadow_creation_required": False, "_consolidation_required": False,
        })
    return decisions
