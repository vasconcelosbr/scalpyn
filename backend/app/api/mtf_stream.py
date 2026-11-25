"""Authenticated live MTF diagnostics; isolated from the observer and orders."""
import asyncio
import json
import logging
import time
from uuid import UUID

import jwt
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..database import AsyncSessionLocal
from ..services import mtf_chart_service as chart
from ..services.mtf_live_preview import LivePreview
from ..services.profile_runtime_config import canonical_hash
from ..websocket.gate_ws_client import SPOT_WS_URL

router = APIRouter()
log = logging.getLogger(__name__)


async def authorized_seed(uid, request):
    async with AsyncSessionLocal() as db:
        cfg = await chart.load_config(db, uid)
        members = await chart.display_members(db, uid, request['watchlist_id'])
        member = next((m for m in members if m['symbol'] == request['symbol']), None)
        if member is None:
            raise ValueError('ASSET_NO_LONGER_IN_WATCHLIST')
        profile = request.get('profile_id') or member.get('profile_id')
        seed = await chart.chart_data(db, uid, request['symbol'], profile, cfg)
        families, windows = None, None
        if cfg.schema_version != 'mtf_chart_v3':
            _, _, _, families, _ = await chart.profile_context(db, uid, profile)
            windows = {tf: chart.calculation_window(seed['indicator_config'], tf) for tf in seed['frames']}
        return LivePreview(seed, cfg, families=families, windows=windows)


@router.websocket('/api/mtf/stream')
async def stream(ws: WebSocket):
    await ws.accept()
    try:
        # Credentials stay in the first frame, never in URLs or proxy logs.
        request = await asyncio.wait_for(ws.receive_json(), timeout=10)
        claims = jwt.decode(request['token'], settings.JWT_SECRET, algorithms=['HS256'],
                            options={'require': ['exp', 'sub']})
        if claims.get('type') != 'access':
            raise ValueError('INVALID_TOKEN_TYPE')
        uid = UUID(claims['sub'])
        live = await authorized_seed(uid, request)
        checked_at = time.monotonic()

        async def produce():
            nonlocal checked_at
            async with websockets.connect(SPOT_WS_URL) as gate:
                for tf in live.raw:
                    await gate.send(json.dumps(dict(time=int(time.time()), channel='spot.candlesticks',
                                                    event='subscribe', payload=[tf, request['symbol']])))
                async for raw in gate:
                    now = time.time()
                    if now >= claims['exp']:
                        await ws.close(code=1008)
                        return
                    if time.monotonic() - checked_at >= live.cfg.poll_seconds:
                        async with AsyncSessionLocal() as db:
                            cfg = await chart.load_config(db, uid)
                            members = await chart.display_members(db, uid, request['watchlist_id'])
                            if (canonical_hash(cfg.engine_dump()) != live.seed['analysis']['config_hash']
                                    or not any(m['symbol'] == request['symbol'] for m in members)):
                                await ws.send_json({'type': 'resync'})
                                return
                            member = next(m for m in members if m['symbol'] == request['symbol'])
                            profile_id = request.get('profile_id') or member.get('profile_id')
                            profile, indicators, identity, _, _ = await chart.profile_context(db, uid, profile_id)
                            _, identity = chart.effective_indicators(indicators, identity, cfg)
                            if (str(profile_id or '') != str(live.seed['analysis'].get('profile_id') or '')
                                    or (canonical_hash(profile.config) if profile else None) != live.seed['analysis'].get('profile_hash')
                                    or (cfg.schema_version != 'mtf_chart_v3' and identity['config_hash'] != live.seed['analysis']['indicator_identity']['config_hash'])):
                                await ws.send_json({'type': 'resync'})
                                return
                            # Preserve the independent source clocks of flow and book data.
                            from ..services.mtf_pump_flow import load_context
                            from ..services.mtf_chart_engine import iso
                            live.seed['analysis']['pump_flow'] = await load_context(db, uid, request['symbol'], iso(now), cfg.pump_flow)
                        from ..services.market_data_service import market_data_service
                        book = await market_data_service.fetch_orderbook_metrics(request['symbol'])
                        live.seed['analysis']['spread_pct'] = book.get('spread_pct') if book.get('market_data_source') in ('gate', 'gate.io') else None
                        checked_at = time.monotonic()
                    message = json.loads(raw)
                    if message.get('error'):
                        raise ValueError('MARKET_STREAM_REJECTED')
                    update = await asyncio.to_thread(live.update, message, now)
                    if update:
                        await ws.send_json(update)
                        if update['type'] == 'resync':
                            # The close notification invalidates only the REST read cache;
                            # websocket values never become official candle evidence.
                            for key in list(chart._market_cache):
                                if key[0] == request['symbol']:
                                    chart._market_cache.pop(key, None)
                            return

        async def disconnected():
            while True:
                await ws.receive_text()

        # Closing a browser tab must cancel the exchange subscription immediately.
        tasks = [asyncio.create_task(produce()), asyncio.create_task(disconnected())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        # Never log the authentication frame or credentials.
        log.info('MTF stream ended: %s', type(exc).__name__)
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
