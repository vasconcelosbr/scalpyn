"""Read-only evidence for capture waiting; never grants training eligibility."""
from datetime import timedelta
from sqlalchemy import text
from .l3_authorization_contract_v3 import validate_profile_contract


def capture_stage(row, *, cutoff, config, eligible_ids):
    if not row:
        return {'stage': 'NO_CAPTURE', 'reason': 'Nenhuma captura L3 encontrada no período do dataset.'}
    result = {k: row.get(k) for k in ('id', 'decision_id', 'symbol', 'created_at', 'outcome', 'lineage_status', 'measurement_status')}
    from app.ml.l3_managed_exit import definition, at
    spec = definition(config)
    managed = (row.get('config_snapshot') or {}).get('l3_managed_ml') or {}
    if spec and str(row['id']) not in eligible_ids:
        if managed.get('contract') != spec or managed.get('capture_valid') is not True:
            return {**result, 'stage':'EXCLUDED', 'reason':'Captura anterior ou incompatível com o contrato de saída gerenciada.'}
        if not row.get('outcome'):
            horizon = row['entry_timestamp'] + timedelta(seconds=spec['max_holding_seconds'])
            return {**result, 'stage':'EXCLUDED' if cutoff > horizon else 'AWAITING_OUTCOME',
                    'reason':'Horizonte ML excedido; posição preservada.' if cutoff > horizon else 'Aguardando saída pela política congelada na entrada.'}
        proof = row.get('managed_label') or {}
        if not proof.get('valid'):
            return {**result, 'stage':'EXCLUDED', 'reason':proof.get('reason') or 'Saída ainda sem comprovação para ML.'}
        if row.get('measurement_status') != 'READY' or row.get('entry_quality') != 'OK':
            return {**result, 'stage':'AWAITING_MEASUREMENT', 'reason':'Aguardando medição READY/OK.'}
        mature = max(row['entry_timestamp']+timedelta(seconds=spec['max_holding_seconds']), at(proof['label_available_at'])) + timedelta(minutes=int(config['ml_maturity_embargo_margin_minutes']))
        if mature > cutoff:
            return {**result, 'stage':'AWAITING_MATURITY','matures_at':mature,'reason':'Aguardando horizonte e margem de maturação do contrato gerenciado.'}
    if str(row['id']) in eligible_ids:
        result.update(stage='ELIGIBLE', reason='Incluída na população canônica do trainer.')
    elif row.get('lineage_status') != 'EXACT' or row.get('eligible_for_training') is not True:
        result.update(stage='EXCLUDED', reason=row.get('lineage_status') or 'Captura sem elegibilidade de origem.')
    elif row.get('barrier_contract_version') != config['ml_active_barrier_contract_version']:
        result.update(stage='EXCLUDED', reason='Contrato econômico diferente do dataset ativo.')
    elif not row.get('outcome'):
        result.update(stage='AWAITING_OUTCOME', reason='Captura persistida, aguardando desfecho.')
    elif row['outcome'] not in ('TP_HIT', 'SL_HIT', 'TIMEOUT'):
        result.update(stage='EXCLUDED', reason=f"Desfecho {row['outcome']} fora do contrato atual do dataset.")
    elif row.get('measurement_status') != 'READY' or row.get('entry_quality') != 'OK':
        result.update(stage='AWAITING_MEASUREMENT', reason='Medição ainda não certificada como READY/OK.')
    elif (row.get('label_resolved_at') or row.get('completed_at')) is None:
        result.update(stage='AWAITING_LABEL', reason='Resultado ainda sem resolução registrada.')
    else:
        mature_at = row['created_at'] + timedelta(minutes=(row.get('ttt_timeout_minutes') or 0) + int(config['ml_maturity_embargo_margin_minutes']))
        result['matures_at'] = mature_at
        if mature_at > cutoff:
            result.update(stage='AWAITING_MATURITY', reason='Aguardando a janela de maturação do trainer.')
        else:
            result.update(stage='EXCLUDED', reason='Excluída pelas demais verificações canônicas do dataset; consultar funil de elegibilidade.')
    return result


async def l3_capture_diagnostics(db, user_id, *, cutoff, config, eligible_ids):
    params = {'uid': str(user_id), 'cutoff': cutoff}
    profiles = (await db.execute(text('''
        SELECT p.id::text AS profile_id, p.name, p.config, pw.id::text AS watchlist_id,
               pw.last_scanned_at
          FROM pipeline_watchlists pw JOIN profiles p ON p.id=pw.profile_id
         WHERE pw.user_id=:uid AND p.user_id=:uid AND UPPER(pw.level)='L3'
           AND p.is_active AND pw.auto_refresh
    '''), params)).mappings().all()
    checks = [{**{k: r[k] for k in ('profile_id','name','watchlist_id','last_scanned_at')},
               'errors': validate_profile_contract(r['config'] or {})} for r in profiles]
    # Scope to this tenant/lane and the same dataset frontier. Old invalid rows
    # remain visible as evidence, never rewritten by this endpoint.
    from app.ml.dataset_config import parse_required_ml_dataset_valid_from
    params['frontier'] = parse_required_ml_dataset_valid_from({'ml_dataset_valid_from': config.get('ml_l3_dataset_valid_from') or config['ml_dataset_valid_from']})
    latest = (await db.execute(text('''
        SELECT st.id::text, st.decision_id, st.symbol, st.created_at, st.outcome,
               st.lineage_status, st.eligible_for_training, st.barrier_contract_version,
               st.config_snapshot, st.entry_timestamp, sx.state->'ml_label' AS managed_label,
               st.label_resolved_at, st.completed_at, st.ttt_timeout_minutes,
               mr.status AS measurement_status, mr.entry_quality
          FROM shadow_trades st LEFT JOIN shadow_l3_exit_states sx ON sx.shadow_id=st.id
          LEFT JOIN LATERAL (
              SELECT status, entry_quality FROM shadow_trade_measurement_revisions
               WHERE shadow_trade_id=st.id AND created_at<=:cutoff
               ORDER BY created_at DESC,id DESC LIMIT 1
          ) mr ON TRUE
         WHERE st.user_id=:uid AND st.source='L3' AND st.created_at<=:cutoff
           AND st.entry_timestamp>=:frontier
         ORDER BY st.created_at DESC,st.id DESC LIMIT 1
    '''),params)).mappings().one_or_none()
    decision = (await db.execute(text('''
        SELECT id, created_at, symbol, decision,
               metrics->'l3_authorization_contract_v3'->>'authorization_status' AS authorization_status
          FROM decisions_log WHERE user_id=:uid AND strategy='L3' AND created_at<=:cutoff
         ORDER BY created_at DESC,id DESC LIMIT 1
    '''),params)).mappings().one_or_none()
    failure = (await db.execute(text('''
        SELECT id, created_at, symbol, source_path, skip_reason FROM shadow_capture_skips
         WHERE user_id=:uid AND created_at<=:cutoff AND created_at>=:frontier
           AND (source_path ILIKE '%l3%' OR source_path ILIKE '%profile%')
         ORDER BY created_at DESC,id DESC LIMIT 1
    '''),params)).mappings().one_or_none()
    return {'profile_contracts_valid': bool(checks) and all(not p['errors'] for p in checks),
            'profiles': checks, 'latest_capture': capture_stage(latest,cutoff=cutoff,config=config,eligible_ids=eligible_ids),
            'latest_decision': dict(decision) if decision else None,
            'latest_capture_skip': dict(failure) if failure else None,
            'runtime_readiness_confirmed': False,
            'note': 'Contrato estrutural não comprova disponibilidade dos dados nem sucesso da próxima captura.'}
