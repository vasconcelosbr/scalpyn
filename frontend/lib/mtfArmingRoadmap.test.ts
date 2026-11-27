import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MTFArmingRoadmap } from '../components/mtf/MTFArmingRoadmap';
import { armingRoadmap, structuralReferences, watchlistArmingAnalysis } from './mtfArmingRoadmap';
import type { Analysis, Asset, TriggerCheck } from '../components/mtf/types';

function alignment(id:string,tf:'4h'|'1h'|'15m', highs:number[],lows:number[]):TriggerCheck {
  return {id,label:'Direção',actual:'RANGE',expected:'UP',operator:'=',unit:'',role:'required',timeframe:tf,
    state:'FAIL',gap:null,note:null,explanation:{version:'mtf_check_explanation_v1',description:'Direção da estrutura',
      method:'Pivôs confirmados',timeframes:[tf],parameters:{pivot_right:3},evidence:{complete:true,closed_at:100,
        pivots:[...highs.map((price,i)=>({kind:'H',price,time:i,confirmed_at:i+10,label:'H'})),
          ...lows.map((price,i)=>({kind:'L',price,time:i,confirmed_at:i+10,label:'L'}))]}}};
}
function snapshot():Analysis {
  const checks = [alignment('audit:structure_alignment','4h',[106.37,97.68],[89.8,92.31]),
    alignment('audit:zone_alignment','1h',[97.36,97.93],[95.77,92.1])];
  checks.push({...checks[0],id:'rules:ARMADO',state:'PASS',actual:true,expected:true,explanation:undefined,
    rule_tree:{logic:'OR',state:'PASS',children:[{state:'FAIL',check_id:'alternative'}]}});
  checks.push({...checks[0],id:'alternative',role:'context'});
  return {symbol:'AAVE_USDT',status:'NO_TRADE',config_hash:'same',evaluated_at_utc:'2026-11-27T20:30:00Z',
    trigger_diagnostics:{version:'mtf_trigger_diagnostics_v1',status:'NO_TRADE',config_hash:'same',checks,patterns:[]}} as unknown as Analysis;
}

test('AAVE lateral requires a higher confirmed H4 high and H1 low without inventing a price target',()=>{
  const a=snapshot(), before=structuredClone(a), view=armingRoadmap(a,a.symbol,0)!;
  assert.deepEqual(structuralReferences(view.arming[0]),[
    {kind:'H',previous:106.37,latest:97.68,ascending:false},{kind:'L',previous:89.8,latest:92.31,ascending:true}]);
  assert.deepEqual(structuralReferences(view.arming[1]),[
    {kind:'H',previous:97.36,latest:97.93,ascending:true},{kind:'L',previous:95.77,latest:92.1,ascending:false}]);
  assert.equal(view.achieved,false);
  assert.equal(view.armedRule?.state,'PASS');
  assert.equal(view.remaining.length,0); // failing OR child is contextual, not a veto
  assert.deepEqual(a,before);
});

test('incomplete, disabled, unavailable or unconfirmed pivots never supply structural prices',()=>{
  const row=alignment('audit:zone_alignment','15m',[11,10],[9,8]);
  assert.ok(structuralReferences(row).every(r=>!r.ascending));
  for(const state of ['UNAVAILABLE','DISABLED','WAITING'] as const) assert.deepEqual(structuralReferences({...row,state}),[]);
  row.explanation!.evidence!.pivots![0].confirmed_at=101;
  assert.deepEqual(structuralReferences(row),[]);
  row.explanation!.evidence!.complete=false;
  assert.deepEqual(structuralReferences(row),[]);
});

