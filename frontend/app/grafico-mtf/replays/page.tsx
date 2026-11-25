'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { apiFetch } from '@/lib/api';
import { createChart, CandlestickSeries, createSeriesMarkers, ColorType, type UTCTimestamp } from 'lightweight-charts';
import './style.css';

type Indicator = {name:string;timeframe:string;value:unknown;source_at?:string;quality:string;producer?:string};
type Candle = {time:string|number;open:number;high:number;low:number;close:number};
type Snapshot = {indicators:Indicator[];analysis:Record<string,unknown>;replay_inputs?:{frames:Record<string,{candles:Candle[]}>};engine_config:{timeframes?:{trigger:string}};activation_boundary?:string;capture_kind?:string};
type Replay = {id:string;symbol:string;watchlist_id:string;profile_id:string|null;triggered_at:string;captured_at:string;baseline:number;target:number;stop:number|null;status:string;issue:string|null;last_price:number|null;result:{return_pct:number|null;price?:number;candle_open_at:string;candle_close_at:string;reason?:string}|null;snapshot:Snapshot;armed:{time:string;price:number|null}[]};
type Trajectory = {candles:Candle[];snapshots:{captured_at:string;payload:Snapshot}[];next_cursor:string|null};
const labels:Record<string,string>={OPEN:'Em acompanhamento',TARGET:'Alvo atingido',STOP:'Stop atingido',INDETERMINATE:'Resultado indeterminado'};
const issues:Record<string,string>={CANDLE_GAP:'Lacuna na trajetória. Recuperação pendente.',STOP_UNAVAILABLE:'O sinal não possui stop válido. Encerramento por prejuízo indisponível.',CAPTURE_ERROR:'Falha de captura. O acompanhamento será retomado.'};
const time=(v:string|number)=>typeof v==='number'?v:Date.parse(v)/1000;
const date=(v:string|number)=>new Date(time(v)*1000).toLocaleString('pt-BR',{timeZone:'America/Sao_Paulo'});
const number=(v:number|null|undefined)=>v==null?'—':v.toLocaleString('pt-BR',{maximumFractionDigits:8});
const value=(v:unknown)=>v==null?'Indisponível':typeof v==='object'?JSON.stringify(v):String(v);
const key=(i:Indicator)=>`${i.producer??''}/${i.timeframe}/${i.name}`;

function ReplayChart({replay,bars,at}:{replay:Replay;bars:Candle[];at:number}){
  const host=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!host.current)return;
    const chart=createChart(host.current,{autoSize:true,height:360,layout:{background:{type:ColorType.Solid,color:'#0b111b'},textColor:'#91a5bd'},grid:{vertLines:{color:'#182333'},horzLines:{color:'#182333'}},timeScale:{timeVisible:true,secondsVisible:false},localization:{timeFormatter:(t:number)=>date(t)}});
    const series=chart.addSeries(CandlestickSeries,{upColor:'#4fd3ad',downColor:'#ed8192',borderVisible:false,wickUpColor:'#4fd3ad',wickDownColor:'#ed8192'});
    const unique=new Map(bars.map(c=>[time(c.time),c]));
    const sorted=[...unique].sort((a,b)=>a[0]-b[0]);
    series.setData(sorted.map(([t,c])=>({...c,time:t as UTCTimestamp})));
    for(const [price,title,color] of [[replay.baseline,'Gatilho','#79a7ff'],[replay.target,'Alvo','#4fd3ad'],[replay.stop,'Stop','#ed8192']] as const){
      if(price!=null)series.createPriceLine({price,title,color,lineWidth:1,axisLabelVisible:true});
    }
    const nearest=(t:number)=>sorted.filter(([s])=>s<=t).at(-1)?.[0];
    const marks:{time:UTCTimestamp;position:'aboveBar'|'belowBar';color:string;shape:'arrowUp'|'arrowDown';text:string}[]=[];
    for(const a of replay.armed??[]){const t=nearest(time(a.time)-1);if(t!=null)marks.push({time:t as UTCTimestamp,position:'belowBar',color:'#edba66',shape:'arrowUp',text:'Armado'});}
    const trigger=nearest(time(replay.triggered_at)-1);
    if(trigger!=null)marks.push({time:trigger as UTCTimestamp,position:'belowBar',color:'#79a7ff',shape:'arrowUp',text:'Gatilho'});
    if(replay.result && time(replay.result.candle_close_at)<=at){const t=nearest(time(replay.result.candle_open_at));if(t!=null)marks.push({time:t as UTCTimestamp,position:'aboveBar',color:replay.status==='TARGET'?'#4fd3ad':'#ed8192',shape:'arrowDown',text:labels[replay.status]});}
    createSeriesMarkers(series,marks.sort((a,b)=>Number(a.time)-Number(b.time)));
    chart.timeScale().fitContent();
    return()=>chart.remove();
  },[replay,bars,at]);
  return <div ref={host} className="replay-chart" aria-label="Gráfico de candles com marcações de armado, gatilho e resultado"/>;
}

