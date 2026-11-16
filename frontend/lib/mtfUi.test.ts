import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeChart, visibleMarks, readingLabel, reasonLabel, type ChartData, type Presentation, type Preferences, type Frame } from '../components/mtf/types';

const preferences:Preferences={theme:'dark',timezone:'UTC',sound:false,aligned_only:false};
test('presentation translates stored enums and does not expose unknown veto codes',()=>{
  assert.equal(readingLabel('Estrutura: UP; gatilho ENGULF_BULL.'),'Estrutura: alta; gatilho Engolfo de alta.');
  assert.equal(reasonLabel('PRIVATE_UNKNOWN_CODE'),'Condição de análise indisponível');
});
test('delta revises live candle, trims expired history and deletes overlays',()=>{
  const candle=(time:number,close:number)=>({time,open:1,high:4,low:1,close,volume:1,closed:false});
  const frame={candles:[candle(1,2),candle(2,3)],indicators:{rsi:[{time:1,value:40},{time:2,value:50}]},patterns:[{time:1,pattern:'DOJI',metrics:{}}],pivots:[],stale:false,source:'gate.io'};
  const original={symbol:'SOL_USDT',frames:{'4h':frame,'1h':frame,'5m':frame},analysis:{config_hash:'a'},profile:null} as unknown as ChartData;
  const next={...frame,window_start:2,candles:[candle(2,4)],indicators:{rsi:[{time:2,value:60}]},patterns:[],patterns_removed:[[1,'DOJI']] as [number,string][]};
  const delta={...original,transport:'delta',frames:{'4h':next,'1h':next,'5m':next}} as ChartData;
  const merged=mergeChart(original,delta);
  assert.deepEqual(merged.frames['5m'].candles,[candle(2,4)]);
  assert.deepEqual(merged.frames['5m'].indicators.rsi,[{time:2,value:60}]);
  assert.deepEqual(merged.frames['5m'].patterns,[]);
  assert.equal(original.frames['5m'].candles.length,2);
  assert.throws(()=>mergeChart({...original,symbol:'BTC_USDT'},delta));
  original.frames['5m'].zones=[{type:'SUPPORT',low:1,high:2,touches:2,available_at:1}];
  assert.deepEqual(mergeChart(original,delta).frames['5m'].zones,original.frames['5m'].zones);
  delta.frames['5m'].zones=[];
  assert.deepEqual(mergeChart(original,delta).frames['5m'].zones,[]);
});

test('marker budget prioritizes current trigger and respects overlay toggles',()=>{
  const config={markers_max:4,markers_recent_signals:2,pivots_per_type:2,marker_min_range_atr:0.5} as Presentation;
  const frame={candles:[{time:20,open:1,high:3,low:1,close:2,volume:1,closed:true}],indicators:{},stale:false,source:'fixture',patterns:Array.from({length:20},(_,time)=>({time,pattern:'ENGULF_BULL',metrics:{atr:1,range:2}})),
    pivots:Array.from({length:20},(_,time)=>({time,price:1,kind:'H',label:'HH',confirmed_at:time}))} as Frame;
  const trigger={time:20,pattern:'ENGULF_BULL',metrics:{}};
  const marks=visibleMarks(frame,config,preferences,trigger);
  assert.equal(marks.length,4);assert(marks.some(m=>m.time===20&&m.pattern));
  assert.equal(visibleMarks(frame,config,{...preferences,show_patterns:false,show_pivots:false},trigger).length,0);
});
