"""Read-only Gate input/canonical calculation parity. No DB writes or orders.

python -m scripts.verify_mtf_chart_market_parity --inputs ../.env.mtf-inputs.json --output ../docs/evidence/mtf-market-parity.json
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import pandas as pd
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.feature_engine import FeatureEngine
from app.services.mtf_chart_engine import TF_SECONDS, closed_candles, indicator_history
from app.services.mtf_chart_service import market_candles, calculation_window


async def main(args):
    inputs = json.loads(Path(args.inputs).read_text(encoding='utf-8-sig'))
    config = inputs['indicator_config']
    cfg = MTFChartConfig.model_validate(experimental_preset())
    symbols = list(dict.fromkeys(inputs['symbols'] + ['ETH_USDT','LINK_USDT','DOGE_USDT','AVAX_USDT','ADA_USDT','SUI_USDT']))[:10]
    engine = FeatureEngine(config)
    families = {f for f in ('ema','rsi','adx','atr') if (config.get(f) or {}).get('enabled')}
    rows = []
    for symbol in symbols:
        raw = await asyncio.gather(*(market_candles(symbol, tf, cfg) for tf in cfg.active_timeframes()))
        for tf, candles in zip(cfg.active_timeframes(), raw):
            closed = closed_candles(candles, tf, int(time.time()))
            window = calculation_window(config, tf)
            if len(closed) < window:
                rows.append({'symbol':symbol,'timeframe':tf,'pass':False,'reason':'INSUFFICIENT_CANDLES','closed_count':len(closed),'window':window})
                continue
            # Last prefix uses the exact producer sample; numerical series is
            # checked against a direct call to the owning calculation.
            history = await asyncio.to_thread(indicator_history, closed[-window-1:], config, families, window)
            expected = {}
            for family in sorted(families):
                expected.update(getattr(engine, '_calc_'+family)(pd.DataFrame(closed[-window:])))
            comparisons = {key:{'chart':history[key][-1]['value'] if history.get(key) else None,'canonical':float(value)}
                           for key,value in expected.items() if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)}
            passed = bool(comparisons) and all(c['chart'] == c['canonical'] for c in comparisons.values())
            rows.append({'symbol':symbol,'timeframe':tf,'source':'gate.io','closed_count':len(closed),'window':window,'candle_time':closed[-1]['time'],'pass':passed,'comparisons':comparisons})
        print(symbol, 'PASS' if all(r['pass'] for r in rows if r['symbol']==symbol) else 'FAIL', flush=True)
    result = {'observed_at':datetime.now(timezone.utc).isoformat(),'scope':'Same closed Gate inputs and configured producer windows; not a comparison to differently timed stored pipeline snapshots.',
              'indicator_config':config,'rows':rows,'all_pass':all(r['pass'] for r in rows),'mode':'READ_ONLY'}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    if not result['all_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--output', required=True)
    asyncio.run(main(parser.parse_args()))
