'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { apiFetch } from '@/lib/api';
import './style.css';
import { MTFDecisionEvidence } from '@/components/mtf/MTFDecisionEvidence';

type Row = { id:string; symbol:string; watchlist_id:string; profile_id:string|null; observed_at:string; status:string; trend:string|null; quality:string; observed_price:number|null; candle_close_price:number|null; score:{total?:number}; price_quality:string };
type Filters = {watchlist_id:string;symbol:string;profile_id:string;status:string;trend:string;quality:string;start:string;end:string};
type Indicator = {name:string;timeframe:string;value:unknown;source_at?:string;producer?:string;role:string;quality:string};
type Detail = Row & {decision_replay?:{status:string;reason?:string;scope?:string};payload:{indicators:Indicator[];[key:string]:unknown};outcomes:{horizon_minutes:number;status:string;due_at:string;payload:Record<string,unknown>|null}[];next_status_change:unknown};
type ExportJob = {id:string;status:string;error?:string;row_count?:number;format:'xlsx'|'csv'};
const emptyFilters:Filters={watchlist_id:'',symbol:'',profile_id:'',status:'',trend:'',quality:'',start:'',end:''};
const states:Record<string,string>={WAIT:'Aguardar',ARMED:'Armado',TRIGGERED:'Gatilho',NO_TRADE:'Sem trade',PENDING:'Pendente',INCOMPLETE:'Incompleto',COMPLETE:'Completo',READY:'Pronto',FAILED:'Falhou',current:'Atual',pending:'Pendência de dados'};
function display(value:unknown){return value==null?'Não disponível':typeof value==='object'?JSON.stringify(value):String(value);}

