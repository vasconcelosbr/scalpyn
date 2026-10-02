"use client";
import {memo,useState} from "react";
import {Cohort,ExecutiveGroup,ShadowContext,Support,completeTargetNarrative,descriptiveComparison,executiveTime,targetNarrative} from "@/lib/pump-intelligence";
import styles from "./opportunity.module.css";

const number=(v:number)=>new Intl.NumberFormat("pt-BR",{maximumFractionDigits:2}).format(v);
const rate=(v:number|null)=>v===null?"indeterminada":`${number(v*100)}% entre conhecidos`;
const names:Record<string,string>={rsi:"RSI",adx:"ADX",rvol_strict:"Volume relativo",delta_norm:"Saldo agressor normalizado",
 buy_persistence:"Persistência compradora",spread_pct:"Spread (%)",price_extension_atr:"Extensão do preço (ATR)"};
const unknownReasons:Record<string,string>={open:"ainda aberto",resolution_timestamp_missing:"sem timestamp de resolução verificável",
 exit_contract_unverified:"contrato de saída não verificável",barrier_evidence_unverified:"evidência da barreira incompleta"};

const Insight=memo(function Insight({item,baseline,onExplore}:{item:ExecutiveGroup;baseline:Support;onExplore:(rules:unknown[])=>void}){
 const s=item.support;
 return <article className={styles.insightCard}>
  <h4>{item.field?item.condition.replace(item.field,names[item.field]??item.field):item.condition}</h4>
  <span className={styles.exploratory}>Exploratório · insuficiente para criar regra</span>
  <p>{targetNarrative(s,"0.8")} Alvo +0,80% bruto.</p>
  <details><summary>Comparar com a taxa-base e ver suporte</summary>
  <p>{s.observations} observações · {s.episodes} episódios · {s.instruments} ativos. Cobertura completa {s.coverage.complete}/{s.observations}; pendentes {s.coverage.pending}; com gaps {s.coverage.with_gaps}.</p>
  {["0.6","0.8"].map(target=>{const t=s.targets[target],b=baseline.targets[target],c=descriptiveComparison(s,baseline,target);return <div key={target} className={styles.insightOutcome}>
   <strong>Alvo +{target==="0.6"?"0,60":"0,80"}% bruto</strong>
   <p>{targetNarrative(s,target)}</p>
   <p>Grupo: {t.hits}/{t.known} ({rate(c.groupRate)}). Taxa-base comparável: {b.hits}/{b.known} ({rate(c.baselineRate)}); {b.unknown} indeterminados na base.</p>
   <p>{c.differencePoints===null?"Evidência insuficiente para comparar.":c.differencePoints>0?`Ponto favorável a testar: frequência entre conhecidos ${number(c.differencePoints)} pontos percentuais acima da base nesta amostra.`:c.differencePoints<0?`Risco a investigar: frequência entre conhecidos ${number(-c.differencePoints)} pontos percentuais abaixo da base nesta amostra.`:"Frequência entre conhecidos igual à base nesta amostra."} Cobertura e composição dos episódios podem explicar a diferença.</p>
   <small>Participação entre os toques da base: {t.hits}/{b.hits} ({c.winnerConcentration===null?"sem toques na base":`${number(c.winnerConcentration*100)}%`}). Essa concentração não é a taxa de acerto do grupo. {completeTargetNarrative(t)}</small>
  </div>;})}
  {item.observed_range&&<p>Valores observados: {number(item.observed_range[0])} a {number(item.observed_range[1])}. Faixa derivada da mediana da amostra, sem otimizar pelos resultados.</p>}
  <small>Período: {executiveTime(s.from)} → {executiveTime(s.to)}. Mesmo contrato e horizonte da taxa-base; não inclui outros perfis ou produtores.</small>
  </details>{item.condition_rule&&<button onClick={()=>onExplore([item.condition_rule])}>Explorar esta condição na amostra</button>}
 </article>;
});

