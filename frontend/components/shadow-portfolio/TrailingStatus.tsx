"use client";
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { trailingPercent, trailingReasons, trailingSummary, type ShadowTrailingView, type TrailingLevel, type TrailingRegime } from '@/lib/shadowTrailingView';

const time = (v: string | null) => v ? new Date(v).toLocaleString('pt-BR') : 'Não registrado';
const price = (v: number | null) => v == null ? 'Não registrado' : `$${v.toLocaleString('pt-BR',{maximumFractionDigits:8})}`;
const quality: Record<string,string> = { VALID:'Válidos', STALE:'Desatualizados', INCOMPLETE:'Incompletos', INCOMPLETE_OR_STALE:'Incompletos ou desatualizados', UNAVAILABLE:'Indisponíveis', PARAMETERS_REQUIRED:'Parâmetros ausentes', PRICE_HISTORY_GAP:'Lacuna no histórico de preços' };

export function TrailingStatus({view:v, symbol}:{view:ShadowTrailingView;symbol:string}) {
  const [open,setOpen] = useState(false);
  const [pinned,setPinned] = useState(false);
  const [position,setPosition] = useState({top:0,left:0});
  const anchor = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const id = useId();
  const close = () => { setOpen(false); setPinned(false); };
  const show = () => {
    if(timer.current) clearTimeout(timer.current);
    const r = anchor.current?.getBoundingClientRect();
    const panelHeight = Math.min(600, window.innerHeight*.75);
    if(r) setPosition({left:Math.max(12,Math.min(r.left,window.innerWidth-390)),top:Math.max(12,Math.min(r.bottom+8,window.innerHeight-panelHeight-12))});
    setOpen(true);
  };
  const leave = () => { if(!pinned) timer.current=setTimeout(()=>setOpen(false),180); };
  useEffect(()=>{
    if(!open) return;
    const outside=(e:PointerEvent)=>{ if(!anchor.current?.contains(e.target as Node) && !panel.current?.contains(e.target as Node)) close(); };
    const escape=(e:KeyboardEvent)=>{if(e.key==='Escape') close();};
    window.addEventListener('pointerdown',outside); window.addEventListener('keydown',escape); window.addEventListener('resize',close);
    return ()=>{window.removeEventListener('pointerdown',outside);window.removeEventListener('keydown',escape);window.removeEventListener('resize',close);};
  },[open]);
  useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current);},[]);
  const level=(title:string,l:TrailingLevel)=> <div className="py-2 border-b border-white/10"><dt className="text-slate-400">{title}</dt><dd className="mt-1 font-mono text-slate-100">{price(l.price)}{l.pct!=null?` · ${trailingPercent(l.pct)}`:''}</dd>{l.at && l.price!=null && <dd className="text-slate-500 mt-1">{time(l.at)}</dd>}</div>;
  const regime=(title:string,r:TrailingRegime)=> <div className="py-2"><dt className="text-slate-400">{title}</dt><dd>{r.label ?? 'Direção não confirmada'}{r.original?` · ${r.original}`:''}</dd><dd className="text-slate-500">{quality[r.quality] ?? r.quality} · {r.source ?? 'Fonte indisponível'} {r.timeframe ?? ''} · {time(r.at)}</dd></div>;
  const protectedState=['ACTIVE','TIGHTENED','EXIT_PENDING'].includes(v.state) && v.mode!=='OBSERVE';
  return <>
    <button ref={anchor} type="button" aria-label={`${symbol}: ${trailingSummary(v)}`} aria-describedby={open?id:undefined} aria-expanded={open}
      onMouseEnter={show} onMouseLeave={leave} onFocus={show} onBlur={()=>{if(!pinned)setOpen(false);}}
      onKeyDown={e=>e.stopPropagation()}
      onClick={e=>{e.stopPropagation();if(pinned)close();else{show();setPinned(true);}}}
      className={`block mt-2 text-[10px] leading-4 rounded-md border px-2 py-1 max-w-52 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${protectedState?'border-emerald-500/40 text-emerald-300 bg-emerald-500/10':'border-slate-600 text-slate-300 bg-slate-800/60'}`}>
      {trailingSummary(v)}
    </button>
    {open && createPortal(<div ref={panel} id={id} role="tooltip" onMouseEnter={()=>{if(timer.current)clearTimeout(timer.current);}} onMouseLeave={leave}
      onClick={e=>e.stopPropagation()} onKeyDown={e=>e.stopPropagation()}
      style={{position:'fixed',...position,width:'min(370px, calc(100vw - 24px))',maxHeight:'min(600px, 75vh)',zIndex:10000,overflowY:'auto',colorScheme:'dark'}}
      className="rounded-xl border border-slate-600 bg-slate-950 p-4 text-xs text-slate-200 shadow-2xl">
      <p className="font-semibold text-sm">{symbol} · {trailingSummary(v)}</p>
      <p className="text-slate-400 mt-1">{v.origin==='POST_TP'?'Continuação após TP':v.origin==='PRE_TP'?'Trailing anterior ao TP':'Gatilho conforme parâmetros do trade'}</p>
      {v.mode==='OBSERVE' && <p className="text-amber-300 mt-2">Simulação em observação. O piso candidato não controla a saída.</p>}
      <dl className="mt-2">
        {level('Entrada do trade',v.entry)}
        {level('Referência de ativação do trailing',v.activation)}
        {level(v.state==='CLOSED'?'Saída persistida':'Último preço observado',v.observed)}
        {level('Máxima processada',v.maximum)}
        {level(v.state==='CLOSED'?'Último piso de proteção registrado':'Piso de proteção vigente',v.floor)}
        {v.distance_to_floor_pp!=null && <div className="py-2">Distância até o piso: {trailingPercent(v.distance_to_floor_pp,' p.p.')}</div>}
        {v.pending_floor.price!=null && level('Piso calculado para avaliação seguinte',v.pending_floor)}
        {v.next_step.price!=null ? <>{level('Próximo degrau de lucro',v.next_step)}{level('Piso correspondente ao próximo degrau',v.next_step_floor)}<p className="text-slate-400 py-2">Continuação sem teto. O ATR pode elevar o piso antes; este degrau não é um alvo de saída.</p></> : level(v.origin==='PRE_TP'?'TP para decidir continuação':'Gatilho de preço para ativação',v.trigger)}
        {v.remaining_pp!=null && <div className="py-2">{v.remaining_pp>0?`Faltam ${trailingPercent(v.remaining_pp,' p.p.')} até o gatilho`:'Gatilho de preço alcançado; verificar confirmação e avaliação'}</div>}
        {regime('Regime atual do ativo',v.asset_regime)}{regime('Regime atual do mercado',v.market_regime)}
      </dl>
      <p className="border-t border-white/10 pt-3 mt-2">{trailingReasons[v.pending_reason ?? ''] ?? v.pending_reason ?? 'Sem decisão registrada'}</p>
      <p className="text-slate-400 mt-1">Dados: {quality[v.quality] ?? v.quality} · Avaliação: {time(v.last_evaluated_at)}</p>
      <p className="text-slate-500 mt-3">Percentuais brutos relativos à entrada. Piso é o gatilho de proteção; gaps podem gerar execução abaixo dele. A ativação indicada é uma referência registrada por candle.</p>
    </div>,document.body)}
  </>;
}
