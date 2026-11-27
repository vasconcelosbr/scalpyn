"""Indexed latest-per-pair lookup shared by display and authorization reads."""

from sqlalchemy import column, false, select, true, values

from ..models.backoffice import DecisionLog


def latest_profile_decision_ids(*, user_id, pairs):
    # LATEST_PROFILE_INDEXED_PROBES: stop at the newest eligible row per pair.
    # DISTINCT ON over the complete history encouraged a parallel bitmap scan
    # and exhausted the container's dynamic shared memory under concurrent reads.
    # Keep JSON extraction outside this narrow lookup. Do not filter validity or
    # ALLOW here: the latest BLOCK/missing evidence must supersede old approval.
    pairs = sorted(set(pairs), key=lambda pair: (str(pair[0]), pair[1]))
    if not pairs:
        return select(DecisionLog.id).where(false())
    requested = values(
        column('profile_id', DecisionLog.profile_id.type),
        column('symbol', DecisionLog.symbol.type),
        name='requested_profile_pairs',
    ).data(pairs)
    newest = (
        select(DecisionLog.id)
        .where(
            DecisionLog.user_id == user_id,
            DecisionLog.profile_id == requested.c.profile_id.cast(DecisionLog.profile_id.type),
            DecisionLog.symbol == requested.c.symbol,
            DecisionLog.event_type.notin_(
                ('PROFILE_CONSOLIDATION', 'PROFILE_CONSOLIDATION_REJECTED')),
        )
        .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        .limit(1)
        .correlate(requested)
        .lateral('latest_for_pair')
    )
    return select(newest.c.id).select_from(requested.join(newest, true()))