test('symbol, version and freshness mismatches hide all arming references',()=>{
  const a=snapshot();
  assert.equal(armingRoadmap(a,'ETH_USDT',0),null);
  assert.equal(armingRoadmap({...a,config_hash:'changed'},a.symbol,0),null);
  assert.equal(armingRoadmap({...a,status:'ARMED'},a.symbol,0),null);
  a.entry_candidate={valid_until:'invalid'} as Analysis['entry_candidate'];
  assert.equal(armingRoadmap(a,a.symbol,0),null);
  a.entry_candidate={valid_until:'2026-11-27T20:45:00Z'} as Analysis['entry_candidate'];
  assert.equal(armingRoadmap(a,a.symbol,Date.parse(a.entry_candidate!.valid_until)),null);
  delete a.entry_candidate;
  a.trigger_diagnostics!.checks.push({...a.trigger_diagnostics!.checks[0],id:'evaluation_current',state:'UNAVAILABLE'});
  assert.equal(armingRoadmap(a,a.symbol,0),null);
});

test('rendered roadmap names timeframes, levels, confirmation and blocking reason',()=>{
  const a=snapshot();
  const html=renderToStaticMarkup(createElement(MTFArmingRoadmap,{analysis:a,symbol:a.symbol,date:v=>v??'—'}));
  for(const text of ['O que falta para ARMAR','4H','1H','novo topo acima de 97,68','novo fundo acima de 92,1',
    '3 candles fechados','Regra ARMADO atendida','Condição não atendida','Tocar ou romper o nível sozinho']) assert.ok(html.includes(text),text);
  assert.ok(!html.includes('data-arming-check="alternative"'));
  const changed=alignment('audit:zone_alignment','15m',[11,10],[9,8]);
  a.trigger_diagnostics!.checks=[changed];
  assert.ok(renderToStaticMarkup(createElement(MTFArmingRoadmap,{analysis:a,symbol:a.symbol,date:v=>v??'—'})).includes('15M'));
});

test('armed is taken from canonical status even with trigger confirmations pending',()=>{
  const a=snapshot(); a.status='ARMED';a.trigger_diagnostics!.status='ARMED';
  a.trigger_diagnostics!.checks=[{...a.trigger_diagnostics!.checks[0],id:'rules:GATILHO',state:'FAIL'}];
  const view=armingRoadmap(a,a.symbol,0)!;
  assert.equal(view.achieved,true);assert.equal(view.arming.length,0);assert.equal(view.remaining[0].id,'rules:GATILHO');
});

test('current watchlist evidence renders before candles without using another profile or stale version',()=>{
  const a=snapshot(); a.profile_id='profile';a.vetoes=[];
  a.entry_candidate={state:'BLOCKED',reason:null,timeframe:'1h',low:95,high:98,support_low:95,support_high:96,
    atr:1,proximity_atr:1.4,entry:null,evaluated_at:a.evaluated_at_utc,valid_until:'2026-11-27T20:45:00Z',
    config_hash:a.config_hash,vetoes:[],operational_effect:false};
  const asset:Asset={symbol:a.symbol,profile_id:'profile',mtf:a}, now=Date.parse('2026-11-27T20:31:00Z');
  assert.equal(watchlistArmingAnalysis(asset,a.symbol,'',now),a);
  assert.equal(watchlistArmingAnalysis(asset,a.symbol,'profile',now),a);
  assert.equal(watchlistArmingAnalysis(asset,a.symbol,'other',now),undefined);
  assert.equal(watchlistArmingAnalysis({...asset,profile_id:'other'},a.symbol,'',now),undefined);
  assert.equal(watchlistArmingAnalysis(asset,'ETH_USDT','',now),undefined);
  assert.equal(watchlistArmingAnalysis(asset,a.symbol,'',Date.parse(a.entry_candidate.valid_until)),undefined);
  for (const veto of ['CONFIG_CHANGED','EVALUATION_STALE']) {
    assert.equal(watchlistArmingAnalysis({...asset,mtf:{...a,vetoes:[veto]}},a.symbol,'',now),undefined);
  }
  assert.equal(watchlistArmingAnalysis({...asset,mtf:{...a,entry_candidate:{...a.entry_candidate,config_hash:'old'}}},a.symbol,'',now),undefined);
  assert.equal(watchlistArmingAnalysis({...asset,mtf:{...a,entry_candidate:null}},a.symbol,'',now),undefined);
});