export default function ObservationHistory(){
  const [filters,setFilters]=useState<Filters>(emptyFilters);
  const [applied,setApplied]=useState<Filters>(emptyFilters);
  const [rows,setRows]=useState<Row[]>([]);
  const [cursor,setCursor]=useState<string|null>(null);
  const [page,setPage]=useState<string|null>(null);
  const [refresh,setRefresh]=useState(0);
  const [zone,setZone]=useState('America/Sao_Paulo');
  const [boot,setBoot]=useState<{watchlists:{id:string;name:string}[];profiles:{id:string;name:string}[]}|null>(null);
  const [settings,setSettings]=useState<{enabled:boolean;horizons_minutes:number[]}|null>(null);
  const [horizons,setHorizons]=useState('');
  const [enabled,setEnabled]=useState(true);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [loading,setLoading]=useState(false);
  const [selected,setSelected]=useState<string|null>(null);
  const [detail,setDetail]=useState<Detail|null>(null);
  const [job,setJob]=useState<ExportJob|null>(null);
  const [exporting,setExporting]=useState(false);
  const [health,setHealth]=useState<{jobs:{status:string;jobs:number;with_error:number}[]}|null>(null);
  const jobId=job?.id, jobStatus=job?.status;
  const detailPanel=useRef<HTMLDivElement>(null);
  const date=(value:string)=>new Date(value).toLocaleString('pt-BR',{timeZone:zone});
  const query=(values:Filters)=>new URLSearchParams(Object.fromEntries(Object.entries(values).filter(([,v])=>v))).toString();

  useEffect(()=>{
    const controller=new AbortController();
    Promise.all([apiFetch<NonNullable<typeof boot>>('/mtf/bootstrap',{signal:controller.signal}),
      apiFetch<NonNullable<typeof settings>>('/mtf/observations/settings',{signal:controller.signal})])
      .then(([b,s])=>{setBoot(b);setSettings(s);setEnabled(s.enabled);setHorizons(s.horizons_minutes.join(', '));})
      .catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();
  },[]);

  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError('');
    apiFetch<NonNullable<typeof health>>('/mtf/observations/capture-health',{signal:controller.signal}).then(setHealth).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    apiFetch<{items:Row[];next_cursor:string|null}>(`/mtf/observations?${query(applied)}${page?`&before=${encodeURIComponent(page)}`:''}`,{signal:controller.signal})
      .then(data=>{setRows(previous=>page?[...previous,...data.items]:data.items);setCursor(data.next_cursor);})
      .catch(e=>{if(!controller.signal.aborted)setError(e.message);})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[applied,page,refresh]);

  useEffect(()=>{
    if(!selected){setDetail(null);return;}
    const controller=new AbortController();setDetail(null);
    apiFetch<Detail>(`/mtf/observations/${selected}`,{signal:controller.signal})
      .then(value=>{setDetail(value);detailPanel.current?.focus();})
      .catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();
  },[selected]);

  useEffect(()=>{
    if(!jobId||!jobStatus||['READY','FAILED'].includes(jobStatus))return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=()=>apiFetch<ExportJob>(`/mtf/observations/exports/${jobId}`,{signal:controller.signal})
      .then(value=>{setJob(previous=>previous?.id===value.id?{...previous,...value}:previous);if(!['READY','FAILED'].includes(value.status))timer=setTimeout(poll,3000);})
      .catch(e=>{if(!controller.signal.aborted){setError(e.message);timer=setTimeout(poll,10000);}});
    timer=setTimeout(poll,1000);
    return()=>{controller.abort();clearTimeout(timer);};
  },[jobId,jobStatus]);

  async function startExport(format:'xlsx'|'csv'){
    setExporting(true);setError('');
    try{const result=await apiFetch<ExportJob>('/mtf/observations/exports',{method:'POST',body:JSON.stringify({filters:Object.fromEntries(Object.entries(applied).filter(([,v])=>v)),format,timezone:zone})});setJob({...result,format});}
    catch(e){setError((e as Error).message);}finally{setExporting(false);}
  }
  async function download(){
    if(!job)return;
    try{const blob=await apiFetch<Blob>(`/mtf/observations/exports/${job.id}/download`,{},'blob');const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`mtf-history-${job.id}.${job.format==='xlsx'?'xlsx':'zip'}`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    catch(e){setError((e as Error).message);}
  }
  async function saveSettings(){
    setError('');setNotice('');
    try{const value=await apiFetch<NonNullable<typeof settings>>('/mtf/observations/settings',{method:'PUT',body:JSON.stringify({enabled,horizons_minutes:horizons.split(',').map(v=>Number(v.trim()))})});setSettings(value);setNotice('Configuração analítica salva. Regras do perfil preservadas.');}
    catch(e){setError((e as Error).message);}
  }

  return <main className="observation-history">
    <header><div><Link href="/grafico-mtf">← Gráfico MTF</Link><Link href="/grafico-mtf/replays">Replay de gatilhos ↗</Link><h1>Histórico de observações</h1><p>O que foi observado, quando e com quais dados.</p></div><span className="history-mode">Somente observação · ordens bloqueadas</span></header>
    <section className="history-intro"><p>Captura automática das watchlists monitoradas no MTF, mesmo com esta página fechada. Todos os estados são registrados. O histórico permanece após a saída do ativo.</p><p>Preço observado e fechamento do candle são distintos. Resultados futuros são descritivos, usam candles posteriores à observação e não representam operações executadas. Avaliações antigas sem captura completa continuam no histórico legado.</p></section>
    <form className="history-filters" onSubmit={e=>{e.preventDefault();setPage(null);setApplied({...filters,start:filters.start?new Date(filters.start+'Z').toISOString():'',end:filters.end?new Date(filters.end+'Z').toISOString():''});}}>
      <label>Watchlist<select value={filters.watchlist_id} onChange={e=>setFilters({...filters,watchlist_id:e.target.value})}><option value="">Todas</option>{boot?.watchlists.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
      <label>Ativo<input placeholder="ETH_USDT" value={filters.symbol} onChange={e=>setFilters({...filters,symbol:e.target.value.toUpperCase().replace('/','_')})}/></label>
      <label>Perfil<select value={filters.profile_id} onChange={e=>setFilters({...filters,profile_id:e.target.value})}><option value="">Todos</option>{boot?.profiles.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label>Estado<select value={filters.status} onChange={e=>setFilters({...filters,status:e.target.value})}><option value="">Todos</option>{['WAIT','ARMED','TRIGGERED','NO_TRADE'].map(s=><option key={s} value={s}>{states[s]}</option>)}</select></label>
      <label>Tendência<select value={filters.trend} onChange={e=>setFilters({...filters,trend:e.target.value})}><option value="">Todas</option><option value="UP">Alta</option><option value="DOWN">Baixa</option><option value="RANGE">Lateral</option></select></label>
      <label>Dados<select value={filters.quality} onChange={e=>setFilters({...filters,quality:e.target.value})}><option value="">Todos</option><option value="current">Atuais</option><option value="pending">Pendências</option></select></label>
      <label>Início (UTC)<input type="datetime-local" value={filters.start} onChange={e=>setFilters({...filters,start:e.target.value})}/></label>
      <label>Fim (UTC)<input type="datetime-local" value={filters.end} onChange={e=>setFilters({...filters,end:e.target.value})}/></label>
      <button type="submit" disabled={loading}>Aplicar filtros</button>
    </form>
    <div className="history-actions"><label>Exibir horários<select value={zone} onChange={e=>setZone(e.target.value)}><option value="America/Sao_Paulo">Brasília</option><option value="UTC">UTC</option></select></label><button onClick={()=>{setPage(null);setRefresh(v=>v+1);}} disabled={loading}>Atualizar</button><button disabled={exporting||job?.status==='PENDING'} onClick={()=>startExport('xlsx')}>Exportar Excel</button><button disabled={exporting||job?.status==='PENDING'} onClick={()=>startExport('csv')}>Exportar CSV ZIP</button><span>Exportação inclui todos os registros dos filtros aplicados.</span></div>
    {health?.jobs.some(j=>j.status==='PENDING')?<p role="status">Coleta pendente: {health.jobs.filter(j=>j.status==='PENDING').map(j=>`${j.jobs} registros (${j.with_error} com erro, retomada automática)`).join(', ')}</p>:null}
    {error?<p role="alert" className="history-error">{error}</p>:null}
    {notice?<p role="status">{notice}</p>:null}
    {job?<div role="status" className="history-export">Exportação: {states[job.status]??job.status}{job.error?` · ${job.error}`:''}{job.status==='READY'?<button onClick={download}>Baixar arquivo ({job.row_count} observações)</button>:null}</div>:null}
    <div className="history-table"><table><thead><tr><th>Ativo</th><th>Observado em ({zone})</th><th>Estado</th><th>Tendência</th><th>Preço observado</th><th>Fechamento</th><th>Score</th><th>Dados</th><th>Detalhes</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td>{row.symbol.replace('_','/')}</td><td>{date(row.observed_at)}</td><td>{states[row.status]??row.status}</td><td>{{UP:'Alta',DOWN:'Baixa',RANGE:'Lateral'}[row.trend??'']??'Não disponível'}</td><td>{display(row.observed_price)}<small>{row.price_quality==='current'?'Atual':row.price_quality==='stale'?'Desatualizado':'Horário não confirmado'}</small></td><td>{display(row.candle_close_price)}</td><td>{display(row.score?.total)}</td><td>{states[row.quality]??row.quality}</td><td><button onClick={()=>setSelected(row.id)} aria-label={`Ver observação de ${row.symbol} em ${date(row.observed_at)}`}>Ver captura</button></td></tr>)}</tbody></table></div>
    {!loading&&!rows.length?<p className="history-empty">Nenhuma observação para estes filtros. A captura começa após a publicação e não reconstrói indicadores históricos ausentes.</p>:null}
    {loading?<p role="status">Carregando observações…</p>:null}
    {cursor?<button disabled={loading} onClick={()=>setPage(cursor)}>Carregar anteriores</button>:null}
    {selected?<section className="history-detail" ref={detailPanel} tabIndex={-1}><button onClick={()=>setSelected(null)}>Fechar detalhes</button>{detail?<><h2>{detail.symbol} · {date(detail.observed_at)}</h2><MTFDecisionEvidence payload={detail.payload} replay={detail.decision_replay}/><h3>Indicadores capturados</h3><div className="history-table"><table><thead><tr><th>Indicador</th><th>Timeframe</th><th>Valor</th><th>Horário de origem</th><th>Produtor / uso</th><th>Qualidade</th></tr></thead><tbody>{detail.payload.indicators.map((i,index)=><tr key={`${i.producer}-${i.name}-${i.timeframe}-${index}`}><td>{i.name}</td><td>{i.timeframe}</td><td>{typeof i.value==='object'?<details><summary>Ver valores</summary><pre>{JSON.stringify(i.value,null,2)}</pre></details>:display(i.value)}</td><td>{i.source_at?date(i.source_at):'Não disponível'}</td><td>{i.producer} · {i.role==='context_only'?'Contexto':i.role==='structural_display_only'?'Visualização estrutural':'Disponível à avaliação'}</td><td>{i.quality}</td></tr>)}</tbody></table></div><h3>Acompanhamento posterior</h3><p>Retornos relativos ao preço capturado ou, se indisponível, ao fechamento identificado no resultado. Sem custos ou execução. Lacunas permanecem incompletas.</p>{detail.outcomes.map(r=><details key={r.horizon_minutes}><summary>{r.horizon_minutes} min · {states[r.status]??r.status} · alvo {date(r.due_at)}</summary><pre>{JSON.stringify(r.payload,null,2)}</pre></details>)}<details><summary>Próxima mudança de estado observada</summary><pre>{JSON.stringify(detail.next_status_change,null,2)}</pre></details><details><summary>Captura completa e proveniência</summary><pre>{JSON.stringify(detail.payload,null,2)}</pre></details></>:<p>Carregando captura…</p>}</section>:null}
    <details className="history-settings"><summary>Configuração da coleta e dos horizontes</summary><p>Configuração exclusiva da análise. Não modifica o Edit Profile nem habilita ordens.</p><label><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/> Captura habilitada nas watchlists monitoradas</label><label>Horizontes em minutos, separados por vírgula<input value={horizons} onChange={e=>setHorizons(e.target.value)}/></label><button disabled={!settings} onClick={saveSettings}>Salvar configuração analítica</button></details>
  </main>;
}
