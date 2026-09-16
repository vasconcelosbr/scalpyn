"""Bounded production preflight using the canonical L3 evaluator, without writes."""
from datetime import datetime, timezone
from sqlalchemy import select, text
from ..models.pipeline_watchlist import PipelineWatchlist
from ..models.profile import Profile
from .config_service import config_service
from .l3_on_demand_decisions import evaluate_on_demand_l3


async def capture_preflight(db, user_id):
    # Database-enforced read-only protection also covers future helper changes.
    await db.execute(text('SET TRANSACTION READ ONLY'))
    watchlists = (await db.execute(select(PipelineWatchlist).join(
        Profile, Profile.id == PipelineWatchlist.profile_id).where(
        PipelineWatchlist.user_id == user_id, Profile.user_id == user_id,
        PipelineWatchlist.level == 'L3', PipelineWatchlist.auto_refresh.is_(True),
        Profile.is_active.is_(True), PipelineWatchlist.market_mode == 'spot'
    ))).scalars().all()
    score = await config_service.get_config(db, 'score', user_id)
    reports = []
    for wl in watchlists:
        # Inspect one real upstream candidate per watchlist. No invented input
        # and no symbol from another tenant or lane.
        symbol = (await db.execute(text('''
            SELECT a.symbol FROM pipeline_watchlist_assets a
            JOIN pipeline_watchlists w ON w.id=a.watchlist_id
            WHERE a.watchlist_id=:wid AND w.user_id=:uid
            ORDER BY a.refreshed_at DESC NULLS LAST, a.symbol LIMIT 1
        '''), {'wid': wl.source_watchlist_id, 'uid': user_id})).scalar_one_or_none()
        row = {'watchlist_id': str(wl.id), 'name': wl.name, 'profile_id': str(wl.profile_id), 'symbol': symbol}
        if not symbol:
            reports.append({**row, 'status': 'NO_UPSTREAM_CANDIDATE', 'reason_codes': ['Nenhum candidato na watchlist de origem.']})
            continue
        decisions = await evaluate_on_demand_l3(db, user_id=user_id, watchlist=wl,
            symbols=[symbol], score_config=score, read_only=True)
        if not decisions:
            reports.append({**row, 'status': 'DATA_UNAVAILABLE', 'reason_codes': ['Dados indisponíveis ou fluxo fora da janela de validade.']})
            continue
        decision = decisions[0]
        auth = decision['metrics'].get('l3_authorization_contract_v3') or {}
        gate = decision['metrics'].get('l3_gate_v2') or {}
        reports.append({**row, 'status': auth.get('authorization_status', 'CONTRACT_REJECT'),
            'decision': decision['decision'], 'reason_codes': auth.get('reason_codes') or [],
            'resolution_errors': (auth.get('provenance_resolution') or {}).get('errors') or [],
            'profile_contract': (gate.get('execution_contract') or {}).get('status'),
            'required_skipped': (gate.get('entry_triggers') or {}).get('skipped_required') or [],
            'evaluated_at': auth.get('evaluated_at'), 'authorization_contract_hash': auth.get('authorization_contract_hash')})
    return {'checked_at': datetime.now(timezone.utc), 'read_only': True,
            'profiles': reports, 'capture_created': False, 'training_started': False,
            'note': 'Amostra atual por perfil; a próxima captura real ainda precisa completar medição, maturação e inclusão no trainer.'}
