"""Pump-only endpoint-direction research and explicitly unvalidated inference.

No Shadow, trading, promotion or database dependencies. Native artifacts remain
separate from the legacy touch model; repeated episodes receive equal weight.
"""
from collections import Counter
import json
from pathlib import Path
from . import pump_opportunity_engine as eng
from .pump_contracts import validate_manifest

OBJECTIVE = 'pump_endpoint_direction_v1'


class DirectionalSupportError(ValueError):
    def __init__(self,message,details):
        super().__init__(message)
        self.details=details


def cohort_support(rows):
    return {'observations':len(rows),'episodes':len({r['episode_id'] for r in rows}),
            'instruments':len({r['instrument_id'] for r in rows}),
            'days':len({eng.utc(r['decision_at']).date() for r in rows}),
            'up':sum(r['target'] is True for r in rows),'down':sum(r['target'] is False for r in rows),
            'from':min((r['decision_at'] for r in rows),default=None),
            'to':max((r['decision_at'] for r in rows),default=None)}


def directional_target(label):
    """Unknown coverage, absent/zero endpoint and booleans are not down labels."""
    value = label.get('endpoint_return_pct')
    if label.get('status') != 'known' or label.get('coverage_complete') is not True or not eng.number(value) or value == 0 or value<=-100:
        return None
    return value > 0


def episode_weights(rows):
    counts = Counter(r['episode_id'] for r in rows)
    return [1 / counts[r['episode_id']] for r in rows]


