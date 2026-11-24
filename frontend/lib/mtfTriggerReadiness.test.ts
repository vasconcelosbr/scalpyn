import test from 'node:test';
import assert from 'node:assert/strict';
import { remainingToTrigger } from './mtfTriggerReadiness';
import type { TriggerCheck } from '../components/mtf/types';

const row = (override: Partial<TriggerCheck>): TriggerCheck => ({id:'rsi',label:'RSI',role:'required',timeframe:'5m',actual:45,expected:50,operator:'>=',unit:'',state:'FAIL',gap:null,note:null,...override});
test('uses direction and strict comparison without inventing a price target', () => {
  assert.deepEqual(remainingToTrigger(row({})), {action:'Subir',amount:5});
  assert.deepEqual(remainingToTrigger(row({actual:55,operator:'<='})), {action:'Cair',amount:5});
  assert.deepEqual(remainingToTrigger(row({operator:'>'})), {action:'Subir mais de',amount:5});
  assert.equal(remainingToTrigger(row({operator:'>',actual:50})).amount, undefined);
  assert.deepEqual(remainingToTrigger(row({operator:'between',expected:[50,60]})), {action:'Subir',amount:5});
  assert.equal(remainingToTrigger(row({operator:'crosses_above'})).amount, undefined);
});
test('missing, waiting and categorical values never become fabricated numeric progress', () => {
  for (const override of [{actual:null,state:'UNAVAILABLE'}, {actual:NaN}, {actual:false,expected:true,operator:'='}, {state:'WAITING'}, {state:'DISABLED'}, {state:'PASS'}] as Partial<TriggerCheck>[])
    assert.equal(remainingToTrigger(row(override)).amount, undefined);
  assert.equal(remainingToTrigger(row({state:'WAITING',note:'Aguardando padrão'})).action,'Aguardando padrão');
});
