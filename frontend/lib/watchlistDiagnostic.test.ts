import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { WatchlistDecisionTable, type WatchlistDecisionItem } from '../components/watchlist/RejectedAssetTable';
import { Diagnostic, diagnosticHref, diagnosticMetrics, diagnosticTime, exportDiagnostic, text } from './watchlistDiagnostic';

test('diagnostic source times retain seconds in the display timezone and handle missing dates', () => {
  const formatted = diagnosticTime('2026-11-26T12:22:33Z');
  assert.match(formatted,/26\/11\/2026/);
  assert.match(formatted,/09:22:33/);
  assert.equal(diagnosticTime(null),'Horário não disponível');
  assert.equal(diagnosticTime('invalid'),'Horário não disponível');
});

test('complete export preserves null, zero, false, nested evidence and every source', () => {
  const data = { schema_version:'WATCHLIST_ASSET_DIAGNOSTIC_V1', sources: {
    indicators:[
      {timeframe:'4h',scheduler_group:'structural',time:'2026-11-26T12:00:00Z',indicators_json:{ema_20:10,rsi:0,missing:null,flag:false,nested:{atr_pct:0.5}}},
      {timeframe:'4h',scheduler_group:'micro',time:'2026-11-26T12:01:00Z',indicators_json:{volume:8}},
    ], profile_decision:{metrics:{rsi:25}}, mtf_observation:{payload:{indicators:[{name:'rsi',value:35,timeframe:'4h',source_at:'2026-11-26T11:00:00Z'}]}}
  }} as unknown as Diagnostic;
  const metrics = diagnosticMetrics(data);
  assert.deepEqual(metrics.filter(m=>m.name==='rsi').map(m=>m.value),[0,35]);
  assert.equal(metrics.find(m=>m.name==='nested.atr_pct')?.value,0.5);
  assert.equal(text(false),'false'); assert.equal(text(0),'0'); assert.equal(text(null),'Não disponível');
  assert.deepEqual(JSON.parse(exportDiagnostic(data)), data);
  assert.equal(new Set(metrics.map(m=>m.path)).size,metrics.length);
});

test('every level and decision state links to its own diagnostic without losing summary', () => {
  for (const stage of ['POOL','L1','L2','L3']) for (const status of ['approved','rejected','awaiting_data'] as const) {
    const item: WatchlistDecisionItem = {symbol:'BTC_USDT',status,stage,profile_id:'profile',data_reasons:[],failed_indicators:[],conditions:[],current_values:{},expected_values:{},
      details:{filters:[],indicators:[],conditions:[],current_values:{},expected_values:{},evaluation_trace:[]}};
    const html = renderToStaticMarkup(createElement(WatchlistDecisionTable,{items:[item],loading:false,watchlistId:'owned-id',expandedRow:null,onExpandedRowChange:()=>{}}));
    assert.match(html,/href="\/watchlist\/owned-id\/BTC_USDT"/);
    assert.match(html,/Resumo de BTC_USDT/);
  }
  assert.equal(diagnosticHref('owned-id','ABC/USDT'),'/watchlist/owned-id/ABC%2FUSDT');
});
