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

test('stop sizing fields round trip independently from the RR veto',()=>{
  const preset={...config,stop_sizing:{enabled:false,target_pct:1,gross_rr:2}};
  const draft=settingsDraft(config,{...boot,preset});
  assert.deepEqual(draft.stop_sizing,preset.stop_sizing);
  const changed=settingsForSave(withConfigValue(draft,'stop_sizing.enabled',true),config,{...boot,preset});
  assert.equal(changed.stop_sizing?.enabled,true);
  assert.equal(changed.min_rr_net,config.min_rr_net);
  assert.equal(changed.veto_filters,config.veto_filters);
  const invalid=withConfigValue(changed,'stop_sizing.gross_rr',.001);
  assert(validateSettings(invalid,{})['stop_sizing.gross_rr']);
  assert.equal(config.stop_sizing,undefined);
});

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


test('complete JSON draft uses effective conversion without mutating saved legacy config',()=>{
  const complete={...structuredClone(config),schema_version:'mtf_chart_v3',atr_period:null,
    timeframes:{structure:'4h',zone:'1h',trigger:'5m'},indicators:{},frame_atr:{},
    rules:{armed:{logic:'AND',conditions:[]},trigger:{logic:'OR',conditions:[]},vetoes:[],scoring:[]}} as MTFConfig;
  const context={...boot,effective_config:complete,effective_preset:{...complete,min_score:0}};
  const draft=settingsDraft(config,context);
  assert.equal(draft.schema_version,'mtf_chart_v3');
  assert.equal(config.schema_version,'mtf_chart_v1');
  const changed=withConfigValue(draft,'timeframes.trigger','1h');
  assert.equal(settingsForSave(changed,config,context).timeframes?.trigger,'1h');
  assert.equal(settingsForSave(changed,config,context).atr_period,null);
  assert.equal(settingsDraft(null,context).min_score,0);
  assert.equal(settingsDraft(changed,context).timeframes?.trigger,'1h');
});

test('audit settings round trip without changing existing stop or veto policy',()=>{
  const original=structuredClone(config);
  const changed=withConfigValue(config,'context_audit.enabled',true);
  const threshold=withConfigValue(changed,'context_audit.min_stop_atr_trigger',1.5);
  assert.equal(threshold.context_audit?.enabled,true);
  assert.equal(threshold.context_audit?.min_stop_atr_trigger,1.5);
  assert.deepEqual(threshold.stop_sizing,original.stop_sizing);
  assert.deepEqual(threshold.veto_filters,original.veto_filters);
  assert.deepEqual(config,original);
});

test('recursive JSON schema accepts all operand alternatives and reports invalid nested values',()=>{
  const schema:ConfigSchema={type:'object',properties:{rules:{$ref:'#/$defs/Group'}},$defs:{
    Group:{type:'object',required:['conditions'],additionalProperties:false,properties:{conditions:{type:'array',items:{anyOf:[{$ref:'#/$defs/Leaf'},{$ref:'#/$defs/Group'}]}}}},
    Leaf:{type:'object',required:['value'],additionalProperties:false,properties:{value:{anyOf:[{type:'number'},{type:'string'},{type:'boolean'},{type:'array',items:{type:'number'}}]}}}
  }};
  const valid={...config,rules:{conditions:[{value:true},{conditions:[{value:'UP'},{value:[1,2]}]}]}} as unknown as MTFConfig;
  assert.deepEqual(validateSettings(valid,schema),{});
  const invalid=structuredClone(valid) as unknown as {rules:{conditions:unknown[]}};
  invalid.rules.conditions=[{conditions:[{value:{invalid:true}}]}];
  assert.ok(Object.keys(validateSettings(invalid as unknown as MTFConfig,schema)).some(k=>k.includes('rules.conditions')));
});
