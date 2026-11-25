"""Read-only frozen replay comparison. Input: owner-scoped JSONL export."""
import argparse
import json
from collections import Counter
from copy import deepcopy
from app.schemas.mtf_chart import MTFChartConfig
from app.services.mtf_chart_engine import evaluate
from app.services.mtf_replay import replay
from app.services.mtf_risk_audit import finalize


def compare(row):
    snapshot = row['snapshot']
    inputs = snapshot.get('replay_inputs')
    if not inputs:
        return dict(id=str(row['id']), excluded='REPLAY_INPUTS_MISSING')
    original = replay(snapshot)
    if original['status'] != 'MATCH':
        return dict(id=str(row['id']), excluded='ORIGINAL_REPLAY_MISMATCH', original=original)
    cfg = MTFChartConfig.model_validate(inputs['config'])
    before = evaluate(inputs['symbol'],inputs['frames'],cfg,inputs['cutoff'],inputs['spread_pct'],
                      inputs['btc_trend'],rule_series=inputs.get('rule_series'),flow=inputs.get('flow'))
    cfg.context_audit.enabled = True
    after = finalize(deepcopy(before),inputs['frames'],cfg,inputs['cutoff'])
    checks = after['risk_audit']['checks']
    return dict(id=str(row['id']),symbol=row['symbol'],triggered_at=row['triggered_at'],
                original_match=True,original_status=before['status'],audited_status=after['status'],
                failures=after['risk_audit']['reasons'],checks=checks,
                hypothetical=True,stop_policy='FROZEN_ORIGINAL_NOT_RESIZED',
                baseline=row['baseline'],target=row['target'],stop=row['stop'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--output', required=True)
    parser.add_argument('--outcomes', help='Optional JSONL of stored replay progress and candles')
    args=parser.parse_args()
    rows=[]
    with open(args.input,encoding='utf-8-sig') as source:
        for line in source:
            if line.strip().startswith('{'): rows.append(json.loads(line))
    results=[compare(row) for row in rows]
    outcomes={}
    if args.outcomes:
        with open(args.outcomes,encoding='utf-8-sig') as source:
            outcomes={r['id']:r for line in source if line.strip().startswith('{') for r in [json.loads(line)]}
    source_by_id={str(r['id']):r for r in rows}
    usable=[r for r in results if 'excluded' not in r]
    # Chronological groups, no threshold fitting. Shared cutoff prevents same-time
    # correlated observations from appearing in both partitions.
    times=sorted(set(r['triggered_at'] for r in usable))
    split=times[len(times)//2] if times else None
    for r in usable: r['partition']='validation' if r['triggered_at']>=split else 'analysis'
    for r in usable:
        future=outcomes.get(r['id'],{})
        bars=future.get('candles') or []
        progress=future.get('progress') or {}
        outcome=progress.get('result') or {}
        cfg=MTFChartConfig.model_validate(source_by_id[r['id']]['snapshot']['replay_inputs']['config'])
        fee,slip=cfg.fee_per_side_pct/100,cfg.slippage_pct/100
        cost=r['baseline']*(1+slip)*(1+fee)
        price=outcome.get('price')
        r['outcome_evidence']=dict(status=progress.get('status','UNAVAILABLE'),issue=progress.get('issue'),
            gross_return_pct=outcome.get('return_pct'),
            hypothetical_net_pct=(price*(1-slip)*(1-fee)/cost-1)*100 if price else None,
            max_excursion_pct=(max(c['high'] for c in bars)/r['baseline']-1)*100 if bars else None,
            min_excursion_pct=(min(c['low'] for c in bars)/r['baseline']-1)*100 if bars else None,
            scope='stored candles until original replay termination; intrabar ordering unknown',
            hypothetical_net_formula='(exit*(1-slippage)*(1-fee)/(baseline*(1+slippage)*(1+fee))-1)*100',
            fee_per_side_pct=cfg.fee_per_side_pct,slippage_pct=cfg.slippage_pct,
            realized_exchange_fills=False)
    unique={}
    for r in usable: unique.setdefault((r['symbol'],r['triggered_at']),r)
    partitions={name:[r for r in unique.values() if r['partition']==name] for name in ['analysis','validation']}
    report=dict(source_count=len(rows),usable_count=len(usable),
                unique_symbol_close_count=len(unique),
                partition_outcomes={name:dict(Counter(r['outcome_evidence']['status'] for r in group)) for name,group in partitions.items()},
                exclusions=dict(Counter(r['excluded'] for r in results if 'excluded' in r)),
                independent_blocks=dict(Counter(reason for r in usable for reason in r['failures'])),
                final_statuses=dict(Counter(r['audited_status'] for r in usable)),
                chronological_split=split,rows=results,
                financial_efficacy='NOT_ESTABLISHED',
                limitations=['Frozen indicators are replayed, not independently recalculated.',
                             'Observed exchange fills are not supplied; no realized PnL inferred.',
                             'Original stop is retained to isolate audit policy effects.'])
    with open(args.output,'w',encoding='utf-8') as target: json.dump(report,target,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))


if __name__=='__main__': main()
