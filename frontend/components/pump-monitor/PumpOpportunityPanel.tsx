"use client";

import {useCallback,useEffect,useState} from "react";
import {apiGet,apiPost,apiPut} from "@/lib/api";
import styles from "./opportunity.module.css";

type Rule={field:string;op:string;value?:unknown;other_field?:string};
type Reference={price:number|null;at:string|null;policy:string;reason:string|null;levels:Record<string,number|null>};
type Observation={observation_id:string;episode_id:string;instrument_id:string;listing_id:string;symbol:string;decision_at:string;
  state:string;score_base:number;score_final:number|null;score_max:number;score_version:string;confirmed_groups:number;reference:Reference;
  episode_reference:Reference;values:Record<string,number|string|boolean|null>;vetos:string[];freshness?:{status:string;age_seconds:number};
  risks:Record<string,number|null>;simulation:{eligible:boolean;threshold:number};
  ledger:{group:string;result:boolean|null;points:number;reason:string;inputs:Record<string,unknown>;conditions:Rule[]}[];
  manifest:Record<string,unknown>;availability:Record<string,unknown>;ml:{status:string;delta:number;probability:number|null}};
type Snapshot={rows:Observation[];status:string;produced_at:string|null;total_eligible:number;total_blocked:number;total_stale:number;config:Record<string,unknown>};
type Label={horizon_minutes:number;status:string;reason:string|null;endpoint_return_pct:number|null;mfe_pct:number|null;mae_pct:number|null;
  targets:Record<string,{hit:boolean|null;first_touch_censored:boolean;first_touch_interval:string[]|null}>;missing_minutes?:number;boundary_ambiguous?:boolean;
  downside?:{hit:boolean|null};order_ambiguous?:boolean};
type Detail={observation:Observation;labels:Label[];timeline:{observation_id:string;decision_at:string;state:string;reference:Reference;score:number|null;vetos:string[]}[]};
type Support={observations:number;episodes:number;instruments:number;days:number;known:number;unknown_or_pending:number;hits:number;descriptive_hit_rate:number|null;from:string|null;to:string|null};
type Intelligence={model:{status:string;reason:string};baseline:Support;patterns:(Support&{pattern:string})[];exploration:Support|null;sample_limit:number;sample_policy:string};

const fmt=(v:unknown)=>v===null||v===undefined?"—":typeof v==="number"?new Intl.NumberFormat("pt-BR",{maximumFractionDigits:6}).format(v):String(v);
const time=(v:string|null)=>v?new Date(v).toLocaleString("pt-BR",{timeZone:"UTC"})+" UTC":"—";
const hit=(v:boolean|null|undefined)=>v===true?"Observado":v===false?"Não tocou (cobertura completa)":"Desconhecido";

function Trajectory({detail}:{detail:Detail}){
  const ref=detail.observation.reference;
  const points=detail.timeline.filter(t=>t.reference.price!==null);
  if(ref.price===null||!points.length)return <p>Gráfico indisponível: sem referência contemporânea válida.</p>;
  const levels=[ref.price,...Object.values(ref.levels).filter((p):p is number=>p!==null),...points.map(p=>p.reference.price as number)];
  const lo=Math.min(...levels),hi=Math.max(...levels);const span=hi-lo||hi*0.001;
  const start=new Date(points[0].decision_at).getTime(),end=new Date(points[points.length-1].decision_at).getTime();
  const x=(t:string)=>55+(new Date(t).getTime()-start)/Math.max(60000,end-start)*850;
  const y=(p:number)=>210-(p-lo)/span*170;
  return <figure className={styles.chart} aria-label={`Trajetória de ${detail.observation.symbol}, referência congelada ${ref.price}`}>
    <svg viewBox="0 0 1000 245" role="img" aria-label="Preços de referência capturados; intervalos sem dados não são interpolados">
      {[["Referência",ref.price],["+0,60%",ref.levels["0.6"]],["+0,80%",ref.levels["0.8"]]].map(([name,p])=>typeof p==="number"&&<g key={String(name)}>
        <line x1="55" x2="910" y1={y(p)} y2={y(p)} stroke={name==="Referência"?"#8794a5":"#40cfa3"} strokeDasharray="6 5"/>
        <text x="920" y={y(p)+4} fill="#b9c9d6" fontSize="12">{name}</text></g>)}
      {points.map((p,i)=>{const previous=points[i-1];return <g key={p.observation_id}>
        {previous&&new Date(p.decision_at).getTime()-new Date(previous.decision_at).getTime()<=180000&&<line x1={x(previous.decision_at)} y1={y(previous.reference.price as number)} x2={x(p.decision_at)} y2={y(p.reference.price as number)} stroke="#49b7ed" strokeWidth="2"/>}
        <circle cx={x(p.decision_at)} cy={y(p.reference.price as number)} r="3" fill="#49b7ed"><title>{time(p.decision_at)} · {fmt(p.reference.price)}</title></circle></g>})}
    </svg><figcaption>Referência selecionada: {fmt(ref.price)} · {time(detail.observation.decision_at)}. Pontos são cotações capturadas, não uma trajetória completa de trades.</figcaption>
  </figure>;
}

