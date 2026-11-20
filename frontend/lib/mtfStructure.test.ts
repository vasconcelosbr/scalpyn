import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {currentStructure,conflictText,structureTooltip} from './mtfStructure';
import {MTFStructurePanel} from '../components/mtf/MTFStructurePanel';
import type {Analysis,StructureContext} from '../components/mtf/types';

const analysis={config_hash:'same',evaluated_at_utc:'2026-11-20T18:00:00Z',context_4h:{trend:'UP',last_pivots:['HH','HL']}} as Analysis;
const frame={trend:'UP',pivots:[],last_hl:null,last_lh:null,last_pivot_age_candles:3,atr:1,ema50:100,closed_close:105,extension_atr:5,live_change_pct:-6};
const context={config_hash:'same',evaluated_at:analysis.evaluated_at_utc,valid_until:'2999-01-01T00:00:00Z',as_of:analysis.evaluated_at_utc,
  frames:{'4h':frame,'1h':frame,'5m':{...frame,trend:'DOWN'}},invalidation:null,invalidation_distance_atr:null,
  conflict:true,conflicts:[{kind:'structure',timeframe:'5m',state:'DOWN',sequence:['LH','LL'],ratio:null}],operational_effect:false} as unknown as StructureContext;
test('structure retains Alta while conflict neutralizes green and keeps live candle separate',()=>{
  const html=renderToStaticMarkup(createElement(MTFStructurePanel,{analysis,context}));
  for(const expected of ['Estrutura 4H','Alta','HH·HL','Conflito MTF','Confirmado há 3 candles 4H','+5 ATR da EMA50','contra a estrutura: -6%','não gera sinal',structureTooltip])assert(html.includes(expected),expected);
  assert(!html.includes('class="positive"'));assert(!html.includes('esticada'));assert(!html.includes('extrema'));
  assert(conflictText(context).includes('5M: Baixa · LH·LL'));
});
test('missing and expired values remain unavailable without losing the existing structural label',()=>{
  const html=renderToStaticMarkup(createElement(MTFStructurePanel,{analysis}));
  assert(html.includes('Alta'));assert(html.includes('indisponível'));assert(!html.includes('+0'));
  assert.equal(currentStructure(context,analysis,Date.parse(context.valid_until)),null);
  assert.equal(currentStructure(context,{...analysis,config_hash:'changed'},Date.now()),null);
  const missing=renderToStaticMarkup(createElement(MTFStructurePanel,{analysis:{...analysis,context_4h:{...analysis.context_4h,trend:'RANGE',last_pivots:[]}}}));
  assert(!missing.includes('Lateral'));
});