def train_directional(rows, *, spec, output_root):
    """Fit one declared horizon; test never selects parameters or calibration."""
    frozen = validate_manifest(spec)
    target = spec.get('directional_target', {})
    horizon = target.get('horizon_minutes')
    if target != {'version': OBJECTIVE, 'horizon_minutes': horizon,
                  'reference_policy': 'gate_best_ask_v1', 'flat_policy': 'exclude_exact_zero',
                  'unknown_policy': 'exclude', 'return_policy': 'endpoint_gross'}:
        raise ValueError('Explicit directional target contract required')
    if type(horizon) is not int or horizon not in spec['label_spec']['horizons_minutes']:
        raise ValueError('Directional horizon must exist in frozen labels')
    if len(rows) > spec['max_rows'] or not 0 < spec['max_threads'] <= 2:
        raise ValueError('Pump directional research budget exceeded')
    options = spec['directional_evaluation']
    if type(options['bootstrap_repetitions']) is not int or not 1 <= options['bootstrap_repetitions'] <= 1000:
        raise ValueError('Bounded episode bootstrap required')
    if type(options['reliability_bins']) is not int or not 2 <= options['reliability_bins'] <= 20:
        raise ValueError('Bounded reliability bins required')
    features = spec['features']
    keys = ('feature_spec_hash', 'label_spec_hash', 'cost_policy_hash', 'legacy_config_hash')
    expected = (spec['feature_spec_hash'], spec['label_spec_hash'], spec['cost_policy_hash'], spec['producer_config_hash'])
    usable = []
    for r in rows:
        y = directional_target({'status': r.get('label_status'), 'coverage_complete': r.get('label_coverage_complete'),
                                'endpoint_return_pct': r.get('endpoint_return_pct')})
        if y is None or r.get('horizon_minutes') != horizon or not all(eng.number(r['values'].get(f)) for f in features):
            continue
        reference=r.get('reference') or {}
        if reference.get('policy')!='gate_best_ask_v1' or not eng.number(reference.get('price')) or reference['price']<=0:
            continue
        if not r['manifest'].get('listing_certified') or tuple(r['manifest'].get(k) for k in keys) != expected:
            continue
        # Derive target here: never trust an unrelated preexisting touch target.
        usable.append({**r, 'target': y})
    support = {'observations': len(usable), 'episodes': len({r['episode_id'] for r in usable}),
               'days': len({eng.utc(r['decision_at']).date() for r in usable}),
               'instruments': len({r['instrument_id'] for r in usable}), 'excluded_rows': len(rows)-len(usable)}
    if support['episodes'] < spec['min_episodes'] or support['days'] < spec['min_days'] or support['instruments'] < spec['min_instruments']:
        raise ValueError('Insufficient independent directional support')
    train, rest = eng.purged_split(usable, spec['boundaries'][0], spec['embargo_seconds'])
    val, rest = eng.purged_split(rest, spec['boundaries'][1], spec['embargo_seconds'])
    cal, test = eng.purged_split(rest, spec['boundaries'][2], spec['embargo_seconds'])
    cohorts = [train, val, cal, test]
    cohort_details={name:cohort_support(c) for name,c in zip(('train','validation','calibration','test'),cohorts)}
    if any(not c or len({r['target'] for r in c}) != 2 for c in cohorts):
        raise DirectionalSupportError('Both directions required in every temporal cohort',cohort_details)
    episodes = [{r['episode_id'] for r in c} for c in cohorts]
    if any(a & b for i, a in enumerate(episodes) for b in episodes[i+1:]):
        raise ValueError('Episode leakage across directional cohorts')
    import numpy as np
    import xgboost as xgb
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score, average_precision_score,balanced_accuracy_score
    def xy(c):
        return np.array([[r['values'][f] for f in features] for r in c]), np.array([int(r['target']) for r in c])
    x, y = xy(train); vx, vy = xy(val); cx, cy = xy(cal); tx, ty = xy(test)
    tw = np.array(episode_weights(train)); cw = np.array(episode_weights(cal)); ew = np.array(episode_weights(test))
    model = xgb.XGBClassifier(**{**spec['params'], 'n_jobs': spec['max_threads'], 'objective': 'binary:logistic'})
    model.fit(x, y, sample_weight=tw, eval_set=[(vx, vy)], verbose=False)
    def logit(p):
        p = np.clip(p, 1e-6, 1-1e-6)
        return np.log(p/(1-p)).reshape(-1, 1)
    calibration = LogisticRegression(C=options['calibration_C'], max_iter=options['calibration_max_iter'],
                                     random_state=spec['params']['random_state'])
    calibration.fit(logit(model.predict_proba(cx)[:, 1]), cy, sample_weight=cw)
    p = calibration.predict_proba(logit(model.predict_proba(tx)[:, 1]))[:, 1]
    prior = float(np.average(y, weights=tw)); baseline = np.full(len(ty), prior)
    calibration_prior=float(np.average(cy,weights=cw));recent_baseline=np.full(len(ty),calibration_prior)
    def measure(weights):
        return {'brier': float(brier_score_loss(ty, p, sample_weight=weights)),
                'baseline_brier': float(brier_score_loss(ty, baseline, sample_weight=weights)),
                'log_loss': float(log_loss(ty, p, sample_weight=weights, labels=[0, 1])),
                'baseline_log_loss': float(log_loss(ty, baseline, sample_weight=weights, labels=[0, 1])),
                'calibration_prior_brier':float(brier_score_loss(ty,recent_baseline,sample_weight=weights)),
                'calibration_prior_log_loss':float(log_loss(ty,recent_baseline,sample_weight=weights,labels=[0,1])),
                'auc': float(roc_auc_score(ty, p, sample_weight=weights)),
                'average_precision_up': float(average_precision_score(ty, p, sample_weight=weights)),
                'direction_accuracy': float(np.average((p >= .5) == ty, weights=weights)),
                'balanced_direction_accuracy':float(balanced_accuracy_score(ty,p>=.5,sample_weight=weights)),
                'baseline_direction_accuracy': float(np.average((baseline >= .5) == ty, weights=weights)),
                'calibration_prior_direction_accuracy':float(np.average((recent_baseline>=.5)==ty,weights=weights)),
                'always_down_accuracy':float(np.average(ty==0,weights=weights)),
                'always_up_accuracy':float(np.average(ty==1,weights=weights)),
                'observed_up_frequency': float(np.average(ty, weights=weights)),
                'baseline_train_up_frequency': prior,'baseline_calibration_up_frequency':calibration_prior}
    reliability = []
    for lo, hi in zip(np.linspace(0, 1, options['reliability_bins']+1)[:-1], np.linspace(0, 1, options['reliability_bins']+1)[1:]):
        mask = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        reliability.append({'lo': float(lo), 'hi': float(hi), 'observations': int(mask.sum()),
                            'mean_probability': float(np.average(p[mask], weights=ew[mask])) if mask.any() else None,
                            'observed_up_frequency': float(np.average(ty[mask], weights=ew[mask])) if mask.any() else None})
    # Paired loss differences resample entire episodes, preserving row dependence.
    groups = {}
    for i, r in enumerate(test): groups.setdefault(r['episode_id'], []).append(i)
    losses = np.array([float(np.mean((ty[idx]-prior)**2 - (ty[idx]-p[idx])**2)) for idx in groups.values()])
    recent_losses=np.array([float(np.mean((ty[idx]-calibration_prior)**2 - (ty[idx]-p[idx])**2)) for idx in groups.values()])
    rng = np.random.default_rng(spec['params']['random_state'])
    boot = [float(rng.choice(losses, len(losses), replace=True).mean()) for _ in range(options['bootstrap_repetitions'])]
    interval = np.quantile(boot, [.025, .975]).tolist()
    recent_boot=[float(rng.choice(recent_losses,len(recent_losses),replace=True).mean()) for _ in range(options['bootstrap_repetitions'])]
    metrics = {'status': 'requires_independent_directional_review', 'support': support,
               'cohort_support':cohort_details,
               'cohort_rows': [len(c) for c in cohorts], 'cohort_episodes': [len(e) for e in episodes],
               'cohort_days': [len({eng.utc(r['decision_at']).date() for r in c}) for c in cohorts],
               'purged_or_embargoed_rows': len(usable)-sum(map(len, cohorts)),
               'episode_weighted_test': measure(ew), 'row_weighted_test': measure(np.ones(len(test))),
               'reliability': reliability, 'paired_episode_brier_improvement': float(losses.mean()),
               'paired_episode_brier_ci95': interval, 'bootstrap_repetitions': options['bootstrap_repetitions'],
               'paired_episode_brier_improvement_vs_calibration_prior':float(recent_losses.mean()),
               'paired_episode_brier_ci95_vs_calibration_prior':np.quantile(recent_boot,[.025,.975]).tolist(),
               'test_predicted_up_observations':int(sum(p>=.5)),
               'interval_scope': 'conditional_on_test_period_not_independent_regimes',
               'applied_delta': 0, 'auto_promotion': False}
    versions={'xgboost':xgb.__version__,'numpy':np.__version__,'scikit_learn':sklearn.__version__}
    experiment = eng.canonical_hash({'spec': spec, 'ids': [r['observation_id'] for r in usable],'library_versions':versions})
    folder = Path(output_root).resolve() / 'pump_directional' / experiment
    folder.mkdir(parents=True, exist_ok=False)
    model.get_booster().save_model(folder/'xgboost.json')
    manifest = {'experiment_id': experiment, 'artifact_namespace': f'pump_directional/{experiment}',
                'objective': OBJECTIVE, 'spec': spec, 'frozen_contract': frozen,
                'status': 'challenger', 'auto_promotion': False, 'delta': 0,
                'library_versions':versions,
                'probability_event':'positive_endpoint_return_conditional_on_known_nonzero_endpoint',
                'feature_bounds': {f: {'min': float(x[:, i].min()), 'max': float(x[:, i].max())} for i, f in enumerate(features)}}
    calibrator = {'input': 'clipped_logit', 'clip': 1e-6, 'coef': calibration.coef_.tolist(),
                  'intercept': calibration.intercept_.tolist()}
    for name, data in [('manifest.json', manifest), ('calibrator.json', calibrator), ('metrics.json', metrics)]:
        (folder/name).write_text(json.dumps(data, sort_keys=True, allow_nan=False), encoding='utf-8')
    return {'manifest': manifest, 'metrics': metrics}


