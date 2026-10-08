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
# 2026-10-07: direction RELATIVE to the market. Label = asset endpoint return minus
# the median endpoint return of every labelled asset captured in the same minute
# (same horizon). Removes the shared daily drift that dominated the absolute label
# (up-frequency 0.25-0.74 between consecutive temporal blocks on 07/10).
RELATIVE_OBJECTIVE = 'pump_relative_direction_v1'
OBJECTIVES = (OBJECTIVE, RELATIVE_OBJECTIVE)
BENCHMARK_POLICY = 'universe_beta_residual_median_mid_v1'
PLAIN_BENCHMARK_POLICY = 'universe_median_same_slot_mid_v1'


def mid_return(endpoint_return_pct, spread_pct):
    """Ask-referenced endpoint return → mid-referenced (mid = ask / (1 + spread/200))."""
    if not eng.number(endpoint_return_pct) or not eng.number(spread_pct) or spread_pct < 0:
        return None
    return ((1 + endpoint_return_pct / 100) * (1 + spread_pct / 200) - 1) * 100


def target_contract(horizon, relative=False, min_assets=10, beta=True):
    base = {'version': OBJECTIVE, 'horizon_minutes': horizon, 'reference_policy': 'gate_best_ask_v1',
            'flat_policy': 'exclude_exact_zero', 'unknown_policy': 'exclude', 'return_policy': 'endpoint_gross'}
    if not relative:
        return base
    return {**base, 'version': RELATIVE_OBJECTIVE, 'benchmark_policy': BENCHMARK_POLICY if beta else PLAIN_BENCHMARK_POLICY,
            'min_benchmark_assets': min_assets, 'flat_policy': 'exclude_exact_tie_with_benchmark'}


