import type { Analysis, EntryCandidate } from '../components/mtf/types';

export const entryBandLabels = { WAITING: 'Aguardando confirmação', BLOCKED: 'Bloqueada', CONFIRMED: 'Gatilho confirmado · Shadow', UNAVAILABLE: 'Indisponível' };
export const entryBandColors = { WAITING: '#d4a64f', BLOCKED: '#fb7185', CONFIRMED: '#2dd4a0', UNAVAILABLE: '#8c9ab0' };
export function entryBandView(analysis: Analysis, current: boolean, now: number): EntryCandidate | null {
  const band = analysis.entry_candidate;
  if (!band) return null;
  const unavailable = (reason: string): EntryCandidate => ({...band, state:'UNAVAILABLE', reason, low:null, high:null, entry:null});
  if (!current || band.config_hash !== analysis.config_hash || band.evaluated_at !== analysis.evaluated_at_utc)
    return unavailable('STALE_CONTEXT');
  const expiry = Date.parse(band.valid_until);
  if (!Number.isFinite(expiry) || now >= expiry) return unavailable('EXPIRED');
  if (band.state === 'UNAVAILABLE') return unavailable(band.reason ?? 'NO_DATA');
  if (band.low == null || band.high == null || !Number.isFinite(band.low) || !Number.isFinite(band.high) || band.low <= 0 || band.low > band.high)
    return unavailable('INVALID_SUPPORT');
  if (band.state === 'CONFIRMED' && (analysis.status !== 'TRIGGERED' || analysis.vetoes.length || !analysis.plan || band.entry !== analysis.plan.entry))
    return unavailable('INCONSISTENT_EVALUATION');
  return band;
}
