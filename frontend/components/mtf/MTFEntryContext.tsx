import { MTFStructurePanel } from './MTFStructurePanel';
import { reasonLabel, type Analysis, type Presentation, type StructureContext } from './types';

const entries = { NO_TRADE: 'SEM TRADE', WAIT: 'AGUARDANDO', ARMED: 'ARMADO · aguardando gatilho', TRIGGERED: 'GATILHO CONFIRMADO' };

// MTF_ENTRY_CONTEXT: structural direction is independent of entry eligibility.
export function MTFEntryContext({ analysis, presentation, structure, precision, timezone }: { analysis?: Analysis; presentation?: Presentation; structure?: StructureContext|null; precision?:number; timezone?:string }) {
  const diagnostic = analysis?.trigger_diagnostics;
  const coherent = diagnostic?.status === analysis?.status && diagnostic?.config_hash === analysis?.config_hash;
  const unmet = coherent ? diagnostic?.checks.filter(c => c.role === 'required' && ['FAIL', 'UNAVAILABLE'].includes(c.state)) ?? [] : [];
  return <div role="group" aria-label="Estrutura e situação da entrada">
    <MTFStructurePanel analysis={analysis} context={structure} precision={precision} timezone={timezone}/>
    <div className="mtf-data-row"><span>Entrada simulada</span><b className={analysis?.status === 'TRIGGERED' ? 'positive' : ''}>{analysis ? entries[analysis.status] : 'Indisponível'}</b></div>
    {!analysis ? <p className="mtf-muted">Aguardando avaliação atual deste ativo.</p> : <>
      {analysis.vetoes.map(v => <p key={v} className="mtf-warning">Veto: {reasonLabel(v, presentation)}.</p>)}
      {unmet.map(c => <p key={c.id} className="mtf-muted">{c.label}{c.timeframe ? ` · ${c.timeframe.toUpperCase()}` : ''}: {c.state === 'UNAVAILABLE' ? 'indisponível' : 'não atendido'}.</p>)}
      {analysis.status === 'TRIGGERED' && <p className="mtf-muted">Gatilho confirmado no candle fechado para simulação. Não representa uma ordem executada.</p>}
      <p className="mtf-muted">A estrutura descreve pivôs confirmados do ativo. A entrada depende do gatilho, do score e dos vetos da avaliação.</p>
    </>}
  </div>;
}
