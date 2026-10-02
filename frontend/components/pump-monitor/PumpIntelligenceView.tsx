"use client";
import {memo,useCallback,useEffect,useRef,useState} from "react";
import {apiFetch} from "@/lib/api";
import {Intelligence,Support,latestRequestGuard,isIntelligenceStale} from "@/lib/pump-intelligence";
import styles from "./opportunity.module.css";

const fmt=(v:number|null)=>v===null?"Desconhecido":new Intl.NumberFormat("pt-BR",{maximumFractionDigits:3}).format(v);
const time=(v:string|null)=>v?new Date(v).toLocaleString("pt-BR",{timeZone:"UTC"})+" UTC":"Sem dados";

const SupportTable=memo(function SupportTable({rows}:{rows:(Support&{pattern:string})[]}){
 return <div className={styles.scroll}><table><thead><tr>{["Grupo / amostra","Cobertura","+0,60% bruto","+0,80% bruto"].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
  {rows.map(r=><tr key={r.pattern}><td>{r.pattern}<small>{r.observations} observações · {r.episodes} episódios · {r.instruments} ativos · {r.days} dias</small><small>{time(r.from)} → {time(r.to)}</small></td>
   <td>Completa {r.coverage.complete}; pendentes {r.coverage.pending}; incompleta {r.coverage.incomplete}<small>Com gaps {r.coverage.with_gaps}; fronteira ambígua {r.coverage.boundary_ambiguous}; ordem ambígua {r.coverage.order_ambiguous}</small></td>
   {["0.6","0.8"].map(target=>{const t=r.targets[target];return <td key={target}>
    <strong>{t.descriptive_hit_rate===null?"Sem desfechos conhecidos":`${fmt(t.descriptive_hit_rate*100)}% (${t.hits}/${t.known})`}</strong>
    <small>Toques {t.hits}; não tocou {t.misses}; desconhecidos {t.unknown}. Cobertura completa: {t.complete_hits}/{t.complete_known} desfechos.</small>
    <small>Tempo exato até alvo: mediana {fmt(t.time_to_touch_seconds.median)} s; média {fmt(t.time_to_touch_seconds.mean)} s (N={t.time_to_touch_seconds.known}).</small>
    <small>Tempo por intervalo: medianas dos limites {fmt(t.time_interval_lower_seconds.median)}–{fmt(t.time_interval_upper_seconds.median)} s (N={t.time_interval_lower_seconds.known}); sem tempo entre hits {t.time_unknown_among_hits}; primeiro toque censurado {t.first_touch_censored}.</small>
    <small>Queda antes do alvo: mediana {fmt(t.drawdown_before_touch_pct.median)}%; média {fmt(t.drawdown_before_touch_pct.mean)}% (N={t.drawdown_before_touch_pct.known}). MAE antes: {fmt(t.mae_before_touch_pct.median)}%.</small>
    <small>Pré-toque desconhecido {t.pre_touch_unknown}; alvo não alcançado {t.pre_touch_not_reached}; contrato sem medição {t.pre_touch_not_measured}.</small>
   </td>;})}</tr>)}
 </tbody></table></div>;
});

export default function PumpIntelligenceView({active=true}:{active?:boolean}){
 const [data,setData]=useState<Intelligence|null>(null),[error,setError]=useState(""),[loading,setLoading]=useState(false);
 const [horizon,setHorizon]=useState(5),[cohortId,setCohortId]=useState("");
 const [draft,setDraft]=useState('[{"field":"rsi","op":"between","value":[65,76]},{"field":"adx","op":"gt","value":25}]');
 const [applied,setApplied]=useState<unknown[]|null>(null),[lastSuccess,setLastSuccess]=useState<number|null>(null),[now,setNow]=useState(Date.now);
 const guard=useRef(latestRequestGuard());
 const refresh=useCallback(async()=>{
  const request=guard.current.begin();setLoading(true);
  try{
   const result=await apiFetch<Intelligence>(applied?"/pump-monitor/opportunities/explore":`/pump-monitor/opportunities/intelligence?horizon=${horizon}`,
    {method:applied?"POST":"GET",cache:"no-store",...(applied?{body:JSON.stringify({conditions:applied,horizon_minutes:horizon})}:{})});
   if(!guard.current.isCurrent(request))return;
   setData(result);setLastSuccess(Date.now());setError("");
  }catch{if(guard.current.isCurrent(request))setError("Falha ao atualizar a Inteligência. Os dados anteriores foram preservados e podem estar atrasados.");}
  finally{if(guard.current.isCurrent(request))setLoading(false);}
 },[applied,horizon]);
 useEffect(()=>{if(!active){guard.current.invalidate();return;}void refresh();const timer=window.setInterval(()=>void refresh(),60000);const clock=window.setInterval(()=>setNow(Date.now()),10000);
  const requestGuard=guard.current;return()=>{clearInterval(timer);clearInterval(clock);requestGuard.invalidate();};},[refresh,active]);
 const selectionMatches=data?.scope.horizon_minutes===horizon;
 const selected=selectionMatches?(cohortId?data?.cohorts.find(c=>c.cohort_id===cohortId):data?.cohorts[0]):undefined;
 const stale=isIntelligenceStale(data,now,lastSuccess)||Boolean(error)||!selectionMatches;
 const explore=()=>{try{const rules:unknown=JSON.parse(draft);if(!Array.isArray(rules)||!rules.length||rules.length>20)throw Error();guard.current.invalidate();setApplied(rules);}catch{setError("Informe de 1 a 20 condições AND válidas.");}};
 return <section aria-label="Inteligência descritiva Pump">
  <h2>Inteligência e qualidade · ML observacional</h2><p>Frequências descritivas de observações correlacionadas por episódio. Não são probabilidades preditivas, evidência fora da amostra ou ordens. Inferência e contribuição ML permanecem zero.</p>
  <div className={styles.toolbar}><label>Horizonte <select aria-label="Horizonte da Inteligência" value={horizon} onChange={e=>{guard.current.invalidate();setHorizon(Number(e.target.value));}}>{(data?.scope.available_horizons??[5,10,15,30,60,120]).map(h=><option key={h} value={h}>{h} min</option>)}</select></label>
   <button onClick={()=>void refresh()} disabled={loading}>{loading?"Atualizando…":"Atualizar Inteligência"}</button><span>Atualização automática enquanto esta aba estiver aberta: 60s.</span></div>
  <p role="status">{stale?"Dados atrasados / aguardando atualização":"Dados atualizados"} · resposta recebida: {time(lastSuccess?new Date(lastSuccess).toISOString():null)} · agregado calculado: {time(data?.computed_at??null)} · capturas até: {time(data?.data_through??null)} · labels até: {time(data?.labels_through??null)}</p>
  {error&&<p role="alert" className={styles.error}>{error}</p>}{data?.freshness.refresh_failed&&<p role="alert">A consulta ao banco falhou; agregado anterior preservado. Nova tentativa no próximo ciclo.</p>}
  {data&&<><p>Escopo: até {data.scope.sample_limit} candidatos recentes, dentro de {data.scope.window_hours} horas, incluindo abaixo do limite. Foram amostradas {data.scope.sampled_observations} observações. {data.scope.sample_truncated?"Limite de amostra atingido; não é toda a janela.":"Amostra retornada dentro da janela."} Não representa o histórico inteiro. Cache do agregador: {data.scope.cache_seconds}s.</p>
   <label>Contrato compatível <select aria-label="Contrato da Inteligência" value={cohortId||selected?.cohort_id||""} onChange={e=>setCohortId(e.target.value)}>{cohortId&&!selected&&<option value={cohortId}>Contrato selecionado fora da amostra atual</option>}{data.cohorts.map(c=><option key={c.cohort_id} value={c.cohort_id}>{c.label_version} · score {c.score_config_hash.slice(0,10)} · label {c.label_spec_hash.slice(0,10)} · {c.baseline.observations} observações</option>)}</select></label>
   {cohortId&&!selected&&selectionMatches&&<p>O contrato selecionado não está na amostra recente. Selecione outro para comparar; os contratos não foram combinados.</p>}
   {!data.cohorts.length&&<p>Sem candidatos nessa janela.</p>}
   {selected&&<><details><summary>Identidade completa do contrato</summary><pre>{JSON.stringify({horizon:selected.horizon_minutes,score:selected.score_config_hash,label:selected.label_spec_hash,features:selected.feature_spec_hash,producer:selected.producer_config_hash,costs:selected.cost_policy_hash,reference:selected.reference_policy},null,2)}</pre></details>
    <p>Conhecidos incluem toques observados; um toque pode ser conhecido apesar de gaps. Desconhecidos e pendentes nunca viram perdas. Tempo e queda usam apenas medições admissíveis; não há interpolação ou mistura de contratos.</p>
    <h3>Taxa-base deste contrato</h3><SupportTable rows={[{pattern:"Todos os candidatos amostrados deste contrato",...selected.baseline}]}/>
    <h3>Faixas de pontos de confirmação</h3><p>Faixas descritivas configuradas: {data.scope.score_edges.join(", ")}. Não são limites novos de admissão.</p><SupportTable rows={selected.score_bands}/>
    <h3>Combinações de confirmações independentes</h3><SupportTable rows={selected.patterns}/>
    {selected.exploration&&<><h3>Condições AND aplicadas</h3><SupportTable rows={[{pattern:"Exploração deste contrato",...selected.exploration}]}/></>}
   </>}
   <div className={styles.config}><h3>Explorar condições AND sobre indicadores</h3><p>Os filtros aplicados permanecem durante as atualizações. Null não confirma uma condição. A exploração não cria regra de compra.</p><textarea aria-label="Condições AND JSON" rows={4} value={draft} onChange={e=>setDraft(e.target.value)}/><button onClick={explore}>Aplicar condições</button><button onClick={()=>{guard.current.invalidate();setApplied(null);}}>Limpar condições aplicadas</button>{applied&&<pre>{JSON.stringify(applied,null,2)}</pre>}</div>
   <h3>Gates e execuções do treino separado</h3><pre>{JSON.stringify(data.gates,null,2)}</pre><p>Job habilitado não significa modelo validado. Contagens por coorte não registradas não são inventadas. Autopromoção desabilitada.</p>
   {data.training_runs.map(r=><p key={r.run_id}>{time(r.started_at)} · {r.status} · {r.payload.reason??"Execução registrada"} · {r.payload.duration_seconds??"-"} s</p>)}
  </>}
 </section>;
}
