import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MTFBitcoinTicker} from '../components/mtf/MTFBitcoinTicker';
import {settingsDraft,withConfigValue,settingsForSave} from './mtfSettings';
import type {Analysis,Bootstrap,MTFConfig} from '../components/mtf/types';

const base={symbol:'ETH_USDT',observed_at_utc:'2026-11-21T18:00:00Z',vetoes:[],btc_trend:'DOWN'} as unknown as Analysis;
const render=(analysis:Analysis)=>renderToStaticMarkup(createElement(MTFBitcoinTicker,{refresh:0,timezone:'UTC',analysis}));
test('legacy BTC reading remains 4H and does not fabricate 1H',()=>{
 const html=render(base);
 assert(html.includes('Tendência do Bitcoin · 4H: Baixa'));
 assert(html.includes('Tendência do Bitcoin · 1H: Indisponível'));
});
test('both frozen BTC states display with active hourly veto; stale readings disappear',()=>{
 const a={...base,btc_trend:'RANGE',btc_trend_timeframe:'1h',
   btc_context:{active_timeframe:'1h',veto_enabled:true,frames:{'4h':{trend:'DOWN'},'1h':{trend:'RANGE'}}}} as Analysis;
 const html=render(a);
 assert(html.includes('Tendência do Bitcoin · 4H: Baixa'));
 assert(html.includes('Tendência do Bitcoin · 1H: Lateral'));
 assert.match(html,/Tendência do Bitcoin · 1H<small> · Veto ativo/);
 assert(!render({...a,vetoes:['DATA_STALE:5m']}).includes('1H: Lateral'));
});
test('timeframe setting preserves all other saved fields',()=>{
 const current={schema_version:'mtf_chart_v1',atr_period:14,btc_trend_timeframe:'4h',min_score:6,veto_filters:{btc_downtrend:true},presentation:{}} as unknown as MTFConfig;
 const boot={preset:current,presentation_preset:{}} as Bootstrap;
 const draft=settingsDraft(current,boot);
 const saved=settingsForSave(withConfigValue(draft,'btc_trend_timeframe','1h'),current,boot);
 assert.deepEqual({...saved,btc_trend_timeframe:'4h'},draft);
 assert.equal(current.btc_trend_timeframe,'4h');
});

