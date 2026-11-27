import type { Analysis, TriggerCheck } from '../components/mtf/types';
import { entryBandView } from './mtfEntryBand';

// Presentation of frozen engine evidence only; never evaluates or authorizes a signal.
export function armingRoadmap(analysis: Analysis | undefined, symbol: string, now: number) {
  const diagnostic = analysis?.trigger_diagnostics;
  const expiry = Date.parse(analysis?.entry_candidate?.valid_until ?? '');
  const expired = analysis?.entry_candidate && (!Number.isFinite(expiry) || now >= expiry);
  if (!analysis || analysis.symbol !== symbol || !diagnostic || expired ||
      diagnostic.status !== analysis.status || diagnostic.config_hash !== analysis.config_hash ||
      diagnostic.checks.some(c => c.id === 'evaluation_current' && c.state !== 'PASS')) return null;
  const checks = diagnostic.checks.filter(c => c.role !== 'context' && c.role !== 'score');
  const pending = checks.filter(c => ['FAIL', 'UNAVAILABLE', 'WAITING'].includes(c.state));
  const armingIds = new Set(['rules:ARMADO', 'audit:structure_alignment', 'audit:zone_alignment',
    'audit:structure_integrity', 'audit:zone_integrity']);
  const arming = pending.filter(c => armingIds.has(c.id));
  const remaining = pending.filter(c => !armingIds.has(c.id));
  return { arming, remaining, checks, band: entryBandView(analysis, true, now),
    armedRule: checks.find(c => c.id === 'rules:ARMADO'),
    // Canonical group stays intact: a false OR alternative is not an extra veto.
    achieved: analysis.status === 'ARMED' || analysis.status === 'TRIGGERED' };
}

export interface StructuralReference { kind: 'H' | 'L'; previous: number; latest: number; ascending: boolean }
export function structuralReferences(row: TriggerCheck): StructuralReference[] {
  if (!['audit:structure_alignment', 'audit:zone_alignment'].includes(row.id) ||
      !['PASS', 'FAIL'].includes(row.state) || row.expected !== 'UP') return [];
  const evidence = row.explanation?.evidence;
  if (evidence?.complete !== true || !evidence.pivots || !Number.isFinite(evidence.closed_at)) return [];
  const points = evidence.pivots.filter(p => Number.isFinite(p.price) && p.price > 0 &&
    Number.isFinite(p.time) && Number.isFinite(p.confirmed_at) && p.confirmed_at <= evidence.closed_at!)
    .sort((a,b) => a.confirmed_at - b.confirmed_at || a.time - b.time);
  const pairs = (['H', 'L'] as const).map(kind => ({ kind, pivots: points.filter(p => p.kind === kind).slice(-2) }));
  if (pairs.some(p => p.pivots.length !== 2)) return [];
  return pairs.map(({kind,pivots}) => ({kind,previous:pivots[0].price,latest:pivots[1].price,
    ascending:pivots[1].price > pivots[0].price}));
}