def directional_preview(values, manifest, predict_up):
    """Research-only per-asset output. Never grants validation or effective score."""
    spec = manifest['spec']
    result = {'objective': OBJECTIVE, 'horizon_minutes': spec['directional_target']['horizon_minutes'],
              'status': 'abstained', 'direction': None, 'score': None, 'probability': None,
              'applied_delta': 0, 'model_id': manifest['experiment_id'], 'validation': manifest['status']}
    if manifest.get('objective') != OBJECTIVE:
        return {**result, 'reason': 'incompatible_model_objective'}
    if any(not eng.number(values.get(f)) for f in spec['features']):
        return {**result, 'reason': 'missing_or_invalid_features'}
    bounds = manifest['feature_bounds']
    if any(not bounds[f]['min'] <= values[f] <= bounds[f]['max'] for f in spec['features']):
        return {**result, 'reason': 'outside_training_feature_range'}
    p = predict_up([values[f] for f in spec['features']])
    if not eng.number(p) or not 0 <= p <= 1:
        return {**result, 'reason': 'invalid_model_output'}
    # Candidate values are visibly separated from any validated operational note.
    return {**result, 'reason': 'directional_model_not_independently_validated',
            'research_only': {'candidate_direction': 'up' if p > .5 else 'down' if p < .5 else 'undetermined',
                              'candidate_ordinal_score': 100*p, 'estimated_up_probability': p,
                              'score_semantics': 'research_scale_0_down_50_neutral_100_up_not_validated',
                              'features_at_decision': {f: values[f] for f in spec['features']}}}


