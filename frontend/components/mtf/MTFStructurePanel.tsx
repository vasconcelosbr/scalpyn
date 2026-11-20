'use client';
import {useEffect,useState} from 'react';
import {currentStructure,structureLabels,structureTooltip,signed,conflictText} from '@/lib/mtfStructure';
import {number,type Analysis,type StructureContext} from './types';

function useStructure(value:StructureContext|null|undefined,analysis:Analysis|undefined|null) {
  const [now,setNow]=useState(()=>Date.now());
  useEffect(()=>{
    setNow(Date.now());
    const expires=Date.parse(value?.valid_until??'');
    if(!Number.isFinite(expires)||expires<=Date.now())return;
    const timer=setTimeout(()=>setNow(Date.now()),expires-Date.now());return()=>clearTimeout(timer);
  },[value]);
  return currentStructure(value,analysis,now);
}
function Conflict({context,compact=false}:{context:StructureContext|null;compact?:boolean}) {
  return context?.conflict?<span className="mtf-conflict" tabIndex={0} title={conflictText(context)} aria-label={`Conflito MTF. ${conflictText(context)}`}>{compact?'⚠':'⚠ Conflito MTF'}<span role="tooltip">{conflictText(context)}</span></span>:null;
}
const directionOf=(analysis?:Analysis|null,context?:StructureContext|null)=>context?context.frames['4h'].trend:analysis?.context_4h.last_pivots?.length?analysis.context_4h.trend:null;
const tone=(analysis?:Analysis|null,context?:StructureContext|null)=>context?.conflict===true?'mtf-structure-conflicted':context?.conflict===false?analysis?.context_4h.trend==='UP'?'positive':analysis?.context_4h.trend==='DOWN'?'negative':'':'';

export function MTFStructureCompact({analysis,context:value}:{analysis?:Analysis|null;context?:StructureContext|null}) {
  const context=useStructure(value,analysis);
  return <span className="mtf-structure-compact"><span title={structureTooltip} className={tone(analysis,context)}>{structureLabels[directionOf(analysis,context)??'']??'—'}</span><small title="Extensão do fechamento 4H em relação à EMA50">{signed(context?.frames['4h'].extension_atr)} ATR</small><Conflict context={context} compact/></span>;
}

export function MTFStructurePanel({analysis,context:value,variant='card',precision,timezone='UTC'}:{analysis?:Analysis;context?:StructureContext|null;variant?:'header'|'card';precision?:number;timezone?:string}) {
  const context=useStructure(value,analysis),frame=context?.frames['4h'];
  const direction=directionOf(analysis,context);
  const change=frame?.live_change_pct;
  const against=change!=null&&((direction==='UP'&&change<0)||(direction==='DOWN'&&change>0));
  const price=(v?:number|null)=>v==null?'—':precision==null?v.toLocaleString('pt-BR',{maximumSignificantDigits:16}):number(v,precision);
  const date=(v:string)=>new Date(v).toLocaleString('pt-BR',{timeZone:timezone});
  return <section className={`mtf-structure-panel ${variant}`} aria-label="Estrutura, estado e conflito MTF">
    <div className="mtf-structure-title"><span title={structureTooltip} tabIndex={0}>Estrutura 4H</span><strong className={tone(analysis,context)}>{structureLabels[direction??'']??'—'}</strong><Conflict context={context}/>{context?.conflict==null&&<small className="mtf-structure-note">Conflito: indisponível</small>}</div>
    <p className="mtf-structure-sequence" title={structureTooltip}>{analysis?.context_4h.last_pivots?.length?analysis.context_4h.last_pivots.join('·'):'Pivôs indisponíveis'}</p>
    <div className="mtf-structure-metrics">
      <div><span>Idade do último pivô</span><b>{frame?.last_pivot_age_candles==null?'—':`Confirmado há ${frame.last_pivot_age_candles} candles 4H`}</b></div>
      <div><span>Invalidação estrutural {context?.invalidation?.label??''}</span><b>{price(context?.invalidation?.price)}</b><small>Preço − nível: {signed(context?.invalidation_distance_atr)} ATR</small></div>
      <div><span>Extensão · candle fechado</span><b>{frame?.extension_atr==null?'—':`${signed(frame.extension_atr)} ATR da EMA50`}</b><small>Sem classificação de extensão</small></div>
    </div>
    <p className={`mtf-structure-live ${against?'against':''}`}>{change==null?'Candle 4H em formação: indisponível':`${against?'Candle 4H em formação contra a estrutura':'Candle 4H em formação'}: ${signed(change,2)}%`} <small>em formação · não gera sinal</small></p>
    <div className="mtf-structure-lower">{(['1h','5m'] as const).map(tf=><span key={tf}>Estrutura {tf.toUpperCase()}: <b>{structureLabels[context?.frames[tf].trend??'']??'—'}</b></span>)}</div>
    <p className="mtf-structure-note">{structureTooltip} {context?`Leitura ${date(context.as_of)}. `:''}Invalidação estrutural é uma referência visual; o stop do plano permanece separado.</p>
  </section>;
}
