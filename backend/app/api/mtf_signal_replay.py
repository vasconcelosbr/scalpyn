"""Owner-scoped immutable signal evidence; no watchlist membership dependency."""
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from ..database import get_db
from .config import get_current_user_id

router = APIRouter(prefix='/api/mtf/replays', tags=['MTF signal replay'])


@router.get('')
async def listing(response: Response, symbol: str | None = None, watchlist_id: str | None = None,
                  status: str | None = None, start: datetime | None = None, end: datetime | None = None,
                  offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  db=Depends(get_db), uid=Depends(get_current_user_id)):
    response.headers['Cache-Control'] = 'private, no-store'
    if any(d and d.tzinfo is None for d in (start, end)) or (start and end and start > end):
        raise HTTPException(422, 'INVALID_DATE_RANGE_OR_TIMEZONE')
    rows = (await db.execute(text('''SELECT r.id,r.symbol,r.watchlist_id,r.profile_id,r.triggered_at,
        r.baseline,r.target,r.stop,p.status,p.result,p.issue,p.checked_at,
        (SELECT (c.payload->>'close')::double precision FROM mtf_replay_candles c
            WHERE c.replay_id=r.id ORDER BY c.time DESC LIMIT 1) AS last_price
        FROM mtf_signal_replays r JOIN mtf_replay_progress p ON p.replay_id=r.id
        WHERE r.user_id=:uid AND (CAST(:symbol AS text) IS NULL OR r.symbol=:symbol)
        AND (CAST(:wid AS text) IS NULL OR r.watchlist_id=:wid)
        AND (CAST(:status AS text) IS NULL OR p.status=:status)
        AND (CAST(:start AS timestamptz) IS NULL OR r.triggered_at>=:start)
        AND (CAST(:end AS timestamptz) IS NULL OR r.triggered_at<=:end)
        ORDER BY r.triggered_at DESC,r.id DESC LIMIT :limit OFFSET :offset'''),
        dict(uid=uid, symbol=symbol, wid=watchlist_id, status=status, start=start, end=end,
             limit=limit+1, offset=offset))).mappings().all()
    return {'items': [dict(r) for r in rows[:limit]], 'next_offset': offset+limit if len(rows)>limit else None}


@router.get('/{rid}')
async def detail(rid: UUID, response: Response, db=Depends(get_db), uid=Depends(get_current_user_id)):
    response.headers['Cache-Control'] = 'private, no-store'
    row = (await db.execute(text('''SELECT r.*,p.status,p.result,p.issue,p.checked_at FROM mtf_signal_replays r
        JOIN mtf_replay_progress p ON p.replay_id=r.id WHERE r.id=:id AND r.user_id=:uid'''),
        {'id': rid, 'uid': uid})).mappings().first()
    if not row:
        raise HTTPException(404, 'REPLAY_NOT_FOUND')
    return dict(row)


@router.get('/{rid}/trajectory')
async def trajectory(rid: UUID, response: Response, after: datetime | None = None,
                     limit: int = Query(1000, ge=1, le=5000), db=Depends(get_db), uid=Depends(get_current_user_id)):
    response.headers['Cache-Control'] = 'private, no-store'
    if after and after.tzinfo is None:
        raise HTTPException(422, 'TIMEZONE_REQUIRED')
    replay = (await db.execute(text('SELECT * FROM mtf_signal_replays WHERE id=:id AND user_id=:uid'),
                              {'id': rid, 'uid': uid})).mappings().first()
    if not replay:
        raise HTTPException(404, 'REPLAY_NOT_FOUND')
    bars = (await db.execute(text('''SELECT time,payload FROM mtf_replay_candles WHERE replay_id=:id
        AND (CAST(:after AS timestamptz) IS NULL OR time>:after) ORDER BY time LIMIT :limit'''),
        {'id': rid, 'after': after, 'limit': limit+1})).mappings().all()
    last = bars[limit-1]['time'] if len(bars)>limit else None
    # Snapshots are paged on the same candle-time window; carry their real capture clocks.
    snapshots = list((await db.execute(text('''SELECT captured_at,payload FROM mtf_replay_snapshots WHERE replay_id=:id
        AND (CAST(:after AS timestamptz) IS NULL OR captured_at>:after)
        AND (CAST(:until AS timestamptz) IS NULL OR captured_at<=:until + interval '1 minute')
        ORDER BY captured_at'''), {'id': rid, 'after': after, 'until': last})).mappings().all())
    if replay['snapshot'].get('capture_kind') == 'reconstructed_from_frozen_observations':
        progress = (await db.execute(text('SELECT next_at FROM mtf_replay_progress WHERE replay_id=:id'), {'id': rid})).scalar_one()
        until = last if last else progress
        historical = (await db.execute(text('''SELECT observed_at AS captured_at,
            payload - 'replay_inputs' AS payload FROM mtf_history_observations
            WHERE user_id=:uid AND watchlist_id=:wid AND symbol=:symbol
            AND profile_id IS NOT DISTINCT FROM CAST(:profile AS text)
            AND observed_at>=:trigger AND observed_at<=:until
            AND (CAST(:after AS timestamptz) IS NULL OR observed_at>:after)
            ORDER BY observed_at,id'''), dict(uid=uid,wid=replay['watchlist_id'],symbol=replay['symbol'],
                profile=replay['profile_id'],trigger=replay['triggered_at'],until=until,after=after))).mappings().all()
        snapshots.extend(historical)
        snapshots.sort(key=lambda s: s['captured_at'])
    return {'candles': [r['payload'] for r in bars[:limit]], 'snapshots': [dict(s) for s in snapshots], 'next_cursor': last}