def load_directional_preview(values, folder):
    """Offline/native inference from a directional artifact, with local evidence."""
    folder=Path(folder)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('objective')!=OBJECTIVE:raise ValueError('Not a directional Pump artifact')
    import numpy as np
    import xgboost as xgb
    calibration=json.loads((folder/'calibrator.json').read_text(encoding='utf-8'))
    if calibration.get('input')!='clipped_logit':raise ValueError('Unknown directional calibration input')
    model=xgb.Booster();model.load_model(folder/'xgboost.json');model.set_param({'nthread':1})
    def predict(vector):
        raw=float(model.predict(xgb.DMatrix(np.array([vector])))[0])
        p=np.clip(raw,calibration['clip'],1-calibration['clip'])
        z=calibration['coef'][0][0]*np.log(p/(1-p))+calibration['intercept'][0]
        return float(1/(1+np.exp(-np.clip(z,-700,700))))
    result=directional_preview(values,manifest,predict)
    metrics=json.loads((folder/'metrics.json').read_text(encoding='utf-8'))
    result['evidence']={'support':metrics['support'],'cohort_episodes':metrics['cohort_episodes'],
                        'cohort_days':metrics['cohort_days'],'review_status':metrics['status'],
                        'paired_episode_brier_ci95':metrics['paired_episode_brier_ci95'],
                        'probability_event':manifest.get('probability_event'),
                        'note':'Univariate range checks cannot certify support for every joint combination'}
    if 'research_only' in result:
        vector=np.array([[values[f] for f in manifest['spec']['features']]])
        contribution=model.predict(xgb.DMatrix(vector),pred_contribs=True)[0]
        result['research_only']['joint_model_contributions_log_odds']={f:float(contribution[i]) for i,f in enumerate(manifest['spec']['features'])}
        result['research_only']['explanation_scope']='conditional model contributions, not independent confirmations or causal effects'
    return result
