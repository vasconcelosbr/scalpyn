import type { ChartData, FormingPreview, Candle } from '../components/mtf/types';

export interface PreviewEvent {
  type: 'preview'; symbol: string; config_hash: string; timeframe: string;
  profile_id?: string | null; profile_hash?: string | null; indicator_hash?: string | null;
  candle: Candle; preview: FormingPreview;
  candles?: Record<string, Candle>;
}

// The live channel cannot replace analysis, status, plans or audio inputs.
export function applyLivePreview(data: ChartData | null, event: PreviewEvent): ChartData | null {
  if (!data || event.symbol !== data.symbol || event.config_hash !== data.analysis.config_hash
      || event.preview.symbol !== data.symbol || event.preview.config_hash !== data.analysis.config_hash
      || event.preview.confirmation !== 'CLOSE_ONLY' || event.preview.operational_effect !== false
      || (event.profile_id ?? null) !== (data.analysis.profile_id ?? null)
      || (event.profile_hash ?? null) !== (data.analysis.profile_hash ?? null)
      || (event.indicator_hash ?? null) !== (data.analysis.indicator_identity?.config_hash ?? null)
      || event.preview.candle_open_at_utc !== data.forming_preview?.candle_open_at_utc
      || !data.frames[event.timeframe] || event.candle.closed !== false) return data;
  const incoming = Date.parse(event.preview.observed_at_utc);
  if (!Number.isFinite(incoming) || incoming < Date.parse(data.forming_preview?.observed_at_utc ?? '')) return data;
  const frame = data.frames[event.timeframe];
  if (event.candle.time !== frame.candles.at(-1)?.time) return data;
  const frames = {...data.frames};
  for (const [tf, candle] of Object.entries(event.candles ?? {[event.timeframe]:event.candle})) {
    const current = frames[tf];
    if (current && candle.closed === false && candle.time === current.candles.at(-1)?.time)
      frames[tf] = {...current, candles:[...current.candles.slice(0,-1),candle]};
  }
  return {...data, forming_preview: event.preview, frames};
}

export function retainLivePreview(previous: ChartData | null, next: ChartData): ChartData {
  if (!previous || previous.symbol !== next.symbol || previous.analysis.config_hash !== next.analysis.config_hash
      || previous.analysis.profile_hash !== next.analysis.profile_hash
      || previous.analysis.indicator_identity?.config_hash !== next.analysis.indicator_identity?.config_hash
      || previous.forming_preview?.delivery !== 'stream' || previous.forming_preview.state !== 'AVAILABLE'
      || Date.parse(previous.forming_preview.valid_until) <= Date.now()
      || previous.forming_preview.candle_open_at_utc !== next.forming_preview?.candle_open_at_utc) return next;
  const frames = {...next.frames};
  for (const [tf, frame] of Object.entries(frames)) {
    const live = previous.frames[tf]?.candles.at(-1);
    if (live?.closed === false && live.time === frame.candles.at(-1)?.time)
      frames[tf] = {...frame, candles: [...frame.candles.slice(0, -1), live]};
  }
  return {...next, frames, forming_preview: previous.forming_preview};
}
