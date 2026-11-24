import { Activity, CheckCircle2 } from 'lucide-react';
import { diagnosticValue } from './MTFTriggerDiagnostics';
import { patternLabels, type Analysis, type TriggerCheck } from './types';
import { remainingToTrigger } from '@/lib/mtfTriggerReadiness';

function Condition({ row }: { row: TriggerCheck }) {
  const gap = remainingToTrigger(row);
  return <div className={`mtf-readiness-row ${row.state}`}>
    <div><strong>{row.label}</strong><small>{row.timeframe ? `${row.timeframe.toUpperCase()} · ` : ''}{{ required: 'Obrigatória', veto: 'Veto', score: 'Pontuação', context: 'Condição do grupo' }[row.role]}</small></div>
    <dl key={JSON.stringify([row.actual, row.expected, row.state])} className="mtf-readiness-values">
      <div><dt>Atual</dt><dd>{diagnosticValue(row.actual, row.unit)}</dd></div>
      <div><dt>Alvo</dt><dd>{row.operator} {Array.isArray(row.expected) ? row.expected.map(n => diagnosticValue(n, row.unit)).join(' a ') : diagnosticValue(row.expected, row.unit)}</dd></div>
      <div><dt>O que falta</dt><dd>{gap.action}{gap.amount != null ? ` ${diagnosticValue(gap.amount, row.unit)}` : ''}</dd></div>
    </dl>
    {row.note && row.note !== gap.action && <p>{row.note}</p>}
  </div>;
}

export function MTFTriggerReadiness({ analysis, current, date }: { analysis: Analysis; current: boolean; date: (timestamp: number) => string }) {
  const diagnostic = analysis.trigger_diagnostics;
  if (!current || !diagnostic || diagnostic.status !== analysis.status || diagnostic.config_hash !== analysis.config_hash)
    return <div className="mtf-readiness" role="status">Aguardando avaliação atual e coerente para mostrar o que falta. Valores antigos não indicam progresso atual.</div>;
  const gates = diagnostic.checks.filter(row => row.role === 'required' || row.role === 'veto');
  const pending = gates.filter(row => !['PASS', 'DISABLED'].includes(row.state));
  const blockers = pending.filter(row => row.state !== 'WAITING');
  const waiting = pending.filter(row => row.state === 'WAITING');
  const confirmed = analysis.status === 'TRIGGERED';
  return <section className={`mtf-readiness ${confirmed ? 'confirmed' : 'tracking'}`} aria-label="Caminho até o gatilho">
    <div className="mtf-readiness-heading"><strong>{confirmed ? <CheckCircle2 size={16}/> : <Activity size={16}/>} {confirmed ? 'Gatilho confirmado' : 'Caminho até o gatilho'}</strong><span role="status">{confirmed ? 'Condições confirmadas pelo motor' : `${blockers.length} pendentes · ${waiting.length} aguardando etapa anterior`}</span></div>
    <p>Atualização automática · avaliação {date(Date.parse(diagnostic.observed_at_utc ?? analysis.observed_at_utc)/1000)} · candle fechado {diagnostic.candle_close_at_utc ? date(Date.parse(diagnostic.candle_close_at_utc)/1000) : 'indisponível'}.</p>
    <p>Valores da última avaliação confirmada. O candle em formação não antecipa o gatilho; as diferenças abaixo não são previsão de preço ou prazo.</p>
    {!confirmed && blockers.map(row => <Condition key={row.id} row={row}/>)}
    {!confirmed && !pending.length && <p>Aguardando confirmação do estado pelo motor.</p>}
    {!confirmed && analysis.rules_diagnostics && <div className="mtf-readiness-group"><h4>Indicadores e condições JSON</h4><p>O grupo obrigatório acima decide o resultado. Em grupos OU, basta uma alternativa; itens de contexto não são vetos isolados.</p>{diagnostic.checks.filter(row => row.role === 'context').map(row => <Condition key={row.id} row={row}/>)}</div>}
    {!confirmed && !analysis.rules_diagnostics && <div className="mtf-readiness-group"><h4>Padrões de gatilho · basta um</h4><p>Cada alternativa exige suas condições em conjunto. Não é necessário completar todos os padrões.</p>{diagnostic.patterns.map(pattern => <div key={pattern.pattern}><h4>{patternLabels[pattern.pattern] ?? pattern.pattern} · {pattern.state === 'PASS' ? 'Atendido' : 'Pendente'}</h4>{pattern.conditions.filter(row => !['PASS','DISABLED'].includes(row.state)).map(row => <Condition key={row.id} row={row}/>)}</div>)}</div>}
    {waiting.length > 0 && <details><summary>Verificações após a etapa anterior · {waiting.length}</summary>{waiting.map(row => <Condition key={row.id} row={row}/>)}</details>}
    <details><summary>Condições atendidas e filtros desativados</summary>{gates.filter(row => ['PASS','DISABLED'].includes(row.state)).map(row => <Condition key={row.id} row={row}/>)}</details>
    <details><summary>Pontuação · contribuições ao score</summary><p>Estes itens somam pontos; não são exigências individuais. O score mínimo aparece entre as condições obrigatórias.</p>{diagnostic.checks.filter(row => row.role === 'score').map(row => <Condition key={row.id} row={row}/>)}</details>
  </section>;
}
