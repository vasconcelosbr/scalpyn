import test from 'node:test';
import assert from 'node:assert/strict';
import {entryBandView} from './mtfEntryBand';
import type {Analysis,EntryCandidate} from '../components/mtf/types';

const band:EntryCandidate={state:'CONFIRMED',reason:null,timeframe:'1h',low:99,high:101,support_low:99,support_high:100,
  atr:2,proximity_atr:.5,entry:100,evaluated_at:'2026-11-20T12:00:00Z',valid_until:'2026-11-20T12:05:00Z',config_hash:'same',vetoes:[],operational_effect:false};
const analysis={status:'TRIGGERED',vetoes:[],plan:{entry:100},config_hash:'same',evaluated_at_utc:band.evaluated_at,entry_candidate:band} as unknown as Analysis;
const now=Date.parse('2026-11-20T12:01:00Z');

test('confirmed guide requires matching current evaluation and expires without a response',()=>{
  assert.deepEqual(entryBandView(analysis,true,now),band);
  for(const [a,current,time] of [[analysis,false,now],[analysis,true,Date.parse(band.valid_until)],
    [{...analysis,config_hash:'new'},true,now],[{...analysis,evaluated_at_utc:'other'},true,now],
    [{...analysis,vetoes:['BTC_DOWNTREND']},true,now],[{...analysis,plan:null},true,now]] as const){
    const result=entryBandView(a as Analysis,current,time);
    assert.equal(result?.state,'UNAVAILABLE'); assert.equal(result?.low,null);assert.equal(result?.entry,null);
  }
});
test('waiting and blocked guides never become confirmations or change evaluation',()=>{
  for(const state of ['WAITING','BLOCKED'] as const){
    const a={...analysis,status:'NO_TRADE',entry_candidate:{...band,state,entry:null}} as Analysis;
    const before=structuredClone(a);
    assert.equal(entryBandView(a,true,now)?.state,state);
    assert.deepEqual(a,before);
  }
  assert.equal(entryBandView({...analysis,entry_candidate:null},true,now),null);
  assert.equal(entryBandView({...analysis,entry_candidate:{...band,low:NaN}},true,now)?.state,'UNAVAILABLE');
});
