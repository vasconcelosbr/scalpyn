"""Fail-closed, read-only projection of a persisted L3 execution decision.

L3_PUBLIC_AUTHORIZATION_V1: membership is never execution authority. Publication
requires the latest decision, its immutable contract and its transactional outbox.
"""
from datetime import datetime, timedelta, timezone
import math

from sqlalchemy import and_, select, tuple_

from ..models.backoffice import DecisionLog, L3AuthorizationOutbox
from ..models.shadow_trade import ShadowTrade
from .l3_authorization_contract_v3 import canonical_hash


def utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def authorization_expiry(contract):
    """Earliest remaining feature lifetime; never extend a source's TTL."""
    evaluated = utc(contract.get("evaluated_at"))
    if evaluated is None:
        return None
    deadlines = []

    def visit(value):
        if isinstance(value, list):
            return all(visit(item) for item in value)
        if not isinstance(value, dict):
            return True
        feature = value.get("resolved_feature")
        if isinstance(feature, dict):
            try:
                ttl = float(value.get("max_age_seconds"))
                age = float(feature.get("age_seconds"))
                if not math.isfinite(ttl) or not math.isfinite(age) or ttl <= 0 or age < 0:
                    return False
            except (ValueError, TypeError):
                return False
            deadline = evaluated + timedelta(seconds=ttl - age)
            source = utc(feature.get("source_timestamp"))
            if source is not None:
                deadline = min(deadline, source + timedelta(seconds=ttl))
            deadlines.append(deadline)
        return all(visit(item) for key, item in value.items() if key != "resolved_feature")

    if not visit(contract.get("feature_evaluations") or []):
        return None
    return min(deadlines) if deadlines else None


def public_authorization(decision, event, shadow, *, watchlist_id, profile_version=None, now=None,
                          ignore_expiry=False):
    now = now or datetime.now(timezone.utc)
    contract = (decision.metrics or {}).get("l3_authorization_contract_v3") or {}
    if (decision.decision != "ALLOW" or contract.get("valid") is not True
            or contract.get("authorization_status") != "ALLOW"
            or contract.get("final_decision") != "ALLOW"
            or contract.get("contract_technical_decision") != "ALLOW"):
        return None
    lineage = contract.get("lineage") or {}
    if (str(lineage.get("watchlist_id")) != str(watchlist_id)
            or str(lineage.get("profile_id")) != str(decision.profile_id)):
        return None
    if profile_version is not None and utc(lineage.get("profile_version")) != utc(profile_version):
        return None
    body = dict(contract)
    digest = body.pop("authorization_contract_hash", None)
    if not digest or canonical_hash(body) != digest:
        return None
    if event is None or event.authorization_contract_hash != digest:
        return None
    payload = event.payload or {}
    if not (payload.get("shadow_creation_required") or payload.get("consolidation_required")):
        return None
    expiry = authorization_expiry(contract)
    evaluated = utc(contract.get("evaluated_at"))
    if expiry is None or evaluated is None or evaluated > now:
        return None
    # 2026-09-18 (part 3): ``ignore_expiry`` is for the public-visibility
    # floor only (see load_recently_authorized_l3_shadows) -- it never
    # applies to entry authorization. Every caller that gates shadow
    # creation/consolidation (pipeline_scan, the outbox service, trade
    # consolidation, on-demand publish) keeps calling authorization_expiry()
    # directly and is untouched by this flag.
    if not ignore_expiry and now >= expiry:
        return None
    result = payload.get("processing_result")
    if event.status == "PROCESSED" and result != "CREATED_OR_RECONCILED":
        return None
    shadow_status = (
        "STARTED" if result == "CREATED_OR_RECONCILED" else
        "RETRY" if event.status == "RETRY" else "PENDING"
    )
    if event.status not in {"PENDING", "RETRY", "PROCESSED"}:
        return None
    metrics = decision.metrics or {}
    from .l3_trade_consolidation import candidate_from_decision, candidate_rank_key
    ranked = candidate_from_decision(
        user_id=getattr(decision, "user_id", None), decision_id=decision.id,
        decision={"symbol": decision.symbol, "score": decision.score, "created_at": evaluated,
                  "direction": getattr(decision, "direction", None),
                  "timeframe": getattr(decision, "timeframe", None), "metrics": metrics},
        buy_threshold=payload.get("buy_threshold"), strong_buy_threshold=payload.get("strong_buy_threshold"),
        profile_id=decision.profile_id, profile_name=lineage.get("profile_name"),
        profile_version=profile_version, rules_snapshot=lineage.get("rules_snapshot"),
        watchlist_id=str(watchlist_id), watchlist_name=lineage.get("watchlist_name"),
        watchlist_level="L3", source_watchlist_id=lineage.get("source_watchlist_id"))
    # S0.3 (2026-09-17 shadow-trade collapse fix): membership in the public
    # feed was never execution authority by design (module docstring), but
    # nothing forced a caller to actually check that. A confirmed Shadow —
    # not just a valid, unexpired contract — is now required to call an
    # opportunity ``executable``; PENDING/RETRY stay tracking-only states.
    executable = shadow_status == "STARTED" and shadow is not None
    return {
        "decision_id": decision.id,
        "authorization_id": digest,
        "authorization_status": "ALLOW",
        "evaluated_at": evaluated.isoformat(),
        "expires_at": expiry.isoformat(),
        "shadow_status": shadow_status,
        "shadow_id": str(shadow.id) if shadow is not None else None,
        "executable": executable,
        "shadow_reason": result or ("SHADOW_RETRY_PENDING" if event.status == "RETRY" else None),
        "alpha_score": metrics.get("final_score") if metrics.get("final_score") is not None else decision.score,
        "current_price": metrics.get("price"),
        "_indicators": {key: value.get("value") if isinstance(value, dict) else value
                        for key, value in (metrics.get("indicators_snapshot") or {}).items()},
        "_trace": contract.get("feature_evaluations") or [],
        "_rank_key": candidate_rank_key(ranked),
    }