function SupportTable({rows}:{rows:(Support&{pattern:string})[]}){
  return <div className={styles.scroll}><table><thead><tr>{["Padrão","Observações","Episódios","Ativos","Dias","Válidos","Desconhecidos / pendentes","Toques observados"].map(x=><th key={x}>{x}</th>)}</tr></thead>
    <tbody>{rows.map(p=><tr key={p.pattern}><td>{p.pattern}</td><td>{p.observations}</td><td>{p.episodes}</td><td>{p.instruments}</td><td>{p.days}</td><td>{p.known}</td><td>{p.unknown_or_pending}</td><td>{p.descriptive_hit_rate===null?"Amostra insuficiente":`${fmt(p.descriptive_hit_rate*100)}% (${p.hits}/${p.known})`}</td></tr>)}</tbody></table></div>;
}

export default function PumpOpportunityPanel(){
  const [tab,setTab]=useState("radar"),[snapshot,setSnapshot]=useState<Snapshot|null>(null),[detail,setDetail]=useState<Detail|null>(null);
  const [intelligence,setIntelligence]=useState<Intelligence|null>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const [filter,setFilter]=useState("todos"),[configText,setConfigText]=useState(""),[configOpen,setConfigOpen]=useState(false);
  const [history,setHistory]=useState<Observation[]>([]),[cursor,setCursor]=useState<string|null>(null);
  const [conditions,setConditions]=useState('[{"field":"rsi","op":"between","value":[65,76]},{"field":"adx","op":"gt","value":25},{"field":"ema9","op":"gt","other_field":"price"}]');
  const load=useCallback(async()=>{try{setSnapshot(await apiGet<Snapshot>("/pump-monitor/opportunities"));setError("");}catch{setError("Não foi possível atualizar o radar. Os dados exibidos podem estar atrasados.");}},[]);
  useEffect(()=>{void load();const timer=window.setInterval(()=>void load(),60000);return()=>window.clearInterval(timer);},[load]);
  const open=async(id:string)=>{setBusy(true);try{setDetail(await apiGet<Detail>(`/pump-monitor/opportunities/${id}`));setTab("detail");setError("");}catch{setError("Não foi possível consultar esta observação.");}finally{setBusy(false);}};
  const showIntelligence=async()=>{setTab("intelligence");try{setIntelligence(await apiGet<Intelligence>("/pump-monitor/opportunities/intelligence"));}catch{setError("Não foi possível consultar a inteligência Pump.");}};
  const loadHistory=async(next:string|null)=>{try{const result=await apiGet<{rows:Observation[];next_cursor:string|null}>(`/pump-monitor/opportunities/history?limit=50${next?`&cursor=${encodeURIComponent(next)}`:""}`);setHistory(h=>next?[...h,...result.rows]:result.rows);setCursor(result.next_cursor);}catch{setError("Não foi possível consultar o histórico.");}};
  const saveConfig=async()=>{setBusy(true);try{await apiPut("/pump-monitor/opportunities/config",JSON.parse(configText));setConfigOpen(false);await load();}catch{setError("Configuração rejeitada. Verifique unidades, limites e flags; ML e conexão à Pool exigem autorização separada.");}finally{setBusy(false);}};
  const rows=(snapshot?.rows??[]).filter(r=>filter==="todos"||r.state===filter);
  return <section className={styles.panel}>
    <header className={styles.header}><div><span className={styles.kicker}>PUMP / CONTINUIDADE</span><h1>Oportunidades desde o preço atual</h1><p>Confirmações atuais · alvo bruto +0,80% em 5 minutos · acompanhamento +0,60%</p></div><span className={styles.badge}>ML em observação · 0 pontos</span></header>
    <div className={styles.notice}>O horizonte avalia o resultado futuro. Você pode acompanhar agora; o motor de saída existente continua sob seu contrato atual. Pontos não são probabilidade.</div>
    <nav className={styles.tabs} aria-label="Visões Pump"><button onClick={()=>setTab("radar")} aria-pressed={tab==="radar"}>Radar</button><button onClick={()=>setTab("detail")} aria-pressed={tab==="detail"}>Detalhe do ativo</button><button onClick={()=>void showIntelligence()} aria-pressed={tab==="intelligence"}>Inteligência e qualidade</button><button onClick={()=>void load()}>Atualizar</button><button onClick={()=>{setConfigText(JSON.stringify(snapshot?.config??{},null,2));setConfigOpen(v=>!v);}}>Configuração observacional</button></nav>
    {error&&<p className={styles.error} role="alert">{error}</p>}
    {configOpen&&<div className={styles.config}><h2>Contrato Pump separado</h2><p>Pesos provisórios, unidade de pontos de confirmação. Esta configuração não altera o score legado nem conecta a Pool. Flags de treino, inferência e delta permanecem desabilitadas.</p><textarea aria-label="Configuração Pump JSON" value={configText} onChange={e=>setConfigText(e.target.value)} rows={18}/><button disabled={busy} onClick={()=>void saveConfig()}>Validar e salvar</button></div>}
    {tab==="radar"&&<><div className={styles.summary}><span>Universo capturado <b>{snapshot?.total_eligible??"—"}</b></span><span>Bloqueados <b>{snapshot?.total_blocked??"—"}</b></span><span>Atrasados <b>{snapshot?.total_stale??"—"}</b></span><span>Atualização lógica <b>60s</b></span></div>
      <div className={styles.toolbar}><label>Estado <select value={filter} onChange={e=>setFilter(e.target.value)}>{["todos","formando","ativo","enfraquecendo","invalidado"].map(x=><option key={x}>{x}</option>)}</select></label><span>Produzido: {time(snapshot?.produced_at??null)}</span><button onClick={()=>void loadHistory(null)}>Carregar histórico</button></div>
      {snapshot?.status==="disabled"&&<p>O novo radar está desabilitado. Ative somente coleta e interface no contrato observacional para começar.</p>}
      {snapshot?.status==="collecting"&&<p>Coleta iniciando. As primeiras observações aparecerão no próximo ciclo; treinamento não é requisito.</p>}
      {snapshot?.status==="storage_budget_exhausted"&&<p role="alert">Coleta pausada: o domínio Pump atingiu seu orçamento de armazenamento. O histórico foi preservado; revise capacidade e retenção antes de ampliar.</p>}
      <div className={styles.scroll}><table><thead><tr>{["Ativo / estado","Pontos / grupos","Referência congelada","RSI / ADX","Fluxo / progresso","Riscos e qualidade","Pool simulada"].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r.observation_id} className={r.vetos.length?styles.blocked:""}><td><button className={styles.asset} onClick={()=>void open(r.observation_id)}>{r.symbol}</button><small>{r.state} · {r.freshness?.status}</small></td><td><strong>{fmt(r.score_final)} / {r.score_max}</strong><small>Base {r.score_base} · {r.confirmed_groups} grupos · ML 0</small></td><td>{fmt(r.reference.price)}<small>{time(r.decision_at)} · ask Gate</small></td><td>{fmt(r.values.rsi)} / {fmt(r.values.adx)}</td><td>Delta {fmt(r.values.delta_norm)}<small>Progresso ATR {fmt(r.values.price_progress_atr)}</small></td><td>Spread {fmt(r.risks.spread_pct)}% · slippage {fmt(r.risks.estimated_slippage_buy_pct)}%<small>{r.vetos.join(" · ")||"Sem veto observado"}</small></td><td>{r.simulation.eligible?"Elegível na simulação":"Bloqueada / abaixo do limite"}<small>Limite {r.simulation.threshold} pontos · desconectada</small></td></tr>)}</tbody></table></div>
      {history.length>0&&<div className={styles.history}><h2>Observações preservadas</h2>{history.map(r=><button key={r.observation_id} onClick={()=>void open(r.observation_id)}>{r.symbol} · {time(r.decision_at)} · {r.state}</button>)}{cursor&&<button onClick={()=>void loadHistory(cursor)}>Mais observações</button>}</div>}</>}
    {tab==="detail"&&(detail?<><div className={styles.toolbar}><h2>{detail.observation.symbol} · {detail.observation.state}</h2><span>Observação selecionada {time(detail.observation.decision_at)}</span></div><Trajectory detail={detail}/><p>Primeira referência do episódio: {fmt(detail.observation.episode_reference.price)} · {time(detail.observation.episode_reference.at)}. A atualização do radar não move a referência selecionada.</p>
      <h2>Ledger de confirmações</h2><div className={styles.scroll}><table><thead><tr><th>Grupo</th><th>Resultado</th><th>Pontos</th><th>Insumos e regra AND</th></tr></thead><tbody>{detail.observation.ledger.map(e=><tr key={e.group}><td>{e.group}</td><td>{e.result===null?"Dados insuficientes":e.result?"Confirmado":"Não confirmado"}</td><td>{e.points}</td><td><code>{JSON.stringify(e.inputs)}</code><small>{JSON.stringify(e.conditions)}</small></td></tr>)}</tbody></table></div>
      <h2>Resultados futuros</h2><p>Labels de minuto usam apenas barras integralmente posteriores à decisão. Fronteiras e gaps preservam desconhecidos; nenhum comando é enviado ao trailing.</p><div className={styles.scroll}><table><thead><tr><th>Horizonte</th><th>+0,60% bruto</th><th>+0,80% bruto</th><th>Endpoint</th><th>Qualidade</th></tr></thead><tbody>{detail.labels.map(l=><tr key={l.horizon_minutes}><td>{l.horizon_minutes} min</td><td>{hit(l.targets["0.6"]?.hit)}</td><td>{hit(l.targets["0.8"]?.hit)}{l.targets["0.8"]?.first_touch_censored&&<small>Primeiro toque censurado</small>}</td><td>{fmt(l.endpoint_return_pct)}%</td><td>{l.status} · {l.reason??"cobertura completa"}{l.order_ambiguous&&<small>Ordem alvo / queda ambígua</small>}</td></tr>)}</tbody></table></div>{!detail.labels.length&&<p>Pendente: resultados ainda não calculados ou coleta de labels desabilitada.</p>}
      <h2>Timeline do episódio</h2><div className={styles.history}>{detail.timeline.map(t=><button key={t.observation_id} onClick={()=>void open(t.observation_id)}>{time(t.decision_at)} · {t.state} · {fmt(t.score)} pontos</button>)}</div><details><summary>Fontes e versões da decisão</summary><pre>{JSON.stringify({instrument_id:detail.observation.instrument_id,listing_id:detail.observation.listing_id,manifest:detail.observation.manifest,availability:detail.observation.availability},null,2)}</pre></details></>:<p>Selecione um ativo no radar ou uma observação do histórico.</p>)}
    {tab==="intelligence"&&<><h2>XGBoost Pump · {intelligence?.model.status??"carregando"}</h2><p>Dataset e registro próprios. Nenhum modelo validado; contribuição aplicada zero. Frequências abaixo são descritivas, com observações correlacionadas por episódio. Não são previsões calibradas nem resultados fora da amostra.</p>{intelligence&&<><SupportTable rows={[{pattern:"Taxa-base da coorte recente",...intelligence.baseline},...intelligence.patterns]}/><p>Janela: {time(intelligence.baseline.from)} → {time(intelligence.baseline.to)} · amostra limitada aos {intelligence.sample_limit} registros recentes, incluindo candidatos abaixo do limite.</p><div className={styles.config}><h2>Explorar condições AND</h2><p>Os operadores são literais: EMA &gt; preço significa preço abaixo da EMA. Null não confirma uma condição. A exploração não cria uma regra de admissão.</p><textarea aria-label="Condições AND JSON" rows={5} value={conditions} onChange={e=>setConditions(e.target.value)}/><button onClick={async()=>{try{setIntelligence(await apiPost<Intelligence>("/pump-monitor/opportunities/explore",{conditions:JSON.parse(conditions)}));}catch{setError("Condições inválidas ou consulta indisponível.");}}}>Pesquisar padrão</button>{intelligence.exploration&&<SupportTable rows={[{pattern:"Exploração AND",...intelligence.exploration}]}/>}</div></>}</>}
  </section>;
}
