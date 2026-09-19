"""Read-only evidence for capture waiting; never grants training eligibility."""
from datetime import timedelta
from sqlalchemy import text
from .l3_authorization_contract_v3 import validate_profile_contract


def capture_stage(row, *, cutoff, config, eligible_ids):
    if not row:
        return {'stage': 'NO_CAPTURE', 'reason': 'Nenhuma captura L3 encontrada no período do dataset.', 'impediments': []}
    result = {k: row.get(k) for k in ('id', 'decision_id', 'symbol', 'created_at', 'outcome', 'lineage_status', 'measurement_status')}
    result['impediments'] = []  # default; the managed-exit branch below may replace this with a full list
    from app.ml.l3_managed_exit import definition, at
    spec = definition(config)
    managed = (row.get('config_snapshot') or {}).get('l3_managed_ml') or {}
    if spec and str(row['id']) not in eligible_ids:
        if managed.get('contract') != spec or managed.get('capture_valid') is not True:
            return {**result, 'stage':'EXCLUDED', 'reason':'Captura anterior ou incompatível com o contrato de saída gerenciada.', 'impediments': []}
        if not row.get('outcome'):
            horizon = row['entry_timestamp'] + timedelta(seconds=spec['max_holding_seconds'])
            return {**result, 'stage':'EXCLUDED' if cutoff > horizon else 'AWAITING_OUTCOME',
                    'reason':'Horizonte ML excedido; posição preservada.' if cutoff > horizon else 'Aguardando saída pela política congelada na entrada.',
                    'impediments': []}
        # S0.6 (2026-09-17 shadow-trade collapse fix): a closed managed-exit
        # capture can simultaneously fail flow evidence, be missing its
        # measurement, AND still be short of maturity. Reporting only the
        # first one hides the rest — an operator who fixes it sees the
        # capture "still excluded" for a completely different, previously
        # invisible reason. Collect every applicable impediment instead of
        # returning on the first match.
        proof = row.get('managed_label') or {}
        impediments = []
        if not proof.get('valid'):
            impediments.append({
                'code': proof.get('reason') or 'MANAGED_LABEL_INVALID',
                'reason': proof.get('reason') or 'Saída ainda sem comprovação para ML.',
            })
        if row.get('measurement_status') != 'READY' or row.get('entry_quality') != 'OK':
            impediments.append({
                'code': 'MEASUREMENT_NOT_READY',
                'reason': f"Medição canônica ausente ou incompleta (status={row.get('measurement_status') or 'AUSENTE'}, "
                          f"entry_quality={row.get('entry_quality') or 'AUSENTE'}).",
            })
        label_available_at = at(proof['label_available_at']) if proof.get('label_available_at') else None
        horizon = row['entry_timestamp'] + timedelta(seconds=spec['max_holding_seconds'])
        mature = max(horizon, label_available_at or horizon) + timedelta(minutes=int(config['ml_maturity_embargo_margin_minutes']))
        if mature > cutoff:
            impediments.append({
                'code': 'AWAITING_MATURITY',
                'reason': f'Prazo mínimo de maturação: {mature.isoformat()}.',
                'matures_at': mature.isoformat(),
            })
        if impediments:
            primary = impediments[0]
            stage = (
                'AWAITING_MEASUREMENT' if primary['code'] == 'MEASUREMENT_NOT_READY'
                else 'AWAITING_MATURITY' if primary['code'] == 'AWAITING_MATURITY'
                else 'EXCLUDED'
            )
            return {**result, 'stage': stage, 'reason': primary['reason'], 'impediments': impediments}
        # No managed impediment found (proof valid, measurement ready, mature)
        # yet the row is not (yet) in eligible_ids -- e.g. a lag on the
        # canonical trainer query. The legacy checks below assume the
        # non-managed contract shape (a 3-outcome allowlist that predates
        # TRAILING_STOP/FLOW_STRUCTURE_EXIT, a barrier_contract_version field
        # the managed contract does not use) and would misreport a clean
        # managed capture as an incompatible outcome. Report the mismatch
        # honestly instead of fabricating a false reason.
        return {**result, 'stage': 'PENDING_CANONICAL_INCLUSION',
                'reason': 'Sem impedimentos identificados pela política gerenciada; aguardando confirmação na população canônica do trainer.',
                'impediments': []}
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


