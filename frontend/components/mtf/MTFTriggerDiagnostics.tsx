import { CheckCircle2, CircleAlert, CirclePause } from 'lucide-react';
import { patternLabels, statusLabels, type Analysis, type DiagnosticState, type TriggerCheck } from './types';

const states: Record<DiagnosticState, string> = {
  PASS: 'Atendido', FAIL: 'Não atendido', DISABLED: 'Desativado',
  UNAVAILABLE: 'Indisponível', WAITING: 'Aguardando',
};
const values: Record<string, string> = { UP: 'Alta', DOWN: 'Baixa', RANGE: 'Lateral' };
const units: Record<string, string> = { price: '', seconds: 's', points: 'pts', ratio: '×', ATR: 'ATR', '%': '%' };

export function diagnosticValue(value: TriggerCheck['actual'], unit: string): string {
  if (value == null || (typeof value === 'number' && !Number.isFinite(value))) return 'Não disponível';
  if (typeof value === 'boolean') return value ? 'Sim' : 'Não';
  if (typeof value === 'string') return values[value] ?? value;
  const text = value.toLocaleString('pt-BR', { maximumSignificantDigits: 10 });
  return `${text}${units[unit] ? ` ${units[unit]}` : ''}`;
}

function State({state}: {state: DiagnosticState}) {
  const Icon = state === 'PASS' ? CheckCircle2 : state === 'FAIL' || state === 'UNAVAILABLE' ? CircleAlert : CirclePause;
  return <span className={`mtf-check-state ${state}`}><Icon size={12} aria-hidden="true"/>{states[state]}</span>;
}

function Check({row}: {row: TriggerCheck}) {
  const unmet = row.state === 'FAIL';
  return <div className="mtf-check" data-check={row.id}>
    <div className="mtf-check-heading"><strong>{row.label}{row.timeframe && <small> · {row.timeframe.toUpperCase()}</small>}</strong><State state={row.state}/></div>
    <dl className="mtf-check-values">
      <div><dt>Observado</dt><dd>{diagnosticValue(row.actual, row.unit)}</dd></div>
      <div><dt>Exigido</dt><dd>{row.operator} {Array.isArray(row.expected) ? row.expected.join(' … ') : diagnosticValue(row.expected, row.unit)}</dd></div>
    </dl>
    {unmet && row.gap != null && <p className="mtf-check-gap">{row.gap > 0
      ? `${['>', '>='].includes(row.operator) ? 'Abaixo do mínimo' : 'Acima do máximo'}: ${diagnosticValue(row.gap, row.unit)}.`
      : 'A igualdade não atende esta comparação estrita.'}</p>}
    {unmet && row.gap == null && <p className="mtf-check-gap">O estado observado não atende à condição exigida.</p>}
    {row.note && <p className="mtf-muted">{row.note}</p>}
    {row.role === 'score' && <p className="mtf-muted">Pontos: {diagnosticValue(row.points ?? null, 'points')} de {diagnosticValue(row.maximum_points ?? null, 'points')} · apenas pontuação</p>}
  </div>;
}

export function MTFTriggerDiagnostics({analysis, symbol, date, pending}: {
  analysis?: Analysis; symbol: string; date: (value?: string | null) => string; pending?: string;
}) {
  const diagnostic = analysis?.trigger_diagnostics;
  const coherent = diagnostic?.status === analysis?.status && diagnostic?.config_hash === analysis?.config_hash;
  const required = diagnostic?.checks.filter(c => c.role !== 'score' && c.role !== 'context') ?? [];
  const unresolved = required.filter(c => ['FAIL', 'UNAVAILABLE'].includes(c.state));
  const waiting = required.filter(c => c.state === 'WAITING');
  const complete = required.filter(c => ['PASS', 'DISABLED'].includes(c.state));
  const score = diagnostic?.checks.filter(c => c.role === 'score') ?? [];
  return <section className="mtf-card mtf-trigger-diagnostics" aria-label="O que falta para o gatilho">
    <div className="mtf-card-title"><strong>O que falta para o GATILHO</strong></div>
    <div className="mtf-panel-body">
      <p className="mtf-diagnostic-symbol">{symbol.replace('_', '/')}{analysis && <span>{statusLabels[analysis.status]}</span>}</p>
      {analysis?.risk_audit && <p role="status">{analysis.risk_audit.status === 'BLOCK' ? 'Gatilho bloqueado pela auditoria de contexto e risco.' : analysis.risk_audit.status === 'PASS' ? 'Auditoria de contexto e risco atendida.' : 'Auditoria aguardando plano confirmado.'} Score não compensa reprovações obrigatórias.</p>}
      {!analysis || !diagnostic || !coherent ? <p className="mtf-muted" role="status">{pending ?? (!analysis ? 'Aguardando avaliação confirmada deste ativo.' : 'Detalhamento indisponível nesta observação.')}</p> : <>
        <p className="mtf-muted">Candle fechado: {date(diagnostic.candle_close_at_utc)}<br/>Avaliação: {date(diagnostic.observed_at_utc)}</p>
        <p className="mtf-diagnostic-summary">{analysis.status === 'TRIGGERED' ? 'Condições do gatilho atendidas nesta avaliação.' : 'Condições a resolver na avaliação confirmada:'}</p>
        {unresolved.map(row => <Check key={row.id} row={row}/>)}
        <details className="mtf-diagnostic-group" open={!analysis.trigger_5m}>
          <summary>{analysis.rules_diagnostics ? 'Padrões de candles · contexto das regras' : 'Padrões de compra · basta um'}</summary>
          <p className="mtf-muted">Alternativas habilitadas. As características de cada padrão precisam ser atendidas em conjunto no candle fechado.</p>
          {diagnostic.patterns.length ? diagnostic.patterns.map(p => <details className="mtf-pattern-check" key={p.pattern}>
            <summary><span>{patternLabels[p.pattern] ?? p.pattern}</span><State state={p.state}/></summary>
            {p.conditions.filter(c => c.state !== 'PASS').map(c => <Check key={c.id} row={c}/>)}
            <details className="mtf-diagnostic-group"><summary>Características atendidas</summary>{p.conditions.filter(c => c.state === 'PASS').map(c => <Check key={c.id} row={c}/>)}</details>
          </details>) : <p className="mtf-muted">Aguardando candles e ATR válidos.</p>}
        </details>
        {waiting.length > 0 && <details className="mtf-diagnostic-group"><summary>Aguardando condições anteriores</summary>{waiting.map(row => <Check key={row.id} row={row}/>)}</details>}
        <details className="mtf-diagnostic-group"><summary>Condições atendidas e filtros desativados</summary>{complete.map(row => <Check key={row.id} row={row}/>)}</details>
        {analysis.rules_diagnostics && <details className="mtf-diagnostic-group"><summary>Condições JSON · resultados individuais</summary><p className="mtf-muted">O resultado obrigatório é o do grupo E/OU acima. Uma alternativa falsa pode ser compensada por outra verdadeira em um grupo OU.</p>{diagnostic.checks.filter(c=>c.role==='context').map(row=><Check key={row.id} row={row}/>)}</details>}
        <details className="mtf-diagnostic-group"><summary>Componentes do score</summary><p className="mtf-muted">Estes itens somam pontos. O requisito obrigatório é atingir o score mínimo; cada item isolado não é um veto.</p>{score.map(row => <Check key={row.id} row={row}/>)}</details>
      </>}
    </div>
  </section>;
}
