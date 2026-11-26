import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MTFTriggerDiagnostics} from '../components/mtf/MTFTriggerDiagnostics';
import {MTFEntryBandVetoes} from '../components/mtf/MTFEntryBandVetoes';
import type {Analysis,ChartData,Presentation,TriggerCheck} from '../components/mtf/types';

const check=(id:string,extra:Partial<TriggerCheck>={}):TriggerCheck=>({id,label:id,actual:false,operator:'=',expected:true,
  unit:'',role:'required',timeframe:'1h',state:'FAIL',gap:null,note:null,...extra});
const render=(checks:TriggerCheck[])=>renderToStaticMarkup(createElement(MTFTriggerDiagnostics,{
  symbol:'TEST_USDT',date:v=>v??'—',analysis:{symbol:'TEST_USDT',status:'NO_TRADE',config_hash:'frozen',
    trigger_diagnostics:{version:'mtf_trigger_diagnostics_v1',status:'NO_TRADE',config_hash:'frozen',checks,patterns:[],
      explanation_context:{version:'mtf_check_explanation_v1',cutoff:7200,symbol:'TEST_USDT',mode:'CLOSED'}}} as unknown as Analysis}));

test('structural card explains frozen pivots and active break with no live config dependency',()=>{
  const row=check('audit:zone_integrity',{label:'Estrutura íntegra zone',explanation:{
    version:'mtf_check_explanation_v1',description:'Verifica a integridade após o fechamento.',method:'Fechamento abaixo do fundo rompe a estrutura.',
    parameters:{pivot_left:4,pivot_right:6},timeframes:['1h'],window:{start_at:0,end_at:7200,candle_count:2},
    evidence:{intact:false,broken_at:3600,last_break:{at:3600,close:99,pivot:{time:0,confirmed_at:300,kind:'L',label:'HL',price:99.6}},
      pivots:[{time:0,confirmed_at:300,kind:'L',label:'HL',price:99.6}]}}});
  const before=structuredClone(row),html=render([row]);
  for(const value of ['Integridade da estrutura do período da zona','Como é calculado','Candles à esquerda do pivô',
    'Pivôs usados','99,6','Rompimento ainda ativo','1970-01-01T01:00:00.000Z']) assert(html.includes(value),value);
  assert(!html.includes('Estrutura íntegra zone'));
  assert.deepEqual(row,before);
});

test('legacy detail never manufactures snapshot parameters and preserves unavailable state',()=>{
  const html=render([check('audit:structure_integrity',{state:'UNAVAILABLE',actual:null})]);
  assert(html.includes('NÃO DISPONÍVEL'));
  assert(html.includes('Detalhes técnicos não registrados'));
  assert(html.includes('Indisponível'));
  assert(!html.includes('Candles à esquerda do pivô'));
});

test('trigger shows nested alternatives and each operand timeframe',()=>{
  const leaf=check('cross',{role:'context',operator:'crosses_above',actual:11,expected:10,
    explanation:{version:'mtf_check_explanation_v1',description:'Compara duas médias.',method:'Cruza no fechamento.',
      parameters:{},timeframes:['5m','1h'],inputs:{
        left:{ref:'indicators.fast',timeframe:'5m',configuration:{family:'ema',output:'ema3',parameters:{periods:[3]},timeframe:'5m',enabled:true,display:false,sample_window:10}},
        right:{ref:'indicators.slow',timeframe:'1h',configuration:{family:'ema',output:'ema8',parameters:{periods:[8]},timeframe:'1h',enabled:true,display:false,sample_window:20}}}},
    previous:{actual:9,expected:10,close_at:300}});
  const root=check('rules:GATILHO',{rule_tree:{logic:'OR',state:'FAIL',children:[{logic:'AND',state:'FAIL',children:[{check_id:'cross',state:'FAIL'}]}]},
    explanation:{version:'mtf_check_explanation_v1',description:'Combina regras.',method:'Grupo E/OU.',
      parameters:{},timeframes:['5m','1h'],operational_timeframe:'5m'}});
  const html=render([root,leaf]);
  for(const text of ['Grupo OU','Grupo E','Fonte do valor observado','Fonte da exigência','ema3','ema8','5M','1H','Comparação anterior','cruza para cima'])
    assert(html.includes(text),text);
});

test('actual candidate-band component receives audit checks and distinguishes failed from missing data',()=>{
  const now=Date.now(),at=new Date(now-1000).toISOString(),until=new Date(now+60000).toISOString();
  const checks=[check('audit:zone_alignment',{actual:'RANGE',expected:'UP'}),
    check('audit:structure_integrity',{state:'UNAVAILABLE',actual:null})];
  const vetoes=checks.map(c=>'RISK_AUDIT:'+c.id);
  const data={symbol:'TEST_USDT',as_of:at,profile:null,indicator_config:{},
    frames:{'1h':{candles:[],indicators:{},patterns:[],pivots:[],stale:false,source:'fixture'}},
    analysis:{symbol:'TEST_USDT',status:'NO_TRADE',config_hash:'same',evaluated_at_utc:at,vetoes,
      risk_audit:{version:'mtf_context_risk_v1',status:'BLOCK',checks,reasons:[]},
      entry_candidate:{state:'BLOCKED',reason:null,timeframe:'1h',low:99,high:101,support_low:99,support_high:100,
        atr:2,proximity_atr:.5,entry:null,evaluated_at:at,valid_until:until,config_hash:'same',vetoes,operational_effect:false}}} as unknown as ChartData;
  const config={entry_band_enabled:true,panel_heights:{price:400},default_indicators:[],reason_labels:{}} as unknown as Presentation;
  const html=renderToStaticMarkup(createElement(MTFEntryBandVetoes,{analysis:data.analysis,presentation:config}));
  assert(html.includes('Direção estrutural do período da zona · 1H — reprovado'));
  assert(html.includes('Integridade da estrutura principal · 1H — dado indisponível'));
  assert(!html.includes('verificação não atendida'));
  assert(!html.includes('detalhes indisponíveis'));
});
