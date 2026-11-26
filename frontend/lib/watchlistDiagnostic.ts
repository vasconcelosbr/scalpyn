import { formatDateTime } from './datetime';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export function diagnosticTime(value: string | null | undefined): string {
  if (!value || !Number.isFinite(new Date(value).getTime())) return 'Horário não disponível';
  return formatDateTime(value, { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', timeZoneName:'short' });
}
export type Diagnostic = {
  schema_version: string; captured_at: string; symbol: string;
  watchlist: { id: string; name: string; level: string; market_mode: string; profile_id: string | null; last_scanned_at: string | null };
  profile: { id: string; name: string; config: Json; updated_at: string | null } | null;
  configuration: Json[]; sources: Record<string, Json>; availability: Record<string, string>;
  scope: string; read_only: boolean; execution_authority: boolean;
};
export type Metric = { name: string; value: Json; timeframe: string; source: string; time: string | null; path: string };
export const object = (v: Json | undefined): Record<string, Json> =>
  v && typeof v === 'object' && !Array.isArray(v) ? v : {};
export const text = (v: Json | undefined): string => v == null ? 'Não disponível' : typeof v === 'object' ? JSON.stringify(v) : String(v);
export function diagnosticClassification(data: Diagnostic | null): string | null {
  if (!data) return null;
  const canonical = object(data.sources.profile_classification);
  if (typeof canonical.status === 'string') return canonical.status;
  const asset = object(data.sources.pipeline_asset);
  const snapshot = object(asset.analysis_snapshot);
  const rejections = data.sources.pipeline_rejections;
  if (Array.isArray(rejections) && rejections.length) {
    if (!data.sources.pipeline_asset) return 'rejected';
    const approvalTime = typeof asset.refreshed_at === 'string' ? Date.parse(asset.refreshed_at) : NaN;
    const rejectionTimes = rejections.map(r => object(r).recorded_at)
      .map(t => typeof t === 'string' ? Date.parse(t) : NaN);
    // Membership may retain an old approval. Never let it hide a newer rejection.
    if (!Number.isFinite(approvalTime) || rejectionTimes.some(t => !Number.isFinite(t))) return null;
    if (Math.max(...rejectionTimes) >= approvalTime) return 'rejected';
  }
  return typeof snapshot.status === 'string' ? snapshot.status : data.sources.pipeline_asset ? 'approved' : null;
}
export function diagnosticHref(watchlistId: string, symbol: string) {
  return `/watchlist/${encodeURIComponent(watchlistId)}/${encodeURIComponent(symbol)}`;
}
export function flatten(value: Json, prefix = ''): { name: string; value: Json }[] {
  if (value === null || typeof value !== 'object') return [{ name: prefix, value }];
  const entries = Object.entries(value);
  if (!entries.length) return [{ name: prefix, value }];
  return entries.flatMap(([key, v]) => flatten(v, prefix ? `${prefix}.${key}` : key));
}
export function diagnosticMetrics(data: Diagnostic): Metric[] {
  const rows: Metric[] = [];
  const producers = data.sources.indicators;
  if (Array.isArray(producers)) for (const [index, producer] of producers.entries()) {
    const p = object(producer);
    for (const entry of flatten(p.indicators_json ?? {})) rows.push({ ...entry,
      timeframe: text(p.timeframe), source: `Coletor · ${text(p.scheduler_group ?? 'combined')}`,
      time: typeof p.time === 'string' ? p.time : null,
      path: `sources.indicators.${index}.indicators_json.${entry.name}` });
  }
  for (const key of ['mtf_observation', 'consolidated_mtf_observation']) {
    const observation = object(data.sources[key]);
    const payload = object(observation.payload);
    if (Array.isArray(payload.indicators)) for (const [index, item] of payload.indicators.entries()) {
      const p = object(item);
      rows.push({ name: text(p.name), value: p.value ?? null, timeframe: text(p.timeframe),
        source: key === 'mtf_observation' ? 'Avaliação MTF da watchlist' : 'Avaliação MTF do consolidado',
        time: typeof p.source_at === 'string' ? p.source_at : null,
        path: `sources.${key}.payload.indicators.${index}` });
    }
  }
  return rows;
}
export function exportDiagnostic(data: Diagnostic) {
  return JSON.stringify(data, null, 2);
}