export default function SignalReplays(){
  const [rows,setRows]=useState<Replay[]>([]),[selected,setSelected]=useState<Replay|null>(null),[trajectory,setTrajectory]=useState<Trajectory>({candles:[],snapshots:[],next_cursor:null});
  const [error,setError]=useState(''),[loading,setLoading]=useState(false),[tick,setTick]=useState(0),[index,setIndex]=useState(0),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(1);
  const [filters,setFilters]=useState({symbol:'',watchlist_id:'',status:'',start:'',end:''}),[applied,setApplied]=useState(filters),[offset,setOffset]=useState(0),[next,setNext]=useState<number|null>(null);
  const [watchlists,setWatchlists]=useState<{id:string;name:string}[]>([]),[target,setTarget]=useState(''),[settings,setSettings]=useState<Record<string,unknown>>({}),[notice,setNotice]=useState('');
  const [profiles,setProfiles]=useState<{id:string;name:string}[]>([]);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const loaded=useRef<{id:string;trajectory:Trajectory}|null>(null);
  useEffect(()=>{const controller=new AbortController();Promise.all([apiFetch<{watchlists:{id:string;name:string}[];profiles:{id:string;name:string}[]}>('/mtf/bootstrap',{signal:controller.signal}),apiFetch<Record<string,unknown>>('/mtf/observations/settings',{signal:controller.signal})]).then(([b,s])=>{setWatchlists(b.watchlists);setProfiles(b.profiles);setSettings(s);setTarget(String(s.replay_target_pct));}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[]);
  useEffect(()=>{const t=setInterval(()=>setTick(v=>v+1),15000);return()=>clearInterval(t);},[]);
  useEffect(()=>{const c=new AbortController();const q=new URLSearchParams({offset:String(offset)});for(const [k,v] of Object.entries(applied))if(v)q.set(k,k==='start'||k==='end'?new Date(v).toISOString():v);apiFetch<{items:Replay[];next_offset:number|null}>(`/mtf/replays?${q}`,{signal:c.signal}).then(r=>{setRows(r.items);setNext(r.next_offset);}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[applied,offset,tick]);
  useEffect(()=>{if(!selectedId)return;const c=new AbortController();(async()=>{
    const r=await apiFetch<Replay>(`/mtf/replays/${selectedId}`,{signal:c.signal});
    const cached=loaded.current?.id===selectedId?loaded.current.trajectory:null;
    const candles:Candle[]=[...(cached?.candles??[])],snapshots:Trajectory['snapshots']=[...(cached?.snapshots??[])];
    let cursor:string|null=candles.length?new Date(time(candles.at(-1)!.time)*1000).toISOString():null;
    do{const page:Trajectory=await apiFetch<Trajectory>(`/mtf/replays/${selectedId}/trajectory${cursor?`?after=${encodeURIComponent(cursor)}`:''}`,{signal:c.signal});candles.push(...page.candles);snapshots.push(...page.snapshots);cursor=page.next_cursor;}while(cursor&&!c.signal.aborted);
    if(!c.signal.aborted){const nextTrajectory={candles,snapshots:[...new Map(snapshots.map(s=>[s.captured_at,s])).values()],next_cursor:null};loaded.current={id:selectedId,trajectory:nextTrajectory};setSelected(r);setTrajectory(nextTrajectory);}
  })().catch(e=>{if(!c.signal.aborted)setError(e.message);}).finally(()=>{if(!c.signal.aborted)setLoading(false);});return()=>c.abort();},[selectedId,tick]);
  useEffect(()=>{if(!playing)return;const id=setInterval(()=>setIndex(n=>{if(n>=trajectory.candles.length){setPlaying(false);return n;}return n+1;}),1000/speed);return()=>clearInterval(id);},[playing,speed,trajectory.candles.length]);
  async function saveTarget(){try{const saved=await apiFetch<Record<string,unknown>>('/mtf/observations/settings',{method:'PUT',body:JSON.stringify({...settings,replay_target_pct:Number(target)})});setSettings(saved);setNotice('Alvo salvo para novas ativações. Replays existentes preservados.');}catch(e){setError(e instanceof Error?e.message:String(e));}}
  const shown=useMemo(()=>trajectory.candles.slice(0,index),[trajectory.candles,index]);
  const at=shown.length?time(shown.at(-1)!.time)+60:selected?time(selected.triggered_at):0;
  const tf=selected?.snapshot.engine_config.timeframes?.trigger??'5m';
  const bars=useMemo(()=>[...(selected?.snapshot.replay_inputs?.frames[tf]?.candles.slice(-80)??[]),...shown],[selected,tf,shown]);
  const follow=trajectory.snapshots.filter(s=>time(s.captured_at)<=at).at(-1);
  const current=follow?.payload??selected?.snapshot;
  const initial=new Map(selected?.snapshot.indicators.map(i=>[key(i),i])??[]);
  const finished=!!selected?.result && at>=time(selected.result.candle_close_at);
  const price=finished?selected?.result?.price:shown.at(-1)?.close??selected?.baseline;
  const pct=selected&&price!=null?(price/selected.baseline-1)*100:null;
  return <main className="signal-replays">
    <header><div><Link href="/grafico-mtf">← Gráfico MTF</Link><span className="replay-eyebrow">MEMÓRIA DOS SINAIS</span><h1>Replay de gatilhos</h1><p>Cada ativação confirmada. Do gatilho ao alvo ou stop, com ou sem Shadow.</p></div><Link href="/grafico-mtf/historico">Histórico de observações ↗</Link></header>
    {error&&<p role="alert" className="replay-error">{error}</p>}
    <form className="replay-filters" onSubmit={e=>{e.preventDefault();setOffset(0);setApplied(filters);setError('');}}>
      <label>Ativo<input placeholder="BTC_USDT" value={filters.symbol} onChange={e=>setFilters({...filters,symbol:e.target.value.toUpperCase().replace('/','_')})}/></label>
      <label>Watchlist<select value={filters.watchlist_id} onChange={e=>setFilters({...filters,watchlist_id:e.target.value})}><option value="">Todas</option>{watchlists.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
      <label>Resultado<select value={filters.status} onChange={e=>setFilters({...filters,status:e.target.value})}><option value="">Todos</option>{Object.entries(labels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label>De (horário local)<input type="datetime-local" value={filters.start} onChange={e=>setFilters({...filters,start:e.target.value})}/></label><label>Até<input type="datetime-local" value={filters.end} onChange={e=>setFilters({...filters,end:e.target.value})}/></label><button>Filtrar</button>
    </form>
    <div className="replay-table"><table><thead><tr><th>Gatilho · Brasília</th><th>Ativo / contexto</th><th>Preço no gatilho</th><th>Estado</th><th>Variação bruta</th><th/></tr></thead><tbody>{rows.map(r=>{const p=r.result?.return_pct??(r.status==='OPEN'&&r.last_price!=null?(r.last_price/r.baseline-1)*100:null);return <tr key={r.id} className={selectedId===r.id?'selected':''}><td>{date(r.triggered_at)}</td><td><strong>{r.symbol.replace('_','/')}</strong><small>{watchlists.find(w=>w.id===r.watchlist_id)?.name??r.watchlist_id}{r.profile_id?` · ${profiles.find(p=>p.id===r.profile_id)?.name??r.profile_id.slice(0,8)}`:''}</small></td><td>{number(r.baseline)}</td><td><span className={`replay-status ${r.status}`}>{r.status==='OPEN'&&r.last_price==null?'Preparando trajetória':labels[r.status]}</span>{r.issue&&<small>{issues[r.issue]??r.issue}</small>}</td><td>{p==null?'—':`${p.toFixed(2)}%`}{r.status==='OPEN'&&<small>Até o último candle capturado</small>}</td><td><button onClick={()=>{setLoading(true);setSelectedId(r.id);setSelected(null);setIndex(0);setPlaying(false);}}>Abrir replay</button></td></tr>;})}</tbody></table>{!rows.length&&<p className="replay-empty">Nenhuma ativação registrada neste filtro. Novos gatilhos são capturados no backend, mesmo com esta tela fechada.</p>}</div>
    <nav className="replay-controls"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Anteriores</button><button disabled={next==null} onClick={()=>setOffset(next!)}>Próximos</button><span>Resultados de sinais, sem custos ou execução de ordens.</span></nav>
    {loading&&!selected&&<p role="status">Carregando trajetória…</p>}
    {selected&&<section className="replay-detail"><div className="replay-detail-heading"><div><span className="replay-eyebrow">REPRODUÇÃO DO SINAL</span><h2>{selected.symbol.replace('_','/')} <small>{date(selected.triggered_at)}</small></h2></div><button onClick={()=>{setSelectedId(null);setSelected(null);setPlaying(false);}}>Fechar</button></div>
      <div className="replay-metrics"><div><small>Gatilho confirmado</small><strong>{number(selected.baseline)}</strong></div><div><small>Alvo congelado</small><strong>{number(selected.target)}</strong></div><div><small>Stop do sinal</small><strong>{number(selected.stop)}</strong></div><div><small>{finished?labels[selected.status]:'No instante selecionado'}</small><strong>{pct==null?'—':`${pct.toFixed(2)}%`}</strong></div></div>
      {selected.issue&&<p className="replay-warning">{issues[selected.issue]??selected.issue}</p>}
      {!selected.armed.length&&<p className="replay-warning">Sem registro anterior de ARMADO para esta ativação.</p>}
      {selected.snapshot.activation_boundary==='first_observation'&&<p className="replay-warning">Gatilho presente na primeira captura deste contexto; início anterior da ativação não comprovado.</p>}
      {selected.snapshot.capture_kind==='reconstructed_from_frozen_observations'&&<p className="replay-warning">Reconstruído exclusivamente das observações históricas preservadas. Fechamentos sem captura original permanecem indisponíveis.</p>}
      <ReplayChart replay={selected} bars={bars} at={at}/>
      <div className="replay-controls"><button onClick={()=>{setIndex(0);setPlaying(false);}}>Início</button><button disabled={!trajectory.candles.length} onClick={()=>setPlaying(!playing)}>{playing?'Pausar':'Reproduzir'}</button><button disabled={index>=trajectory.candles.length} onClick={()=>{setPlaying(false);setIndex(i=>i+1);}}>Próximo candle</button><label>Velocidade<select value={speed} onChange={e=>setSpeed(Number(e.target.value))}>{[1,2,4,8].map(s=><option key={s} value={s}>{s}×</option>)}</select></label><span>{date(at)} · Brasília</span></div>
      <input className="replay-range" aria-label="Posição do replay" type="range" min={0} max={trajectory.candles.length} value={index} onChange={e=>{setPlaying(false);setIndex(Number(e.target.value));}}/>
      <p className="replay-caption">Contexto anterior em {tf}; trajetória posterior em candles fechados de um minuto. Marcações de alvo e stop identificam o candle do toque, não um horário intraminuto estimado.</p>
      {finished&&selected.status==='INDETERMINATE'&&<p className="replay-warning">Alvo e stop foram tocados no mesmo candle de menor resolução disponível. A ordem dos eventos não foi comprovada.</p>}
      <h3>Indicadores · confirmação × instante selecionado</h3><p className="replay-caption">{follow?`Última captura disponível: ${date(follow.captured_at)}.`:'Exibindo a captura da confirmação.'} Fechamentos não capturados não são preenchidos por estimativa.</p>
      <div className="replay-table"><table><thead><tr><th>Indicador</th><th>Timeframe</th><th>No gatilho</th><th>No instante</th><th>Origem do valor</th><th>Qualidade</th></tr></thead><tbody>{current?.indicators.map((i,n)=><tr key={`${key(i)}-${n}`}><td>{i.name}<small>{i.producer}</small></td><td>{i.timeframe}</td><td>{value(initial.get(key(i))?.value)}</td><td>{value(i.value)}</td><td>{i.source_at?date(i.source_at):'Indisponível'}</td><td>{i.quality}</td></tr>)}</tbody></table></div>
      <details><summary>Condições, vetos, capital e fluxo na confirmação</summary><pre>{JSON.stringify(selected.snapshot.analysis,null,2)}</pre></details>
      <details><summary>Evidência completa do sinal</summary><pre>{JSON.stringify(selected.snapshot,null,2)}</pre></details>
    </section>}
    <details className="replay-settings"><summary>Configuração do acompanhamento</summary><p>Alvo exclusivo do replay, aplicado somente às próximas ativações. O stop permanece o definido no sinal.</p><label>Alvo de alta (%)<input type="number" min="0.01" max="100" step="any" value={target} onChange={e=>setTarget(e.target.value)}/></label><button onClick={saveTarget}>Salvar alvo</button>{notice&&<p role="status">{notice}</p>}</details>
  </main>;
}
