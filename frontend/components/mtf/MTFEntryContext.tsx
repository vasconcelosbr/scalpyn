import { reasonLabel, type Analysis, type Presentation } from './types';

const directions: Record<string, string> = { UP: 'Alta', DOWN: 'Baixa', RANGE: 'Lateral' };
const entries = { NO_TRADE: 'SEM TRADE', WAIT: 'AGUARDANDO', ARMED: 'ARMADO · aguardando gatilho', TRIGGERED: 'GATILHO CONFIRMADO' };

// MTF_ENTRY_CONTEXT: structural direction is independent of entry eligibility.
export function MTFEntryContext({ analysis, presentation }: { analysis?: Analysis; presentation?: Presentation }) {
  const diagnostic = analysis?.trigger_diagnostics;
  const coherent = diagnostic?.status === analysis?.status && diagnostic?.config_hash === analysis?.config_hash;
  const unmet = coherent ? diagnostic?.checks.filter(c => c.role === 'required' && ['FAIL', 'UNAVAILABLE'].includes(c.state)) ?? [] : [];
  return <div role="group" aria-label="Viés estrutural e situação da entrada">
    <div className="mtf-data-row"><span>Viés estrutural · 4H</span><b className={analysis?.context_4h.trend === 'UP' ? 'positive' : analysis?.context_4h.trend === 'DOWN' ? 'negative' : ''}>{directions[analysis?.context_4h.trend ?? ''] ?? 'Indisponível'}</b></div>
    <div className="mtf-data-row"><span>Entrada simulada</span><b className={analysis?.status === 'TRIGGERED' ? 'positive' : ''}>{analysis ? entries[analysis.status] : 'Indisponível'}</b></div>
    {!analysis ? <p className="mtf-muted">Aguardando avaliação atual deste ativo.</p> : <>
      {analysis.vetoes.map(v => <p key={v} className="mtf-warning">Veto: {reasonLabel(v, presentation)}.</p>)}
      {unmet.map(c => <p key={c.id} className="mtf-muted">{c.label}{c.timeframe ? ` · ${c.timeframe.toUpperCase()}` : ''}: {c.state === 'UNAVAILABLE' ? 'indisponível' : 'não atendido'}.</p>)}
      {analysis.status === 'TRIGGERED' && <p className="mtf-muted">Gatilho confirmado no candle fechado para simulação. Não representa uma ordem executada.</p>}
      <p className="mtf-muted">O viés descreve a estrutura do ativo. A entrada depende do gatilho, do score e dos vetos da avaliação.</p>
    </>}
  </div>;
}