def relative_target(row, min_assets):
    """Above (True) / below (False) the same-minute universe median, both mid-referenced;
    None when unknown."""
    own = mid_return(row.get('endpoint_return_pct'), (row.get('values') or {}).get('spread_pct'))
    bench = row.get('benchmark_return_pct')
    if row.get('label_status') != 'known' or row.get('label_coverage_complete') is not True:
        return None
    if not eng.number(own) or own <= -100 or not eng.number(bench):
        return None
    if type(row.get('benchmark_assets')) is not int or row['benchmark_assets'] < min_assets:
        return None
    diff = own - bench
    return None if abs(diff) < 1e-9 else diff > 0   # tie (float-tolerant) → excluded


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
    relative = target.get('version') == RELATIVE_OBJECTIVE
    min_assets = target.get('min_benchmark_assets')
    if target != target_contract(horizon, relative, min_assets, target.get('benchmark_policy') != PLAIN_BENCHMARK_POLICY):
        raise ValueError('Explicit directional target contract required')
    if relative and (type(min_assets) is not int or min_assets < 3):
        raise ValueError('Relative target requires min_benchmark_assets >= 3')
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
    context = list(spec.get('context_features') or [])
    columns = model_columns(spec)
    keys = ('feature_spec_hash', 'label_spec_hash', 'cost_policy_hash', 'legacy_config_hash')
    expected = (spec['feature_spec_hash'], spec['label_spec_hash'], spec['cost_policy_hash'], spec['producer_config_hash'])
    usable = []
    for r in rows:
        y = relative_target(r, min_assets) if relative else directional_target(
            {'status': r.get('label_status'), 'coverage_complete': r.get('label_coverage_complete'),
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
        # Core features are complete by eligibility; optional context is NaN when absent.
        def cell(r, f):
            v = (r.get('values') or {}).get(f)
            return float(v) if eng.number(v) else np.nan
        return (np.array([[cell(r, f) for f in columns] for r in c], dtype=float).reshape(len(c), len(columns)),
                np.array([int(r['target']) for r in c]))
    x, y = xy(train); vx, vy = xy(val); cx, cy = xy(cal); tx, ty = xy(test)
    tw = np.array(episode_weights(train)); cw = np.array(episode_weights(cal)); ew = np.array(episode_weights(test))
    model = xgb.XGBClassifier(**{**spec['params'], 'n_jobs': spec['max_threads'], 'objective': 'binary:logistic'})
    model.fit(x, y, sample_weight=tw, eval_set=[(vx, vy)], verbose=False)
    def logit(p):
        p = np.clip(p, 1e-6, 1-1e-6)
        return np.log(p/(1-p)).reshape(-1, 1)
    pool = options.get('calibration_pool', 'calibration')
    if pool == 'validation_and_calibration':
        # Validation is never used to fit or stop the booster, so it is out-of-sample
        # for calibration too; pooling doubles the calibration evidence.
        kx = np.vstack([vx, cx]); ky = np.concatenate([vy, cy])
        kw = np.concatenate([np.array(episode_weights(val)), cw])
    else:
        kx, ky, kw = cx, cy, cw
    calibration_fit = fit_calibration(logit(model.predict_proba(kx)[:, 1]).ravel(), ky, kw, options,
                                      spec['params']['random_state'])
    p = apply_calibration(model.predict_proba(tx)[:, 1], calibration_fit)
    prior = float(np.average(y, weights=tw)); baseline = np.full(len(ty), prior)
    calibration_prior=float(np.average(ky,weights=kw));recent_baseline=np.full(len(ty),calibration_prior)
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
               'calibration': {k: calibration_fit[k] for k in ('method', 'pool', 'slope', 'intercept',
                                                               'unbounded_slope', 'bounded')}
                              | {'pool': pool, 'rows': int(len(ky))},
               'cohort_up_frequency': [float(np.average(y, weights=tw)), float(np.average(vy, weights=episode_weights(val))),
                                       float(np.average(cy, weights=cw)), float(np.average(ty, weights=ew))],
               'test_mean_probability': float(np.average(p, weights=ew)),
               'context_coverage': {name: {f: float(np.mean(~np.isnan(m[:, len(features)+i]))) for i, f in enumerate(context)}
                                    for name, m in (('train', x), ('calibration', kx), ('test', tx))} if context else {},
               'feature_importance_gain': _importance(model, columns),
               'walk_forward': (walk_forward_evaluation(usable, columns=columns, spec=spec, options=options,
                                                        relative=relative)
                                if (spec.get('walk_forward') or {}).get('enabled') else None),
               'interval_scope': 'conditional_on_test_period_not_independent_regimes',
               'applied_delta': 0, 'auto_promotion': False}
    versions={'xgboost':xgb.__version__,'numpy':np.__version__,'scikit_learn':sklearn.__version__}
    experiment = eng.canonical_hash({'spec': spec, 'ids': [r['observation_id'] for r in usable],'library_versions':versions})
    folder = Path(output_root).resolve() / 'pump_ml' / 'directional' / experiment
    folder.mkdir(parents=True, exist_ok=False)
    model.get_booster().save_model(folder/'xgboost.json')
    manifest = {'experiment_id': experiment, 'artifact_namespace': f'pump_ml/directional/{experiment}',
                'objective': RELATIVE_OBJECTIVE if relative else OBJECTIVE, 'spec': spec, 'frozen_contract': frozen,
                'status': 'challenger', 'auto_promotion': False, 'delta': 0,
                'library_versions':versions,
                'probability_event':('endpoint_return_above_same_minute_universe_median' if relative
                                     else 'positive_endpoint_return_conditional_on_known_nonzero_endpoint'),
                'feature_bounds': _bounds(x, columns)}
    calibrator = {'input': 'clipped_logit', 'clip': 1e-6, 'coef': [[calibration_fit['slope']]],
                  'intercept': [calibration_fit['intercept']], 'method': calibration_fit['method'], 'pool': pool}
    for name, data in [('manifest.json', manifest), ('calibrator.json', calibrator), ('metrics.json', metrics)]:
        (folder/name).write_text(json.dumps(data, sort_keys=True, allow_nan=False), encoding='utf-8')
    return {'manifest': manifest, 'metrics': metrics}


def _bounds(x, columns):
    """Training range per column; context columns use observed (non-NaN) values only."""
    import numpy as np
    out = {}
    for i, f in enumerate(columns):
        col = x[:, i][~np.isnan(x[:, i])]
        out[f] = {'min': float(col.min()), 'max': float(col.max())} if col.size else None
    return out


def _importance(model, columns):
    gain = model.get_booster().get_score(importance_type='total_gain')
    total = sum(gain.values()) or 1.0
    return {f: round(float(gain.get(f'f{i}', 0.0)) / total, 6) for i, f in enumerate(columns)}


def fit_calibration(z, y, w, options, random_state):
    """Platt scaling on the booster logit.

    ``platt``: unconstrained logistic fit (the 07/10 behaviour).
    ``platt_bounded``: slope clipped to [0, calibration_max_slope] with the
    intercept refit for the clipped slope. A slope above 1 amplifies the
    booster's confidence; a negative slope would invert it. Clipping to 0
    degrades to the pooled base rate instead of a confidently wrong model.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    reg = LogisticRegression(C=options['calibration_C'], max_iter=options['calibration_max_iter'],
                             random_state=random_state)
    reg.fit(z.reshape(-1, 1), y, sample_weight=w)
    slope, intercept = float(reg.coef_[0][0]), float(reg.intercept_[0])
    method = options.get('calibration_method', 'platt')
    out = {'method': method, 'pool': options.get('calibration_pool', 'calibration'),
           'unbounded_slope': slope, 'bounded': False}
    if method == 'platt_bounded':
        hi = float(options.get('calibration_max_slope', 1.0))
        clipped = min(max(slope, 0.0), hi)
        if clipped != slope:
            # Newton steps on the intercept only (convex 1-D weighted log loss).
            b = intercept
            for _ in range(50):
                q = 1 / (1 + np.exp(-np.clip(clipped * z + b, -700, 700)))
                grad = float(np.sum(w * (q - y))); hess = float(np.sum(w * q * (1 - q))) + 1e-12
                step = grad / hess; b -= step
                if abs(step) < 1e-10: break
            slope, intercept = clipped, float(b)
            out['bounded'] = True
    return {**out, 'slope': slope, 'intercept': intercept}


def apply_calibration(raw, fit, clip=1e-6):
    import numpy as np
    p = np.clip(raw, clip, 1 - clip)
    z = fit['slope'] * np.log(p / (1 - p)) + fit['intercept']
    return 1 / (1 + np.exp(-np.clip(z, -700, 700)))


def excess_return(row, relative):
    """Economic outcome in pp: relative → mid-referenced excess over the beta-adjusted
    same-minute benchmark (what the label signs); absolute → ask-referenced return."""
    if relative:
        own = mid_return(row.get('endpoint_return_pct'), (row.get('values') or {}).get('spread_pct'))
        bench = row.get('benchmark_return_pct')
        return None if own is None or not eng.number(bench) else own - bench
    v = row.get('endpoint_return_pct')
    return float(v) if eng.number(v) else None


def sign_test_p(successes, n):
    """One-sided binomial P(X >= successes | n, 0.5)."""
    from math import comb
    if n <= 0:
        return None
    return sum(comb(n, i) for i in range(successes, n + 1)) / 2 ** n


def walk_forward_evaluation(usable, *, columns, spec, options, relative):
    """Rolling-origin evaluation: every UTC day with >= ``min_train_days`` earlier days
    is a test fold once (at most ``max_folds`` evenly spaced days). Fold model = same
    booster params, trained on rows before (day start - embargo), calibrated on the
    last ``calibration_fraction`` of that past (same bounded Platt), never on the test
    day. Episodes that touch the test day are removed from the fold's past.

    The verdict is DAY-LEVEL (2026-10-08): pooling days rewards matching day-to-day
    base-rate shifts, not ranking assets within a day (observation 15m: pooled AUC
    0.526 with 0/4 days above 0.5). Reported:
      * median per-day AUC and a one-sided sign test over days (AUC > 0.5);
      * per-day paired Brier improvement vs the fold's base rate, CI by resampling DAYS;
      * economic spread (top vs bottom ``economic_quantile`` excess return, pp), CI by
        resampling DAYS (rows within a day/minute move together);
      * pooled AUC/Brier kept for reference only."""
    import numpy as np
    import xgboost as xgb
    from datetime import timedelta
    from sklearn.metrics import brier_score_loss, roc_auc_score
    wf = spec.get('walk_forward') or {}
    min_days = int(wf.get('min_train_days', 2)); frac = float(wf.get('calibration_fraction', 0.2))
    q = float(wf.get('economic_quantile', 0.1)); embargo = timedelta(seconds=int(spec['embargo_seconds']))
    max_folds = int(wf.get('max_folds', 30))
    rows = sorted(usable, key=lambda r: eng.utc(r['decision_at']))
    times = np.array([eng.utc(r['decision_at']).timestamp() for r in rows])
    dates = [eng.utc(r['decision_at']).date() for r in rows]
    eps = np.array([r['episode_id'] for r in rows], dtype=object)
    Y = np.array([int(r['target']) for r in rows])
    def cell(r, f):
        v = (r.get('values') or {}).get(f)
        return float(v) if eng.number(v) else np.nan
    X = np.array([[cell(r, f) for f in columns] for r in rows], dtype=float).reshape(len(rows), len(columns))
    EX = np.array([np.nan if (e := excess_return(r, relative)) is None else e for r in rows], dtype=float)
    days = sorted(set(dates))
    eligible = days[min_days:]
    if len(eligible) > max_folds:
        pick = np.linspace(0, len(eligible) - 1, max_folds).round().astype(int)
        eligible = [eligible[i] for i in sorted(set(pick.tolist()))]
    date_arr = np.array(dates, dtype=object)
    def weights(mask):
        e = eps[mask]
        _, inv, counts = np.unique(e, return_inverse=True, return_counts=True)
        return 1.0 / counts[inv]
    def logit(p):
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))
    folds = []; per_day = []
    for day in eligible:
        tmask = date_arr == day
        start = times[tmask].min()
        test_eps = set(eps[tmask].tolist())
        pmask = (times < start - embargo.total_seconds()) & ~np.isin(eps, list(test_eps))
        if pmask.sum() < 100 or len(set(Y[pmask])) < 2 or len(set(Y[tmask])) < 2:
            folds.append({'day': day.isoformat(), 'skipped': 'insufficient_past_or_single_class',
                          'test_rows': int(tmask.sum()), 'past_rows': int(pmask.sum())})
            continue
        ptimes = np.sort(times[pmask])
        cut = ptimes[int(len(ptimes) * (1 - frac))]
        cmask = pmask & (times >= cut)
        cal_eps = set(eps[cmask].tolist())
        fmask = pmask & (times < cut - embargo.total_seconds()) & ~np.isin(eps, list(cal_eps))
        if fmask.sum() < 50 or len(set(Y[fmask])) < 2 or len(set(Y[cmask])) < 2:
            folds.append({'day': day.isoformat(), 'skipped': 'insufficient_fit_or_calibration',
                          'test_rows': int(tmask.sum())})
            continue
        fw = weights(fmask)
        model = xgb.XGBClassifier(**{**spec['params'], 'n_jobs': spec['max_threads'], 'objective': 'binary:logistic'})
        model.fit(X[fmask], Y[fmask], sample_weight=fw, verbose=False)
        fitc = fit_calibration(logit(model.predict_proba(X[cmask])[:, 1]), Y[cmask], weights(cmask), options,
                               spec['params']['random_state'])
        p = apply_calibration(model.predict_proba(X[tmask])[:, 1], fitc)
        ty = Y[tmask]; tw = weights(tmask)
        prior = float(np.average(Y[fmask], weights=fw))
        auc = float(roc_auc_score(ty, p, sample_weight=tw))
        brier = float(brier_score_loss(ty, p, sample_weight=tw))
        base = float(brier_score_loss(ty, np.full(len(ty), prior), sample_weight=tw))
        folds.append({'day': day.isoformat(), 'test_rows': int(tmask.sum()), 'test_episodes': len(test_eps),
                      'fit_rows': int(fmask.sum()), 'calibration_rows': int(cmask.sum()), 'auc': auc,
                      'brier': brier, 'baseline_brier': base, 'brier_improvement': base - brier,
                      'up_frequency': float(np.average(ty, weights=tw)), 'train_prior': prior,
                      'calibration_slope': fitc['slope']})
        per_day.append({'p': p, 'y': ty, 'w': tw, 'prior': prior, 'ex': EX[tmask], 'n_eps': len(test_eps)})
    scored = [f for f in folds if 'auc' in f]
    out = {'version': 'pump_walk_forward_daily_v2', 'min_train_days': min_days, 'calibration_fraction': frac,
           'max_folds': max_folds, 'folds': folds, 'scored_days': len(scored)}
    if not scored:
        return {**out, 'pooled': None}
    rng = np.random.default_rng(spec['params']['random_state'])
    reps = int(options['bootstrap_repetitions'])
    n = len(scored)
    aucs = np.array([f['auc'] for f in scored]); imps = np.array([f['brier_improvement'] for f in scored])
    above = int((aucs > 0.5).sum())
    boot_idx = [rng.integers(0, n, n) for _ in range(reps)]
    imp_boot = [float(imps[i].mean()) for i in boot_idx]
    P = np.concatenate([d['p'] for d in per_day]); Yp = np.concatenate([d['y'] for d in per_day])
    W = np.concatenate([d['w'] for d in per_day]); PR = np.concatenate([np.full(len(d['y']), d['prior']) for d in per_day])
    pooled = {'auc_statistic': 'day_median',
              'auc': float(np.median(aucs)), 'day_auc_median': float(np.median(aucs)),
              'day_auc_mean': float(aucs.mean()), 'day_auc_min': float(aucs.min()), 'day_auc_max': float(aucs.max()),
              'days_auc_above_half': above, 'sign_test_p': sign_test_p(above, n),
              'brier_improvement_day_mean': float(imps.mean()),
              'paired_episode_brier_ci95': np.quantile(imp_boot, [.025, .975]).tolist(),
              'ci_scope': 'bootstrap_over_days',
              'brier': float(np.average((Yp - P) ** 2, weights=W)),
              'baseline_brier': float(np.average((Yp - PR) ** 2, weights=W)),
              'pooled_auc_reference_only': float(roc_auc_score(Yp, P, sample_weight=W)),
              'episodes': int(sum(d['n_eps'] for d in per_day)), 'rows': int(len(Yp))}
    # Economic spread with thresholds from all out-of-fold predictions, CI over days.
    allp = np.concatenate([d['p'][~np.isnan(d['ex'])] for d in per_day]) if per_day else np.array([])
    if allp.size >= 20:
        hi, lo = np.quantile(allp, 1 - q), np.quantile(allp, q)
        sums = []
        for d in per_day:
            ok = ~np.isnan(d['ex'])
            pp, ee = d['p'][ok], d['ex'][ok]
            t, b = ee[pp >= hi], ee[pp <= lo]
            sums.append((t.sum(), len(t), b.sum(), len(b), ee.sum(), len(ee)))
        S = np.array(sums, dtype=float)
        def spread(idx):
            s = S[idx].sum(axis=0)
            if s[1] == 0 or s[3] == 0:
                return np.nan
            return s[0] / s[1] - s[2] / s[3]
        tot = S.sum(axis=0)
        diffs = np.array([spread(i) for i in boot_idx]); diffs = diffs[~np.isnan(diffs)]
        day_spreads = np.array([spread(np.array([i])) for i in range(len(S))])
        day_spreads = day_spreads[~np.isnan(day_spreads)]
        pooled['economic'] = {'quantile': q, 'unit': 'pp_excess_over_benchmark' if relative else 'pp_endpoint_return',
                              'all_mean': float(tot[4] / tot[5]) if tot[5] else None,
                              'top_mean': float(tot[0] / tot[1]) if tot[1] else None, 'top_rows': int(tot[1]),
                              'bottom_mean': float(tot[2] / tot[3]) if tot[3] else None, 'bottom_rows': int(tot[3]),
                              'top_minus_bottom': float(spread(np.arange(len(S)))),
                              'top_minus_bottom_ci95': np.quantile(diffs, [.025, .975]).tolist() if diffs.size else None,
                              'days_spread_positive': int((day_spreads > 0).sum()), 'days_with_spread': int(day_spreads.size),
                              'ci_scope': 'bootstrap_over_days'}
    return {**out, 'pooled': pooled}

def model_columns(spec):
    """Booster column order: frozen core features, then optional context."""
    return list(spec['features']) + [f for f in (spec.get('context_features') or []) if f not in spec['features']]


def directional_preview(values, manifest, predict_up):
    """Research-only per-asset output. Never grants validation or effective score."""
    spec = manifest['spec']
    result = {'objective': manifest.get('objective'), 'horizon_minutes': spec['directional_target']['horizon_minutes'],
              'status': 'abstained', 'direction': None, 'score': None, 'probability': None,
              'applied_delta': 0, 'model_id': manifest['experiment_id'], 'validation': manifest['status']}
    if manifest.get('objective') not in OBJECTIVES:
        return {**result, 'reason': 'incompatible_model_objective'}
    if any(not eng.number(values.get(f)) for f in spec['features']):
        return {**result, 'reason': 'missing_or_invalid_features'}
    bounds = manifest['feature_bounds']
    columns = model_columns(spec)
    present = [f for f in columns if eng.number(values.get(f))]
    if any(bounds.get(f) is not None and not bounds[f]['min'] <= values[f] <= bounds[f]['max'] for f in present):
        return {**result, 'reason': 'outside_training_feature_range'}
    p = predict_up([float(values[f]) if eng.number(values.get(f)) else float('nan') for f in columns])
    if not eng.number(p) or not 0 <= p <= 1:
        return {**result, 'reason': 'invalid_model_output'}
    # Candidate values are visibly separated from any validated operational note.
    return {**result, 'reason': 'directional_model_not_independently_validated',
            'research_only': {'candidate_direction': 'up' if p > .5 else 'down' if p < .5 else 'undetermined',
                              'candidate_ordinal_score': 100*p, 'estimated_up_probability': p,
                              'score_semantics': 'research_scale_0_down_50_neutral_100_up_not_validated',
                              'features_at_decision': {f: values.get(f) for f in model_columns(spec)}}}


def load_directional_preview(values, folder):
    """Offline/native inference from a directional artifact, with local evidence."""
    folder=Path(folder)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('objective') not in OBJECTIVES:raise ValueError('Not a directional Pump artifact')
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
        columns=model_columns(manifest['spec'])
        vector=np.array([[float(values[f]) if eng.number(values.get(f)) else np.nan for f in columns]])
        contribution=model.predict(xgb.DMatrix(vector),pred_contribs=True)[0]
        result['research_only']['joint_model_contributions_log_odds']={f:float(contribution[i]) for i,f in enumerate(columns)}
        result['research_only']['explanation_scope']='conditional model contributions, not independent confirmations or causal effects'
    return result
