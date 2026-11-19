import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MTFTriggerDiagnostics, diagnosticValue } from '../components/mtf/MTFTriggerDiagnostics';
import type { Analysis, TriggerDiagnostics } from '../components/mtf/types';

const diagnostic: TriggerDiagnostics = {
  version:'mtf_trigger_diagnostics_v1', status:'ARMED', config_hash:'frozen',
  candle_close_at_utc:'closed', observed_at_utc:'observed', patterns:[], checks:[
    {id:'trigger',label:'Padrão de compra',actual:false,expected:true,operator:'=',unit:'',role:'required',timeframe:'5m',state:'FAIL',gap:null,note:'Basta um padrão.'},
    {id:'spread',label:'Spread máximo',actual:.0000012,expected:.000001,operator:'<=',unit:'%',role:'veto',timeframe:null,state:'FAIL',gap:.0000002,note:null},
    {id:'rr',label:'Retorno/risco',actual:null,expected:2.5,operator:'>=',unit:'ratio',role:'veto',timeframe:null,state:'WAITING',gap:null,note:'Aguardando padrão de gatilho.'},
    {id:'btc',label:'Bitcoin',actual:'DOWN',expected:'DOWN',operator:'!=',unit:'',role:'veto',timeframe:'4h',state:'DISABLED',gap:null,note:null},
  ],
};
const analysis = {symbol:'SMALL_USDT',status:'ARMED',config_hash:'frozen',trigger_diagnostics:diagnostic,trigger_5m:null} as Analysis;
const render=(a?:Analysis)=>renderToStaticMarkup(createElement(MTFTriggerDiagnostics,{analysis:a,symbol:'SMALL_USDT',date:v=>v??'—'}));

test('confirmed operands, deficits, pending dependencies and disabled filters remain distinct',()=>{
  const html=render(analysis);
  for(const label of ['SMALL/USDT','Observado','Exigido','Não atendido','Acima do máximo','Aguardando padrão de gatilho.','Desativado','closed','observed']) assert(html.includes(label),label);
  assert(html.includes('0,0000012'));
  assert(html.includes('0,0000002'));
  assert(!html.includes('NaN'));
});

test('old observations, changed identity and empty selection never invent diagnostics',()=>{
  assert(render({...analysis,trigger_diagnostics:undefined}).includes('Detalhamento indisponível'));
  assert(render({...analysis,config_hash:'changed'}).includes('Detalhamento indisponível'));
  assert(!render({...analysis,config_hash:'changed'}).includes('0,0000012'));
  assert(render().includes('Aguardando avaliação confirmada'));
});

test('small market values retain significant digits and unavailable never becomes zero',()=>{
  assert.equal(diagnosticValue(.000000000123,'price'),'0,000000000123');
  assert.equal(diagnosticValue(null,'price'),'Não disponível');
  assert.equal(diagnosticValue(Infinity,'price'),'Não disponível');
  assert.equal(diagnosticValue('DOWN',''),'Baixa');
});
