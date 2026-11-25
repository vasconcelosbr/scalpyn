'use client';

import { useEffect, useState } from 'react';
import { Activity, CheckCircle2 } from 'lucide-react';
import { diagnosticValue } from './MTFTriggerDiagnostics';
import { patternLabels, type Analysis, type FormingPreview, type TriggerCheck } from './types';
import { formingPreviewCurrent, remainingToTrigger } from '@/lib/mtfTriggerReadiness';

function Condition({ row }: { row: TriggerCheck }) {
  const gap = remainingToTrigger(row);
  return <div className={`mtf-readiness-row ${row.state}`} tabIndex={0} role="group" aria-label={row.label} title={row.note ?? undefined}>
    <strong>{row.label}{row.timeframe && <small> · {row.timeframe.toUpperCase()}</small>}</strong>
    <span key={JSON.stringify([row.actual, row.expected, row.state])} className="mtf-readiness-values">
      <span><b>Atual:</b> {diagnosticValue(row.actual, row.unit)}</span>
      <span><b>Alvo:</b> {row.operator} {Array.isArray(row.expected) ? row.expected.map(n => diagnosticValue(n, row.unit)).join(' a ') : diagnosticValue(row.expected, row.unit)}</span>
      <span><b>O que falta:</b> {gap.action}{gap.amount != null ? ` ${diagnosticValue(gap.amount, row.unit)}` : ''}</span>
    </span>
  </div>;
}

export function MTFTriggerReadiness({ analysis, preview, current, timezone = 'UTC' }: { analysis: Analysis; preview?: FormingPreview; current: boolean; timezone?: string }) {
  const [clock, setClock] = useState(Date.now);
  useEffect(() => {
    setClock(Date.now());
    const expires = Date.parse(preview?.valid_until ?? '');
    if (!Number.isFinite(expires) || expires <= Date.now()) return;
    const timer = setTimeout(() => setClock(Date.now()), expires - Date.now());
    return () => clearTimeout(timer);
  }, [preview?.valid_until]);
  const now = Math.max(clock, Date.now());
  const diagnostic = preview?.diagnostics;
  if (!current || !formingPreviewCurrent(preview, analysis, now) || !preview || !diagnostic)
    return <div className="mtf-readiness" role="status">Prévia do candle em formação indisponível ou vencida. Aguardando nova atualização; o sinal oficial continua dependendo do fechamento.</div>;
  const time = (value: string | null) => value ? new Date(value).toLocaleTimeString('pt-BR', {timeZone: timezone}) : 'indisponível';
  const gates = diagnostic.checks.filter(row => row.role === 'required' || row.role === 'veto');
  const pending = gates.filter(row => !['PASS', 'DISABLED'].includes(row.state));
  const blockers = pending.filter(row => row.state !== 'WAITING');
  const waiting = pending.filter(row => row.state === 'WAITING');
  const confirmed = analysis.status === 'TRIGGERED';
  return <section className={`mtf-readiness tracking`} aria-label="Caminho até o gatilho">
    <div className="mtf-readiness-heading"><strong>{confirmed ? <CheckCircle2 size={16}/> : <Activity size={16}/>} Prévia · candle em formação</strong><span role="status">{pending.length ? `${blockers.length} pendentes · ${waiting.length} aguardando` : 'Prévia atendida — aguardando fechamento'}</span></div>
    <p>Atualizado {time(preview.observed_at_utc)} · {preview.delivery === 'stream' ? 'Ao vivo · eventos da Gate.io' : 'Conectando ao vivo · consulta de recuperação'} · fechamento previsto {time(preview.candle_close_at_utc)}. {confirmed ? 'Gatilho oficial confirmado no candle anterior.' : 'Confirmação oficial somente no fechamento.'}</p>
    <p>Indicadores do candle aberto podem mudar. Pivôs e tendência do BTC permanecem confirmados; capital e fluxo mantêm o horário de sua fonte.</p>
    {blockers.map(row => <Condition key={row.id} row={row}/>)}

    {analysis.rules_diagnostics && <div className="mtf-readiness-group"><h4>Indicadores e condições JSON</h4><p>O grupo obrigatório acima decide o resultado. Em grupos OU, basta uma alternativa; itens de contexto não são vetos isolados.</p>{diagnostic.checks.filter(row => row.role === 'context').map(row => <Condition key={row.id} row={row}/>)}</div>}
    {!analysis.rules_diagnostics && <div className="mtf-readiness-group"><h4>Padrões de gatilho · basta um</h4><p>Cada alternativa exige suas condições em conjunto. Não é necessário completar todos os padrões.</p>{diagnostic.patterns.map(pattern => <div key={pattern.pattern}><h4>{patternLabels[pattern.pattern] ?? pattern.pattern} · {pattern.state === 'PASS' ? 'Atendido' : 'Pendente'}</h4>{pattern.conditions.filter(row => !['PASS','DISABLED'].includes(row.state)).map(row => <Condition key={row.id} row={row}/>)}</div>)}</div>}
    {waiting.length > 0 && <details><summary>Verificações após a etapa anterior · {waiting.length}</summary>{waiting.map(row => <Condition key={row.id} row={row}/>)}</details>}
    <details><summary>Condições atendidas e filtros desativados</summary>{gates.filter(row => ['PASS','DISABLED'].includes(row.state)).map(row => <Condition key={row.id} row={row}/>)}</details>
    <details><summary>Pontuação · contribuições ao score</summary><p>Estes itens somam pontos; não são exigências individuais. O score mínimo aparece entre as condições obrigatórias.</p>{diagnostic.checks.filter(row => row.role === 'score').map(row => <Condition key={row.id} row={row}/>)}</details>
  </section>;
}
