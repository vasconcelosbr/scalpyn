import test from 'node:test';
import assert from 'node:assert/strict';
import { applyLivePreview, retainLivePreview, type PreviewEvent } from './mtfLivePreview';
import type { ChartData, FormingPreview } from '../components/mtf/types';

const candle = {time: 100, open:10, high:12, low:9, close:11, volume:5, closed:false};
const preview = {symbol:'BTC_USDT',config_hash:'cfg',state:'AVAILABLE',delivery:'stream',
  observed_at_utc:new Date().toISOString(),valid_until:new Date(Date.now()+60000).toISOString(),
  confirmation:'CLOSE_ONLY',operational_effect:false,candle_open_at_utc:'open'} as FormingPreview;
const data = {symbol:'BTC_USDT',analysis:{status:'ARMED',config_hash:'cfg'},
  frames:{'5m':{candles:[candle]}},forming_preview:{...preview,delivery:undefined}} as unknown as ChartData;
const event:PreviewEvent = {type:'preview',symbol:'BTC_USDT',config_hash:'cfg',timeframe:'5m',
  candle:{...candle,close:12},preview};

test('stream replaces only preview and forming candle, preserving official object', () => {
  const next = applyLivePreview(data,event)!;
  assert.equal(next.analysis,data.analysis);
  assert.equal(next.analysis.status,'ARMED');
  assert.equal(next.frames['5m'].candles.at(-1)!.close,12);
  assert.equal(data.frames['5m'].candles.at(-1)!.close,11);
});
test('wrong context, older events and closed candles cannot update the projection', () => {
  for(const patch of [{symbol:'ETH_USDT'},{config_hash:'other'},{timeframe:'1m'},
    {candle:{...candle,closed:true}}, {candle:{...candle,time:400}},
    {preview:{...preview,operational_effect:true}},
    {preview:{...preview,observed_at_utc:'2000-01-01'}}])
    assert.equal(applyLivePreview(data,{...event,...patch} as PreviewEvent),data);
});
test('REST recovery cannot overwrite live OHLCV with a cached candle; rollover can', () => {
  const live=applyLivePreview(data,event)!;
  const merged=retainLivePreview(live,{...data,analysis:{...data.analysis},forming_preview:{...data.forming_preview!,observed_at_utc:new Date(Date.now()+1000).toISOString()}});
  assert.equal(merged.frames['5m'].candles.at(-1)!.close,12);
  assert.notEqual(merged.analysis,live.analysis);
  const rollover={...data,forming_preview:{...preview,candle_open_at_utc:'new'}};
  assert.equal(retainLivePreview(live,rollover),rollover);
  const offline={...live,forming_preview:{...preview,state:'UNAVAILABLE' as const}};
  assert.equal(retainLivePreview(offline,data),data);
});
