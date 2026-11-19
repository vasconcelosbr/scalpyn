'use client';
import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import type { FlowComparison, PumpFlowContext, Timeframe } from './types';
import { number } from './types';

export const flowLabels: Record<string,string> = {inflow:'Entrada',outflow:'Saída',neutral:'Neutro',unavailable:'Indisponível',confirmed:'Confirmado',not_confirmed:'Não confirmado',divergent:'Vendedor',disabled:'Desativado',baseline:'MTF original'};
const reasons: Record<string,string> = {asset_not_in_pump_universe:'Ativo fora do universo Pump',no_trades:'Sem negócios na janela',incomplete_or_delayed_window:'Janela incompleta ou atrasada',pump_snapshot_stale_or_missing:'Snapshot Pump ausente ou vencido',source_unavailable:'Fonte temporariamente indisponível',evaluation_outside_flow_freshness:'Avaliação fora da validade configurada'};
const pct = (v?: number | null) => v == null ? '—' : `${number(v*100,2)}%`;

export function MTFPumpFlow({context, watchlist, pollSeconds, timezone, stale}: {context?:PumpFlowContext;watchlist:string;pollSeconds:number;timezone:string;stale:boolean}) {
  const [report,setReport]=useState<FlowComparison|null>(null);
  const [error,setError]=useState('');
  const enabled = !!context && context.confirmation!=='disabled';
  useEffect(()=>{
    setReport(null);setError('');
    if(!enabled || !watchlist) return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const load=async()=>{
      try {const r=await apiFetch<FlowComparison>(`/mtf/pump-flow/report?watchlist_id=${encodeURIComponent(watchlist)}`,{signal:controller.signal});if(!controller.signal.aborted){setReport(r);setError('');}}
      catch {if(!controller.signal.aborted)setError('Comparação temporariamente indisponível.');}
      finally {if(!controller.signal.aborted)timer=setTimeout(load,pollSeconds*1000);}
    };
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[enabled,watchlist,pollSeconds,context?.config_hash]);
  const groups=report?.groups.filter(g=>g.flow_config_hash===context?.config_hash)??[];
  const date=(value?:string)=>value?new Date(value).toLocaleString('pt-BR',{timeZone:timezone}):'—';
  return <section className="mtf-card mtf-flow" aria-label="Capital e fluxo Pump">
    <div className="mtf-card-title"><strong>Capital e fluxo</strong><span>SHADOW</span></div>
    <div className="mtf-panel-body">
      <p className="mtf-muted">Contexto do Pump. Score e vetos MTF preservados.</p>
      {!enabled?<p className="mtf-reading">Ative em Configurar análise MTF → Capital e fluxo.</p>:<>
        {stale&&<p className="mtf-warning" role="status">Leitura anterior: aguardando atualização do gráfico.</p>}
        <div className="mtf-flow-windows">{(['4h','1h','5m'] as Timeframe[]).map(tf=>{
          const r=context.capital[tf];return <div key={tf} className={`mtf-flow-window ${r?.state==='inflow'?'positive':r?.state==='outflow'?'negative':''}`}>
            <small>CAPITAL {tf.toUpperCase()}</small><strong>{flowLabels[r?.state??'unavailable']}</strong>
            <b>{r?.state==='unavailable'?'—':pct(r?.ratio)}</b><span>{r?.state==='unavailable'?'—':`${number(r?.net_usdt,2)} USDT`}</span>
            <small>Cobertura {pct(r?.coverage)}</small>{r?.reason&&<small>{reasons[r.reason]??r.reason}</small>}
          </div>;
        })}</div>
        <div className="mtf-data-row"><span>Fluxo do ativo · 5m</span><b>{flowLabels[context.asset.state??'unavailable']}</b></div>
        <div className="mtf-data-row"><span>Delta normalizado</span><b>{context.asset.state==='unavailable'?'—':number(context.asset.ratio,4)}</b></div>
        <div className="mtf-data-row"><span>Persistência compradora</span><b>{context.asset.state==='unavailable'?'—':pct(context.asset.buy_persistence)}</b></div>
        <div className="mtf-data-row"><span>Confirmação para comparação</span><b>{flowLabels[context.confirmation]}</b></div>
        {context.asset.reason&&<p className="mtf-muted">{reasons[context.asset.reason]??context.asset.reason}</p>}
        {context.reasons?.filter(r=>reasons[r]).map(r=><p className="mtf-muted" key={r}>{reasons[r]}</p>)}
        <p className="mtf-muted">Janelas até {date(context.cutoff)}. Capturado em {date(context.captured_at)}. Saldo de agressão; não mede depósitos na corretora.</p>
        <details className="mtf-flow-comparison"><summary>Comparação Shadow · mesma execução</summary>
          <p className="mtf-muted">Confirmados são um subconjunto do MTF original. Resultados líquidos dos custos congelados; não são carteiras independentes nem prova de melhora.</p>
          {error&&<p role="status" className="mtf-muted">{error}</p>}
          {!groups.length?<p className="mtf-reading">Coletando novas simulações para esta configuração. O histórico anterior não é preenchido retroativamente.</p>:groups.map(g=><div className="mtf-flow-result" key={`${g.mtf_config_hash}:${g.cohort}`}>
            <strong>{flowLabels[g.cohort]??g.cohort}</strong><span>{g.closed} encerradas / {g.observations} observações</span>
            <span>Média líquida {g.mean_net_pct==null?'—':`${number(g.mean_net_pct,3)}%`}</span>
            <small>{g.closed<(report?.minimum_sample??Infinity)?'Amostra insuficiente. ':'Descritivo; sem teste de significância. '}{g.ambiguous} ambíguas. Versão MTF {g.mtf_config_hash.slice(0,8)}</small>
          </div>)}
        </details>
      </>}
    </div>
  </section>;
}