async def load_public_authorizations(db, *, user_id, candidates):
    """Latest decision first, THEN validity: an old ALLOW never masks BLOCK."""
    if not candidates:
        return {}
    pairs = {(item["profile_id"], item["symbol"]) for item in candidates}
    latest = (
        select(DecisionLog.id)
        .where(DecisionLog.user_id == user_id,
               tuple_(DecisionLog.profile_id, DecisionLog.symbol).in_(pairs))
        .distinct(DecisionLog.profile_id, DecisionLog.symbol)
        .order_by(DecisionLog.profile_id, DecisionLog.symbol,
                  DecisionLog.created_at.desc(), DecisionLog.id.desc())
    )
    rows = (await db.execute(
        select(DecisionLog, L3AuthorizationOutbox, ShadowTrade)
        .outerjoin(L3AuthorizationOutbox, L3AuthorizationOutbox.decision_id == DecisionLog.id)
        .outerjoin(
            ShadowTrade,
            and_(
                ShadowTrade.decision_id == DecisionLog.id,
                ShadowTrade.source == "L3",
            ),
        )
        .where(DecisionLog.id.in_(latest))
    )).all()
    by_pair = {
        (row.profile_id, row.symbol): (row, event, shadow)
        for row, event, shadow in rows
    }
    result = {}
    now = datetime.now(timezone.utc)
    for item in candidates:
        pair = by_pair.get((item["profile_id"], item["symbol"]))
        if pair is None:
            continue
        auth = public_authorization(*pair, watchlist_id=item["watchlist_id"],
                                    profile_version=item.get("profile_version"), now=now)
        # S0.3: only a confirmed Shadow makes an opportunity part of the
        # executable population. PENDING/RETRY (contract valid, Shadow not
        # yet confirmed) are real states worth surfacing elsewhere for
        # tracking, but must never reach the list a consumer buys from.
        if auth and auth.get("executable"):
            result[(item["watchlist_id"], item["symbol"])] = auth
    return result