async def _flow_evidence_detail(db, row):
    """First post-warmup evaluation that failed flow-evidence quality, plus
    which specific policy threshold it broke -- so an operator sees the exact
    number instead of just the generic INCOMPLETE_FLOW_EVIDENCE code.
    """
    snapshot = row.get('config_snapshot') or {}
    policy = (snapshot.get('shadow_l3_exit_policy') or {}).get('config') or {}
    warmup_seconds = policy.get('warmup_seconds')
    entry_timestamp = row.get('entry_timestamp')
    if warmup_seconds is None or entry_timestamp is None:
        return None
    detail = (await db.execute(text('''
        SELECT candle_at, evidence->>'quality' AS quality,
               (evidence->>'data_age_seconds')::float AS data_age_seconds,
               (evidence->>'flow_window_age_seconds')::float AS flow_window_age_seconds,
               (evidence->>'max_gap_seconds')::float AS max_gap_seconds,
               (evidence->>'coverage_pct')::float AS coverage_pct,
               (evidence->>'collection_lag_seconds')::float AS collection_lag_seconds
          FROM shadow_l3_exit_decisions
         WHERE shadow_id = CAST(:id AS uuid) AND candle_at >= :warmup_from
           AND COALESCE(evidence->>'quality', '') <> 'VALID'
         ORDER BY candle_at LIMIT 1
    '''), {'id': row['id'], 'warmup_from': entry_timestamp + timedelta(seconds=warmup_seconds)})).mappings().one_or_none()
    if not detail:
        return None
    when = detail['candle_at']
    stamp = when.strftime('%d/%m às %H:%M')
    # S1 (2026-09-17 flow-window/candle-delay split): a v2 policy sets
    # flow_window_age_seconds and checks it independently from the candle's
    # own availability delay (alignment_seconds); a v1 policy still gates on
    # the combined max_age_seconds. Name whichever dimension the frozen
    # policy actually enforced, not always the v1 one.
    is_v2 = policy.get('flow_window_age_seconds') is not None
    if detail['max_gap_seconds'] is not None and policy.get('max_gap_seconds') is not None and detail['max_gap_seconds'] > policy['max_gap_seconds']:
        reason = f"Avaliação de {stamp}: maior lacuna do fluxo de {detail['max_gap_seconds']:.2f} s, acima de {policy['max_gap_seconds']} s."
    elif detail['coverage_pct'] is not None and policy.get('min_coverage_pct') is not None and detail['coverage_pct'] < policy['min_coverage_pct']:
        reason = f"Avaliação de {stamp}: cobertura do fluxo de {detail['coverage_pct']:.2f}%, abaixo de {policy['min_coverage_pct']}%."
    elif is_v2 and detail['flow_window_age_seconds'] is not None and detail['flow_window_age_seconds'] > policy['flow_window_age_seconds']:
        reason = f"Avaliação de {stamp}: idade do fluxo na janela de {detail['flow_window_age_seconds']:.2f} s, acima de {policy['flow_window_age_seconds']} s."
    elif is_v2 and detail['collection_lag_seconds'] is not None and policy.get('alignment_seconds') is not None and detail['collection_lag_seconds'] > policy['alignment_seconds']:
        reason = f"Avaliação de {stamp}: atraso do candle de {detail['collection_lag_seconds']:.2f} s, acima de {policy['alignment_seconds']} s."
    elif not is_v2 and detail['data_age_seconds'] is not None and policy.get('max_age_seconds') is not None and detail['data_age_seconds'] > policy['max_age_seconds']:
        reason = f"Avaliação de {stamp}: idade do fluxo na referência da decisão de {detail['data_age_seconds']:.2f} s, acima de {policy['max_age_seconds']} s."
    else:
        reason = f"Avaliação de {stamp}: qualidade do fluxo = {detail['quality'] or 'AUSENTE'}."
    return {'candle_at': when, 'quality': detail['quality'], 'data_age_seconds': detail['data_age_seconds'],
            'flow_window_age_seconds': detail['flow_window_age_seconds'],
            'max_gap_seconds': detail['max_gap_seconds'], 'coverage_pct': detail['coverage_pct'],
            'collection_lag_seconds': detail['collection_lag_seconds'], 'reason': reason}


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
    latest_capture = capture_stage(latest, cutoff=cutoff, config=config, eligible_ids=eligible_ids)
    for impediment in latest_capture['impediments']:
        if impediment['code'] == 'INCOMPLETE_FLOW_EVIDENCE':
            detail = await _flow_evidence_detail(db, latest)
            if detail:
                impediment.update(detail)
                if impediment is latest_capture['impediments'][0]:
                    latest_capture['reason'] = detail['reason']
    return {'profile_contracts_valid': bool(checks) and all(not p['errors'] for p in checks),
            'profiles': checks, 'latest_capture': latest_capture,
            'latest_decision': dict(decision) if decision else None,
            'latest_capture_skip': dict(failure) if failure else None,
            'runtime_readiness_confirmed': False,
            'note': 'Contrato estrutural não comprova disponibilidade dos dados nem sucesso da próxima captura.'}
