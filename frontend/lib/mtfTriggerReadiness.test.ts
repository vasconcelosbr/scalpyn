import test from 'node:test';
import assert from 'node:assert/strict';
import { formingPreviewCurrent, remainingToTrigger } from './mtfTriggerReadiness';
import type { Analysis, FormingPreview, TriggerCheck } from '../components/mtf/types';

const row = (override: Partial<TriggerCheck>): TriggerCheck => ({id:'rsi',label:'RSI',role:'required',timeframe:'5m',actual:45,expected:50,operator:'>=',unit:'',state:'FAIL',gap:null,note:null,...override});
test('uses direction and strict comparison without inventing a price target', () => {
  assert.deepEqual(remainingToTrigger(row({})), {action:'Subir',amount:5});
  assert.deepEqual(remainingToTrigger(row({actual:55,operator:'<='})), {action:'Cair',amount:5});
  assert.deepEqual(remainingToTrigger(row({operator:'>'})), {action:'Subir mais de',amount:5});
  assert.equal(remainingToTrigger(row({operator:'>',actual:50})).amount, undefined);
  assert.deepEqual(remainingToTrigger(row({operator:'between',expected:[50,60]})), {action:'Subir',amount:5});
  assert.equal(remainingToTrigger(row({operator:'crosses_above'})).amount, undefined);
});

test('preview expires at the candle boundary and never replaces official state', () => {
  const analysis = {symbol:'BTC_USDT', config_hash:'config',status:'ARMED'} as Analysis;
  const preview: FormingPreview = {version:'mtf_forming_preview_v1',symbol:analysis.symbol,config_hash:'config',
    state:'AVAILABLE',reason:null,confirmation:'CLOSE_ONLY',operational_effect:false,refresh_seconds:15,
    candle_open_at_utc:'2026-11-24T12:00:00Z',candle_close_at_utc:'2026-11-24T12:05:00Z',
    observed_at_utc:'2026-11-24T12:04:50Z',valid_until:'2026-11-24T12:05:00Z',diagnostics:{checks:[],patterns:[]}};
  const now = Date.parse('2026-11-24T12:04:55Z');
  assert.equal(formingPreviewCurrent(preview,analysis,now),true);
  assert.equal(formingPreviewCurrent(preview,analysis,Date.parse(preview.valid_until)),false);
  for(const patch of [{symbol:'ETH_USDT'},{config_hash:'other'},{state:'UNAVAILABLE'}, {diagnostics:null},
    {candle_open_at_utc:'invalid'}, {observed_at_utc:'2026-11-24T12:05:00Z'}] as Partial<FormingPreview>[])
    assert.equal(formingPreviewCurrent({...preview,...patch},analysis,now),false);
  assert.equal(analysis.status,'ARMED');
});
test('missing, waiting and categorical values never become fabricated numeric progress', () => {
  for (const override of [{actual:null,state:'UNAVAILABLE'}, {actual:NaN}, {actual:false,expected:true,operator:'='}, {state:'WAITING'}, {state:'DISABLED'}, {state:'PASS'}] as Partial<TriggerCheck>[])
    assert.equal(remainingToTrigger(row(override)).amount, undefined);
  assert.equal(remainingToTrigger(row({state:'WAITING',note:'Aguardando padrão'})).action,'Aguardando padrão');
  assert.equal(remainingToTrigger(row({id:'previous_direction'})).amount, undefined);
  assert.match(remainingToTrigger(row({id:'previous_direction'})).action, /já fechado/);
});
