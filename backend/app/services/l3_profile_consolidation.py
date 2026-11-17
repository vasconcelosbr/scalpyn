"""Observation-only union of profile approvals, independent of Shadow admission."""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from ..models.pipeline_watchlist import PipelineWatchlist
from .profile_display_status import load_profile_display_rows


def consolidate_profile_display_rows(rows, *, now):
    # L3_PROFILE_CONSOLIDATED_DISPLAY: Shadow admission is never a display veto.
    groups = defaultdict(list)
    for row in rows:
        groups[row['symbol']].append(row)
    approved, pending = [], []
    for symbol, contributions in sorted(groups.items()):
        passing = [r for r in contributions if r['status'] == 'approved']
        waiting = [r for r in contributions if r['status'] == 'awaiting_data']
        eligible = passing or waiting
        if not eligible:
            continue
        # Score chooses the representative only; it cannot exclude a symbol.
        eligible = sorted(eligible, key=lambda r: (
            -(float(r['alpha_score']) if r.get('alpha_score') is not None else float('-inf')),
            str(r['watchlist_id']), str(r['profile_id'])))
        winner = eligible[0]
        item = {key: winner.get(key) for key in (
            'symbol', 'profile_name', 'alpha_score', 'current_price', 'evaluated_at', 'decision_id')}
        item.update({key: str(winner[key]) for key in ('asset_id', 'watchlist_id', 'profile_id')})
        item.update(
            direction='SPOT', status='approved' if passing else 'awaiting_data',
            refreshed_at=winner.get('evaluated_at'),
            candidate_count=len({str(r['profile_id']) for r in eligible}),
            candidate_profile_ids=list(dict.fromkeys(str(r['profile_id']) for r in eligible)),
            candidate_profile_names=list(dict.fromkeys(r['profile_name'] for r in eligible)),
            data_reasons=list(dict.fromkeys(reason for r in eligible for reason in r['data_reasons'])),
            executable=False, execution_authority=False,
        )
        (approved if passing else pending).append(item)
    return {
        'id': 'l3-consolidated', 'name': 'L3 Consolidado', 'level': 'L3',
        'virtual': True, 'read_only': True, 'semantic': 'PROFILE_DISPLAY_DATA_SEPARATION',
        'items': approved, 'awaiting_data_items': pending, 'total': len(approved),
        'awaiting_data_count': len(pending), 'as_of': now.isoformat(),
        'executable': False, 'execution_authority': False,
    }


async def load_l3_profile_consolidation(db, *, user_id):
    now = datetime.now(timezone.utc)
    watchlist_ids = (await db.execute(select(PipelineWatchlist.id).where(
        PipelineWatchlist.user_id == user_id, PipelineWatchlist.level == 'L3',
        PipelineWatchlist.market_mode == 'spot',
    ).order_by(PipelineWatchlist.id))).scalars().all()
    rows = []
    for watchlist_id in watchlist_ids:
        # The shared loader enforces active profile and complete parent membership.
        rows.extend(await load_profile_display_rows(
            db, user_id=user_id, watchlist_id=watchlist_id, now=now))
    return consolidate_profile_display_rows(rows, now=now)


async def load_l3_profile_summary_counts(db, *, user_id, watchlists):
    now = datetime.now(timezone.utc)
    counts = {}
    for watchlist in watchlists:
        if watchlist.level != 'L3' or watchlist.market_mode != 'spot':
            continue
        rows = await load_profile_display_rows(
            db, user_id=user_id, watchlist_id=watchlist.id, now=now)
        counts[watchlist.id] = sum(row['status'] == 'approved' for row in rows)
    return counts