export default function PumpExecutiveSummary({cohort,shadow,onExplore}:{cohort:Cohort;shadow?:ShadowContext;onExplore:(rules:unknown[])=>void}){
 const [view,setView]=useState("hours"),[shadowId,setShadowId]=useState("");
 const base=cohort.baseline,e=cohort.executive;
 const groups=view==="hours"?(e?.groups.filter(g=>g.kind==="hour")??[]):view==="indicators"?(e?.groups.filter(g=>g.kind==="indicator")??[]):[
  ...cohort.score_bands.map(s=>({kind:"score",condition:`Pontos de confirmação ${s.pattern}`,support:s})),
  ...cohort.patterns.map(s=>({kind:"confirmations",condition:`Confirmações: ${s.pattern}`,support:s})),
  ...(cohort.exploration?[{kind:"filter",condition:"Condições AND aplicadas",support:cohort.exploration}]:[])];
 const shadowCohorts=shadow?.cohorts??[];
 const selectedShadow=shadowId?shadowCohorts.find(c=>c.cohort_id===shadowId):[...shadowCohorts].sort((a,b)=>b.baseline.known-a.baseline.known)[0];
 return <section aria-label="Resumo executivo da Inteligência" className={styles.executive}>
  <div className={styles.executiveHeading}><div><span className={styles.kicker}>LEITURA DO HISTÓRICO</span><h3>Resumo executivo</h3></div><span className={styles.exploratory}>Amostra exploratória · ML zero</span></div>
  <p>O que aconteceu nesta amostra — e o que merece um teste separado. Contrato {cohort.label_version}, horizonte {cohort.horizon_minutes} min. {base.observations} observações, {base.episodes} episódios e {e?.local_days??base.days} dias {e?"GMT−3":"UTC"}; {executiveTime(base.from)} → {executiveTime(base.to)}.</p>
  <div className={styles.insightGrid}>{["0.6","0.8"].map(target=><article key={target} className={styles.resultCard}><h4>Alvo +{target==="0.6"?"0,60":"0,80"}% bruto</h4><p>{targetNarrative(base,target)}</p><small>Os indeterminados permanecem no total e não viram perdas. Frequência entre conhecidos não é taxa operacional nem previsão.</small></article>)}</div>
  <div className={styles.executiveRisk}><h4>Prioridade para melhorar a leitura</h4><p>Cobertura completa em {base.coverage.complete}/{base.observations}; {base.coverage.pending} pendentes; {base.coverage.incomplete} incompletas e {base.coverage.with_gaps} com gaps. Toques podem estar comprovados mesmo quando o restante do caminho é desconhecido. Complete a evidência antes de concluir sobre falhas, tempo ou queda.</p><p>Melhor/pior horário: evidência insuficiente para um ranking acionável. Horários abaixo são do início da observação em GMT−3 fixo; exposição significa observações amostradas, não todas as oportunidades da hora.</p></div>
  <div className={styles.tabs} aria-label="Recortes do resumo">{[["hours","Horários GMT−3"],["indicators","Faixas de indicadores"],["scores","Score e confirmações"]].map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</div>
  {view==="indicators"&&<p>Faixas construídas pelos valores observados, sem novos limites de admissão. {e?.indicator_availability.map(f=>`${names[f.field]??f.field}: ${f.available} disponíveis, ${f.missing} ausentes`).join(" · ")}. Fluxo agressor não mede entrada líquida de capital.</p>}
  <div className={styles.insightGrid}>{groups.map(g=><Insight key={`${g.kind}:${g.condition}`} item={g} baseline={base} onExplore={onExplore}/>)}</div>
  {!groups.length&&<p>Sem suporte nesse recorte da amostra. Isso não demonstra ausência de resultados no histórico.</p>}
  <p className={styles.notice}>Sugestão para teste: {e?.suggestion??"Repetir o recorte em outro período do mesmo contrato."} Há múltiplos recortes sobre os mesmos dados, com possível sobreposição de episódios; padrões podem surgir por acaso. Nenhum resultado foi validado fora da amostra. Nenhuma regra ou ordem será criada por esta leitura.</p>
  <details><summary>SL no Shadow — simulações separadas</summary>
   <p>Não há associação direta comprovada entre estas observações Pump e os trades Shadow. Os dados abaixo usam somente o histórico Shadow, separados por origem, perfil, versões e política de saída. Não representam lucro real nem justificam mudar o SL do Pump.</p>
   {!shadow||shadow.status==="unavailable"?<p>Contexto Shadow indisponível nesta análise; não foi interpretado como zero SLs.</p>:<>
    <p>Amostra temporal limitada: {shadow.sampled_rows} registros de todos os estados; {shadow.unverified_rows} sem contrato/linhagem admissível para comparação. Atualiza junto com a análise manual.</p>
    {!shadowCohorts.length&&<p>Sem coorte verificável nesta amostra. Evidência insuficiente para sugestões sobre SL.</p>}
    {!!shadowCohorts.length&&<label>Perfil/contrato Shadow <select aria-label="Contrato Shadow do resumo" value={shadowId||selectedShadow?.cohort_id||""} onChange={ev=>setShadowId(ev.target.value)}>{shadowId&&!selectedShadow&&<option value={shadowId}>Seleção fora da amostra atual</option>}{shadowCohorts.map(c=><option key={c.cohort_id} value={c.cohort_id}>{c.contract.source} · perfil {c.contract.profile_version_id.slice(0,8)} · {c.baseline.known} conhecidos/{c.baseline.observations}</option>)}</select></label>}
    {selectedShadow&&<><p>Base deste contrato: {selectedShadow.baseline.sl_hits} SLs verificados/{selectedShadow.baseline.known} desfechos conhecidos; {selectedShadow.baseline.unknown} indeterminados e {selectedShadow.baseline.pending} abertos. Cobertura de desfechos {selectedShadow.baseline.known}/{selectedShadow.baseline.observations}. Sem ranking ou recomendação de ajuste.</p>
     <p>Limitações dos desfechos: {Object.entries(selectedShadow.baseline.unknown_reasons).map(([reason,count])=>`${unknownReasons[reason]??reason}: ${count}`).join(" · ")||"nenhuma nesta amostra"}.</p>
     <p>Período de entrada: {executiveTime(selectedShadow.baseline.from)} → {executiveTime(selectedShadow.baseline.to)}. {selectedShadow.baseline.events} eventos identificados; {selectedShadow.baseline.events_missing} registros sem identidade de evento.</p>
     {selectedShadow.hours.map(({hour,support:s})=><p key={hour}>Entrada {String(hour).padStart(2,"0")}:00-{String(hour).padStart(2,"0")}:59 GMT−3: {s.sl_hits}/{s.known} SLs/conhecidos; {s.unknown} indeterminados; exposição {s.observations}, {s.events} eventos. Base: {selectedShadow.baseline.sl_hits}/{selectedShadow.baseline.known}. Motivos de barreira verificados: {Object.entries(s.reasons).map(([reason,count])=>`${reason}: ${count}`).join(", ")||"sem desfecho verificável"}.</p>)}
     <pre>{JSON.stringify(selectedShadow.contract,null,2)}</pre><p>Toques no mesmo candle usam a convenção conservadora registrada; não revelam a ordem intrabar. Um SL simulado não é execução real.</p></>}
   </>}
  </details>
 </section>;
}
