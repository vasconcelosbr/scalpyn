import {test} from 'node:test';
import assert from 'node:assert/strict';
import {settingsDraft, settingsForSave, targetLabel, targetPoints, validateSettings, withConfigValue} from './mtfSettings';
import {mergeChart, type Bootstrap, type Candle, type ChartData, type ConfigSchema, type MTFConfig} from '../components/mtf/types';

const candle = (time:number, close:number):Candle=>({time,close,open:close,high:close,low:close,volume:1,closed:false});
test('Target follows candle closes including live updates, with no warmup or fabricated gaps',()=>{
  assert.deepEqual(targetPoints([candle(300,100),candle(900,0.004327)],1),[{time:300,value:101},{time:900,value:0.004327*1.01}]);
  assert.deepEqual(targetPoints([candle(300,NaN),candle(600,0)],1),[]);
  assert.deepEqual(targetPoints([candle(300,1)],undefined),[]);
  assert.deepEqual(targetPoints([],1),[]);
  assert.equal(targetLabel(1),'Target1%');
  assert.equal(targetLabel(1.5),'Target1,5%');
  const frame={candles:[candle(300,100)],indicators:{},patterns:[],pivots:[],source:'fixture',stale:false};
  const previous={symbol:'TEST_USDT',profile:null,analysis:{config_hash:'same'},frames:{'4h':frame,'1h':frame,'5m':frame}} as unknown as ChartData;
  const delta={...previous,transport:'delta',frames:Object.fromEntries(['4h','1h','5m'].map(tf=>[tf,{...frame,candles:[candle(300,102),candle(600,105)]}]))} as unknown as ChartData;
  assert.deepEqual(targetPoints(mergeChart(previous,delta).frames['5m'].candles,1),[{time:300,value:102*1.01},{time:600,value:105*1.01}]);
  assert.equal(previous.frames['5m'].candles[0].close,100);
});

const config={schema_version:'mtf_chart_v1',atr_period:null,force_patterns:['ENGULF_BULL'],rejection_patterns:['PIN_BULL'],trigger_patterns:['ENGULF_BULL'],
  candle_limits:{'4h':100,'1h':100,'5m':100},score_weights:{trend:2,force:1,zone:2,rejection:1,trigger:2,volume:1,rr:1},min_score:7,min_rr_net:2,rr_bonus_threshold:3,
  presentation:{target_pct:1,retry_initial_seconds:3,retry_max_seconds:30}} as unknown as MTFConfig;
const boot={preset:config,presentation_preset:config.presentation,inherited_atr:{enabled:true,period:14}} as Bootstrap;

test('entry band is editable without changing inherited ATR, score or veto rules',()=>{
  const context={...boot,presentation_preset:{...boot.presentation_preset,entry_band_enabled:true}};
  const draft=settingsDraft(config,context);
  assert.equal(draft.presentation?.entry_band_enabled,true);
  const changed=settingsForSave(withConfigValue(draft,'presentation.entry_band_enabled',false),config,context);
  const {presentation,...rules}=changed;
  const {presentation:old,...original}=config;
  assert.deepEqual(rules,original);
  assert.deepEqual(presentation,{...old,entry_band_enabled:false});
});

test('fear and greed controls preserve rules and validate source freshness',()=>{
  const presentation={...boot.presentation_preset,fear_greed:{enabled:true,refresh_seconds:900,max_age_seconds:3600}};
  const context={...boot,presentation_preset:presentation};
  const draft=settingsDraft(config,context);
  const changed=settingsForSave(withConfigValue(draft,'presentation.fear_greed.refresh_seconds',1800),config,context);
  const {presentation:visual,...rules}=changed;
  const {presentation:oldVisual,...original}=config;
  assert.deepEqual(rules,original);
  assert.equal(visual?.fear_greed?.refresh_seconds,1800);
  const invalid=withConfigValue(changed,'presentation.fear_greed.max_age_seconds',900);
  assert.match(validateSettings(invalid,{})['presentation.fear_greed.max_age_seconds'],/intervalo de consulta/);
});

test('veto draft toggles preserve saved thresholds and cancellation baseline',()=>{
  const saved={...config,min_score:0,slippage_pct:.1,atr_period:14,veto_filters:{btc_downtrend:true,spread:true}};
  const draft=settingsDraft(saved,{...boot,preset:saved});
  const changed=settingsForSave(withConfigValue(draft,'veto_filters.btc_downtrend',false),saved,boot);
  assert.deepEqual(changed,{...saved,veto_filters:{btc_downtrend:false,spread:true}});
  assert.deepEqual(settingsDraft(saved,boot),saved);
});
test('drafts are independent; no-op and visual saves preserve inheritance, rule saves freeze local ATR',()=>{
  const draft=settingsDraft(config,boot);
  const changed=withConfigValue(draft,'presentation.target_pct',2);
  assert.equal(config.presentation?.target_pct,1);
  assert.equal(settingsForSave(draft,config,boot).atr_period,null);
  assert.equal(settingsForSave(changed,config,boot).atr_period,null);
  assert.equal(settingsForSave(withConfigValue(draft,'min_score',8),config,boot).atr_period,14);
  assert.equal(settingsForSave(withConfigValue(draft,'atr_period',21),config,boot).atr_period,21);
  assert.equal(boot.inherited_atr?.period,14);
  assert.deepEqual(config,settingsDraft(config,boot));
});

const schema:ConfigSchema={type:'object',properties:{atr_period:{anyOf:[{type:'integer',minimum:1},{type:'null'}]},
  presentation:{$ref:'#/$defs/Presentation'}},$defs:{Presentation:{type:'object',properties:{target_pct:{type:'number',exclusiveMinimum:0}}}}};
test('validation applies published server bounds and score/history consistency',()=>{
  assert.deepEqual(validateSettings(config,schema),{});
  assert(validateSettings(withConfigValue(config,'presentation.target_pct',0),schema)['presentation.target_pct']);
  assert(validateSettings(withConfigValue(config,'atr_period',1.5),schema).atr_period);
  assert(validateSettings(withConfigValue(config,'atr_period',''),schema).atr_period);
  assert(validateSettings(withConfigValue(config,'atr_period',100),schema).atr_period);
  assert(validateSettings(withConfigValue(config,'min_score',20),schema).min_score);
  assert(validateSettings(withConfigValue(config,'trigger_patterns',[]),schema).trigger_patterns);
  assert(validateSettings(withConfigValue(config,'rr_bonus_threshold',1),schema).rr_bonus_threshold);
});

test('flow-only saves preserve inherited ATR, scoring and veto configuration',()=>{
  const withFlow={...boot,pump_flow_preset:{enabled:false,asset_min_delta:.03}};
  const draft=settingsDraft(config,withFlow);
  const changed=settingsForSave(withConfigValue(draft,'pump_flow.enabled',true),config,withFlow);
  assert.equal(changed.atr_period,null);
  assert.deepEqual(changed.score_weights,config.score_weights);
  assert.deepEqual(changed.veto_filters,config.veto_filters);
  assert.deepEqual(changed.pump_flow,{enabled:true,asset_min_delta:.03});
  assert.deepEqual(withFlow.pump_flow_preset,{enabled:false,asset_min_delta:.03});
});
