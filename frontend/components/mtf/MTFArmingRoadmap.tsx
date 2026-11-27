import { useEffect, useState } from 'react';
import { armingRoadmap, structuralReferences } from '../../lib/mtfArmingRoadmap';
import { CheckDetails, checkSummary, conditionText, diagnosticValue } from './MTFCheckDetails';
import { diagnosticTitle, statusLabels, type Analysis, type TriggerCheck } from './types';

export function MTFArmingRoadmap({analysis,symbol,date,precision,fromWatchlist=false}: {
  analysis?:Analysis; symbol:string; date:(value?:string|null)=>string; precision?:number; fromWatchlist?:boolean;
}) {
  const [now,setNow] = useState(() => Date.now());
  useEffect(() => {
    const expiry = Date.parse(analysis?.entry_candidate?.valid_until ?? '');
    if (!Number.isFinite(expiry)) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0,expiry-Date.now()));
    return () => clearTimeout(timer);
  },[analysis]);
  const view = armingRoadmap(analysis,symbol,Math.max(now,Date.now()));
  const price = (value:number) => value.toLocaleString('pt-BR',
    precision != null && Number.isInteger(precision) && precision >= 0 && precision <= 20
      ? {maximumFractionDigits:precision} : {maximumSignificantDigits:16});
  const render = (row:TriggerCheck) => {
    const refs = structuralReferences(row);
    const tf = row.timeframe ?? row.explanation?.operational_timeframe;
    const right = row.explanation?.parameters.pivot_right;
    return <article className="mtf-arm-condition" key={row.id} data-arming-check={row.id}>
      <header><strong>{diagnosticTitle(row)}</strong><span>{tf?.toUpperCase() ?? row.explanation?.timeframes.join(' / ').toUpperCase() ?? 'Timeframe não disponível'}</span></header>
      <p className="mtf-arm-reason">{row.state === 'UNAVAILABLE' ? 'Dados indisponíveis' : row.state === 'WAITING' ? 'Aguardando confirmação' : 'Condição não atendida'} · Observado: {diagnosticValue(row.actual,row.unit)}. Exigido: {conditionText(row)}.</p>
      {refs.length ? <>
        {refs.map(ref => <div className="mtf-arm-level" key={ref.kind}>
          <span>{ref.kind === 'H' ? 'Topos confirmados' : 'Fundos confirmados'}</span>
          <b>{price(ref.previous)} → {price(ref.latest)}</b>
          <p>{ref.ascending ? `Sequência ascendente atendida; precisa permanecer válida.` :
            `Falta confirmar novo ${ref.kind === 'H' ? 'topo' : 'fundo'} acima de ${price(ref.latest)}.`}</p>
        </div>)}
        <p className="mtf-muted">{typeof right === 'number' && Number.isInteger(right) && right > 0
          ? `O pivô precisa satisfazer a regra de extremos e aguardar ${right} candles fechados à direita em ${tf?.toUpperCase()}.`
          : 'A confirmação depende da regra de pivôs; janela de confirmação não disponível.'} Tocar ou romper o nível sozinho não confirma a estrutura.</p>
      </> : <p>{checkSummary(row)}{row.id.endsWith('_alignment') && ' Referência de preço não disponível nesta avaliação.'}</p>}
      {row.id.endsWith('_integrity') && <p>Retornar ao preço rompido não restaura a estrutura. O motor exige uma nova estrutura completa após a quebra.</p>}
      {row.note && <p className="mtf-muted">{row.note}</p>}
      <CheckDetails row={row} checks={analysis?.trigger_diagnostics?.checks ?? []} date={date} context={analysis?.trigger_diagnostics?.explanation_context}/>
    </article>;
  };
  return <section className="mtf-card mtf-arming-roadmap" aria-label="O que falta para armar">
    <div className="mtf-card-title"><strong>O que falta para ARMAR</strong></div>
    <div className="mtf-panel-body">
      <p className="mtf-diagnostic-symbol">{symbol.replace('_','/')}<span>{view && analysis ? statusLabels[analysis.status] : 'AGUARDANDO DADOS'}</span></p>
      {!view || !analysis ? <p role="status" className="mtf-muted">Aguardando avaliação atual deste ativo. Referências vencidas ou de outra configuração não são usadas.</p> : <>
        <p className="mtf-muted">Avaliação confirmada: {date(analysis.candle_close_at_utc)}. Referências recalculadas quando novos pivôs são confirmados.</p>
        {fromWatchlist && <p className="mtf-muted">Fonte: avaliação registrada da watchlist. O carregamento dos candles é independente deste diagnóstico.</p>}
        <p className="mtf-arm-summary">{view.achieved ? 'O ativo já atingiu ARMADO nesta avaliação.' :
          view.armedRule?.state === 'PASS' ? 'Regra ARMADO atendida; o estado final ainda depende dos bloqueios abaixo.' : 'Confirmações pendentes para armar e bloqueios da avaliação:'}</p>
        {view.band && view.band.state !== 'UNAVAILABLE' ? <div className="mtf-arm-band">
          <header><strong>Faixa candidata de proximidade</strong><span>{view.band.timeframe.toUpperCase()}</span></header>
          <b>{price(view.band.low!)} – {price(view.band.high!)}</b>
          <p>Suporte: {view.band.support_low == null ? 'Não disponível' : price(view.band.support_low)} – {view.band.support_high == null ? 'Não disponível' : price(view.band.support_high)}.</p>
          <p className="mtf-muted">Válida até {date(view.band.valid_until)}. É uma referência da zona; as regras ARMADO e os bloqueios continuam obrigatórios.</p>
        </div> : <p className="mtf-muted">Faixa de preço atual não disponível.</p>}
        {view.arming.map(render)}
        {view.arming.length === 0 && <p className="mtf-muted">Nenhuma pendência registrada nas regras de armar e alinhamento estrutural.</p>}
        {view.remaining.length > 0 && <details className="mtf-diagnostic-group" open={!view.achieved}>
          <summary>Outras condições pendentes · dados, vetos e confirmação da entrada</summary>
          <p className="mtf-muted">Estes requisitos preservam o estágio informado pelo motor. Confirmações de gatilho podem permanecer pendentes quando o ativo já está armado.</p>
          {view.remaining.map(render)}
        </details>}
        <p className="mtf-muted">Não existe um preço isolado que garanta ARMADO. A direção, os dados e as regras habilitadas precisam ser reavaliados no fechamento.</p>
      </>}
    </div>
  </section>;
}
