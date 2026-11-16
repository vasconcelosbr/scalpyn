"""Read-only profile classification. This module never grants execution authority."""
from datetime import datetime, timezone

from sqlalchemy import select, tuple_

from ..models.backoffice import DecisionLog
from .l3_public_authorization import authorization_expiry, utc
from .profile_runtime_config import canonical_profile_config_hash


def classify_profile_snapshot(row, profile_config, *, watchlist_id, profile_id,
                              profile_version, now=None):
    # PROFILE_DISPLAY_DATA_SEPARATION: only authored predicates may reject;
    # provenance, freshness, global policies and execution authority are separate.
    now = now or datetime.now(timezone.utc)
    result = {"status": "awaiting_data", "data_reasons": [], "trace": [],
              "executable": False, "execution_authority": False}

    def pending(reason):
        result["data_reasons"].append(reason)
        return result

    if row is None:
        return pending("EVALUATION_PENDING")
    lineage = row.get("lineage") or {}
    if (str(lineage.get("watchlist_id")) != str(watchlist_id)
            or str(lineage.get("profile_id")) != str(profile_id)
            or (profile_version is not None
                and utc(lineage.get("profile_version")) != utc(profile_version))):
        return pending("PROFILE_EVALUATION_OUTDATED")
    if row.get("profile_hash") != canonical_profile_config_hash(profile_config):
        return pending("PROFILE_EVALUATION_OUTDATED")
    evaluated = utc(row.get("evaluated_at"))
    if evaluated is None or evaluated > now:
        return pending("EVALUATION_TIME_INVALID")

    audits = row.get("sections") or {}
    rejected = False
    configured = []
    for name in ("filters", "signals", "entry_triggers"):
        conditions = [c for c in (profile_config.get(name) or {}).get("conditions", [])
                      if c.get("enabled", True) is not False]
        if not conditions:
            continue
        audit = audits.get(name) or {}
        if len(audit.get("conditions") or []) != len(conditions):
            return pending("PROFILE_EVALUATION_INCOMPLETE")
        configured.append(audit)
        if audit.get("passed") is False and not audit.get("contract_reject"):
            rejected = True
    block_audits = {str(b.get("id")): b for b in
                    (audits.get("block_rules") or {}).get("blocks", [])}
    for block in (profile_config.get("block_rules") or {}).get("blocks", []):
        if block.get("enabled", True) is False:
            continue
        key = str(block.get("id") or block.get("rule_id") or block.get("name"))
        audit = block_audits.get(key)
        if audit is None:
            return pending("PROFILE_EVALUATION_INCOMPLETE")
        if audit.get("status") == "NOT_NEEDED_FOR_BOOLEAN_RESULT":
            if rejected:
                continue
            return pending("PROFILE_EVALUATION_INCOMPLETE")
        expected = block.get("conditions")
        expected = [block] if expected is None else expected
        if len(audit.get("conditions") or []) != len(expected):
            return pending("PROFILE_EVALUATION_INCOMPLETE")
        configured.append(audit)
        if audit.get("matched") is True and not audit.get("contract_reject"):
            rejected = True

    for audit in configured:
        if audit.get("contract_reject"):
            result["data_reasons"].extend(audit.get("reason_codes") or ["DATA_UNAVAILABLE"])
        for condition in audit.get("conditions") or []:
            result["trace"].append(condition)
            status = condition.get("status")
            if status == "NOT_NEEDED_FOR_BOOLEAN_RESULT":
                continue
            if status not in {"PASS", "FAIL"}:
                result["data_reasons"].extend(condition.get("reason_codes") or ["DATA_UNAVAILABLE"])
                continue
            expiry = authorization_expiry({"evaluated_at": evaluated,
                                           "feature_evaluations": [condition]})
            if expiry is None:
                result["data_reasons"].append("FEATURE_AGE_UNKNOWN")
            elif now >= expiry:
                result["data_reasons"].append("FEATURE_TTL_EXPIRED")
    if result["data_reasons"]:
        result["data_reasons"] = list(dict.fromkeys(result["data_reasons"]))
        return result
    result["status"] = "rejected" if rejected else "approved"
    return result


async def load_profile_display_rows(db, *, user_id, watchlist_id):
    from .pipeline_live_candidates import _l3_symbol_universe_statement
    from .pool_service import load_radar_watchlist_eligibility
    rows = (await db.execute(_l3_symbol_universe_statement(
        user_id=user_id, l3_watchlist_id=watchlist_id))).mappings().all()
    memberships = await load_radar_watchlist_eligibility(
        db, user_id=user_id, watchlist_ids=[watchlist_id])
    if watchlist_id in memberships:
        rows = [r for r in rows if r['symbol'] in memberships[watchlist_id]]
    if not rows:
        return []
    contract = DecisionLog.metrics['l3_authorization_contract_v3']
    # Select compact immutable evidence, not the multi-megabyte feature registry.
    decisions = (await db.execute(select(
        DecisionLog.id, DecisionLog.symbol, DecisionLog.profile_id, DecisionLog.score,
        DecisionLog.metrics['price'].label('price'),
        contract['lineage'].label('lineage'),
        contract['evaluated_at'].label('evaluated_at'),
        contract['sections'].label('sections'),
        contract['profile_execution_contract']['profile_projection_hash'].label('profile_hash'),
    ).where(DecisionLog.user_id == user_id,
        tuple_(DecisionLog.profile_id, DecisionLog.symbol).in_(
            {(r['profile_id'], r['symbol']) for r in rows}),
        DecisionLog.event_type.notin_(['PROFILE_CONSOLIDATION', 'PROFILE_CONSOLIDATION_REJECTED'])
    ).distinct(DecisionLog.profile_id, DecisionLog.symbol)
     .order_by(DecisionLog.profile_id, DecisionLog.symbol,
               DecisionLog.created_at.desc(), DecisionLog.id.desc()))).mappings().all()
    latest = {(d['profile_id'], d['symbol']): d for d in decisions}
    now = datetime.now(timezone.utc)
    items = []
    for row in rows:
        decision = latest.get((row['profile_id'], row['symbol']))
        state = classify_profile_snapshot(decision, row['profile_config'] or {},
            watchlist_id=watchlist_id, profile_id=row['profile_id'],
            profile_version=row['profile_version'], now=now)
        items.append({**dict(row), **state,
            'decision_id': decision['id'] if decision else None,
            'evaluated_at': decision['evaluated_at'] if decision else None,
            'alpha_score': decision['score'] if decision else row['alpha_score'],
            'current_price': decision['price'] if decision and decision['price'] is not None else row['current_price']})
    return items
