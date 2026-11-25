export type Timeframe = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';
export type MTFStatus = 'NO_TRADE' | 'WAIT' | 'ARMED' | 'TRIGGERED';
export interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number; closed: boolean }
export interface Point { time: number; value: number }
export interface Pattern { time: number; pattern: string; metrics: Record<string, number> }
export interface Pivot { time: number; price: number; label: string; kind: string; confirmed_at: number }
export interface StructureFrame {
  trend: 'UP'|'DOWN'|'RANGE'|null; pivots: Pivot[]; last_hl: Pivot|null; last_lh: Pivot|null;
  last_pivot_age_candles: number|null; atr:number|null; ema50:number|null; closed_close:number|null;
  closed_at:string|null; extension_atr:number|null; live_candle:Candle|null; live_change_pct:number|null;
}
export interface StructureContext {
  stage_timeframes?: StageTimeframes; ema_period?: number;
  source:'chart'|'observation'; as_of:string; evaluated_at:string; valid_until:string; config_hash:string;
  profile_id:string|null; frames:Record<'4h'|'1h'|'5m',StructureFrame> & Partial<Record<Timeframe,StructureFrame>>; current_price:number|null;
  current_price_source:string|null; invalidation:Pivot|null; invalidation_distance_atr:number|null;
  conflict:boolean|null; conflicts:{kind:'structure'|'flow';timeframe:Timeframe;state:string;sequence:string[];ratio:number|null}[];
  operational_effect:false;
}
export interface Zone { type: string; low: number; high: number; touches: number; available_at: number; distance_atr?: number; in_zone?: boolean; rejection?: boolean }
export interface Frame { candles: Candle[]; indicators: Record<string, Point[]>; patterns: Pattern[]; pivots: Pivot[]; patterns_removed?: [number,string][]; pivots_removed?: [number,string][]; zones?: Zone[]; warmup?: {reason: string | null}; window_start?: number; error?: string | null; stale: boolean; source: string }
export interface Plan { side: string; entry_type: string; entry: number; stop: number; tp1: number; tp2: number; rr_net: number; rr_gross: number; quantity: number; notional_usdt: number; expires_at: string; expires_after_candles: number }
export type DiagnosticState = 'PASS' | 'FAIL' | 'DISABLED' | 'UNAVAILABLE' | 'WAITING';
export interface TriggerCheck {
  id: string; label: string; actual: number | string | boolean | null;
  operator: string; expected: number | string | boolean | number[] | null; unit: string;
  role: 'required' | 'veto' | 'score' | 'context'; timeframe: Timeframe | null;
  state: DiagnosticState; gap: number | null; note: string | null;
  points?: number; maximum_points?: number;
}
export interface TriggerDiagnostics {
  version: 'mtf_trigger_diagnostics_v1'; status: MTFStatus;
  candle_close_at_utc?: string | null; observed_at_utc?: string; config_hash?: string;
  checks: TriggerCheck[];
  patterns: { pattern: string; state: DiagnosticState; conditions: TriggerCheck[] }[];
}
export interface StageTimeframes { structure: Timeframe; zone: Timeframe; trigger: Timeframe }
export interface Analysis {
  stage_results?: {armed:boolean;trigger:boolean};
  stage_timeframes?: StageTimeframes;
  rules_diagnostics?: TriggerCheck[];
  btc_trend_timeframe?: Timeframe;
  btc_context?: {
    version: 'mtf_btc_context_v1'; active_timeframe: Timeframe; veto_enabled: boolean; exempt: boolean;
    frames: Record<string, {trend: 'UP' | 'DOWN' | 'RANGE' | null; reason: string | null;
      pivots_used: Pivot[]; reference_candle: {open_at: string; close_at: string; close: number} | null}>;
  };
  profile_id?: string | null;
  profile_hash?: string | null; indicator_identity?: {config_hash?: string};
  entry_candidate?: EntryCandidate | null;
  pump_flow?: PumpFlowContext;
  trigger_diagnostics?: TriggerDiagnostics;
  evaluation_id?: string; symbol: string; status: MTFStatus; observed_at_utc: string; evaluated_at_utc: string;
  candle_closed_5m: string | null; candle_close_at_utc?: string; config_hash: string; mode: 'SHADOW'; operational_effect: false;
  context_4h: { trend: string; last_pivots: string[]; strength: string; last_candle_pattern: string | null };
  zone_1h: Zone | null; zones: Zone[]; trigger_5m: (Pattern & { volume_ratio: number | null; type: string }) | null;
  plan: Plan | null; score: { total: number; raw?: number | null; maximum: number; components: Record<string, number>; calibrated: false };
  vetoes: string[]; explanation: string[]; unavailable_profile_indicators: string[]; spread_pct: number | null; btc_trend: string | null;
}
export interface FormingPreview {
  delivery?: 'stream'; source_event_at?: number;
  version: 'mtf_forming_preview_v1'; symbol: string; config_hash: string;
  state: 'AVAILABLE' | 'UNAVAILABLE'; reason: string | null; confirmation: 'CLOSE_ONLY'; operational_effect: false;
  observed_at_utc: string; valid_until: string; candle_open_at_utc: string | null; candle_close_at_utc: string | null;
  refresh_seconds: number; diagnostics: Pick<TriggerDiagnostics, 'checks' | 'patterns'> | null;
}
export interface ChartData { forming_preview?: FormingPreview; structure_context?: StructureContext | null; symbol: string; frames: Record<string, Frame>; analysis: Analysis; as_of: string; profile: Profile | null; indicator_config: Record<string, unknown>; cursor?: string; transport?: 'full'|'delta'; price_format?: {precision: number; min_move: number; source: string} | null }
export interface Profile { id: string; name: string; default_timeframe?: string }
export interface Watchlist { id: string; name: string; profile_id?: string }
export interface Asset { structure_context?: StructureContext | null; symbol: string; current_price?: number | null; price_snapshot_at?: string | null; alpha_score?: number | null; profile_id?: string; mtf?: Analysis | null }
export interface IndicatorInstance { family:string; output:string; parameters:Record<string,unknown>; timeframe:Timeframe; enabled:boolean; display:boolean; sample_window:number }
export interface RuleOperand { ref?:string; value?:number|string|boolean|number[] }
export interface RuleCondition { id:string; label:string; enabled?:boolean; left:RuleOperand; operator:string; right:RuleOperand }
export interface RuleGroup { logic:'AND'|'OR'; conditions:(RuleCondition|RuleGroup)[] }
export interface MTFConfig {
  stop_sizing?: {enabled:boolean; target_pct:number; gross_rr:number};
  timeframes?: StageTimeframes | null;
  indicators?: Record<string,IndicatorInstance> | null;
  frame_atr?: Partial<Record<Timeframe,string>> | null;
  rules?: {armed:RuleGroup;trigger:RuleGroup;vetoes:(RuleCondition|RuleGroup)[];scoring:{id:string;points:number;when:RuleGroup}[]} | null;
  btc_trend_timeframe?: Timeframe;
  schema_version: string; enabled: boolean; calibrated: false; default_watchlist: string; monitored_watchlists: string[];
  poll_seconds: number; history_page_size: number; candle_limits: Record<Timeframe, number>; stale_tf_multiplier: number;
  presentation?: Presentation;
  [key: string]: unknown;
}
export interface Presentation {
  entry_band_enabled?: boolean | null;
  fear_greed?: { enabled: boolean; refresh_seconds: number; max_age_seconds: number } | null;
  target_pct?: number;
  visible_candles: Record<Timeframe,number>; panel_heights: Record<string,number>;
  default_indicators: string[]; markers_max: number; markers_recent_signals: number;
  pivots_per_type: number; marker_min_range_atr: number; zones_per_side: number;
  removal_confirmations: number; retry_initial_seconds: number; retry_max_seconds: number;
  watchlist_poll_seconds: number; autoscale_clip_pct: number; pair_metadata_ttl_seconds: number;
  reason_labels: Record<string,string>;
}
export interface EntryCandidate {
  state: 'WAITING' | 'BLOCKED' | 'CONFIRMED' | 'UNAVAILABLE'; reason: string | null;
  timeframe: Timeframe; low: number | null; high: number | null; support_low: number | null; support_high: number | null;
  atr: number | null; proximity_atr: number; entry: number | null; evaluated_at: string; valid_until: string;
  config_hash: string; vetoes: string[]; operational_effect: false;
}
export interface Preferences { theme: 'dark' | 'light'; timezone: 'America/Sao_Paulo' | 'UTC'; sound: boolean; aligned_only: boolean;
  last_symbol?: string | null; last_watchlist?: string | null; last_timeframe?: Timeframe | null;
  enabled_indicators?: string[] | null; show_patterns?: boolean; show_pivots?: boolean; show_zones?: boolean;
  collapsed_panels?: string[]; clipped_scale?: boolean;
  show_target?: boolean;
}
export interface ConfigSchema { type?: string; $ref?: string; $defs?: Record<string, ConfigSchema>; properties?: Record<string, ConfigSchema>; anyOf?: ConfigSchema[]; items?: ConfigSchema; additionalProperties?: ConfigSchema | boolean; minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number; enum?: unknown[]; required?: string[]; minItems?: number }
export interface Bootstrap { effective_preset?: MTFConfig | null; effective_config?: MTFConfig|null; conversion_error?:string|null; catalog?: {indicators:Record<string,{parameters:string[];outputs:string[]}>;timeframes:Timeframe[];context_fields:string[];operators:string[]}; pump_flow_preset?: Record<string, unknown>; config: MTFConfig | null; preset: MTFConfig; presentation_preset: Presentation; watchlists: Watchlist[]; profiles: Profile[]; preferences: Partial<Preferences>; inherited_atr?: { enabled?: boolean; period?: number } | null; config_schema?: ConfigSchema }
export interface HistoryItem { evaluation: Analysis; observed_at: string; shadow: { status: string; pnl_pct?: number | null; pnl_usdt?: number | null; fill?: number; ambiguous?: boolean; reason?: string } | null }
export interface MTFReport { groups: { timeframe?: Timeframe; pattern: string; config_hash: string; n: number; mean_pct: number; ambiguous: number; sample_sufficient: boolean; confidence_interval: [number,number] | null; fee_per_side_pct: number; slippage_pct: number }[]; confidence_level: number; formula: string; origin: string; method: string }
export const statusLabels: Record<MTFStatus, string> = { NO_TRADE: 'SEM TRADE', WAIT: 'AGUARDAR', ARMED: 'ARMADO', TRIGGERED: 'GATILHO' };
export const patternLabels: Record<string, string> = { ENGULF_BULL: 'Engolfo de alta', ENGULF_BEAR: 'Engolfo de baixa', PIN_BULL: 'Martelo / Pin de alta', PIN_BEAR: 'Pin de baixa', SHOOTING_STAR: 'Estrela cadente', DOJI: 'Doji', HARAMI: 'Harami', INSIDE_BAR: 'Inside Bar', MARUBOZU_BULL: 'Marubozu de alta', MARUBOZU_BEAR: 'Marubozu de baixa' };
export const patternShort: Record<string, string> = { ENGULF_BULL: 'ENG', ENGULF_BEAR: 'ENG', PIN_BULL: 'MART', PIN_BEAR: 'PIN', SHOOTING_STAR: 'EC', DOJI: 'DOJI', HARAMI: 'HAR', INSIDE_BAR: 'IB', MARUBOZU_BULL: 'MARU', MARUBOZU_BEAR: 'MARU' };
export const shadowLabels: Record<string,string> = {PENDING:'Entrada pendente',OPEN:'Em acompanhamento',TP:'Alvo',SL:'Stop',EXPIRED:'Expirado',UNRESOLVED:'Sem resolução'};
export function readingLabel(line:string) {
  const labels={...patternLabels,UP:'alta',DOWN:'baixa',RANGE:'lateral'};
  return Object.entries(labels).reduce((text,[code,label])=>text.replace(new RegExp(`\\b${code}\\b`,'g'),label),line);
}
export const number = (value: number | undefined | null, digits = 5) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('pt-BR', { maximumFractionDigits: digits });

export function mergeChart(previous: ChartData | null, incoming: ChartData): ChartData {
  if (incoming.transport !== 'delta') return incoming;
  if (!previous || previous.symbol !== incoming.symbol || previous.analysis.config_hash !== incoming.analysis.config_hash || previous.profile?.id !== incoming.profile?.id) throw new Error('Contexto incremental inválido');
  const merge = <T extends { time: number }>(old: T[], updates: T[], start?: number) =>
    [...new Map([...old, ...updates].map(p => [p.time, p])).values()].filter(p => start == null || p.time >= start).sort((a,b)=>a.time-b.time);
  const frames = { ...incoming.frames };
  for (const tf of Object.keys(frames) as Timeframe[]) {
    const f = frames[tf], old = previous.frames[tf];
    const overlays = <T extends {time:number}>(before:T[], updates:T[], removed:[number,string][], discriminator:(p:T)=>string) => {
      const key=(p:T)=>`${p.time}:${discriminator(p)}`;
      const map=new Map(before.map(p=>[key(p),p]));removed.forEach(([t,k])=>map.delete(`${t}:${k}`));updates.forEach(p=>map.set(key(p),p));
      return [...map.values()].sort((a,b)=>a.time-b.time);
    };
    frames[tf] = { ...f, zones:f.zones??old.zones, candles: merge(old.candles, f.candles, f.window_start),
      patterns:overlays(old.patterns,f.patterns,f.patterns_removed??[],p=>p.pattern),
      pivots:overlays(old.pivots,f.pivots,f.pivots_removed??[],p=>p.kind),
      indicators: Object.fromEntries(Object.entries(f.indicators).map(([k,v]) => [k, merge(old.indicators[k] ?? [], v, f.window_start)])) };
  }
  return { ...incoming, frames };
}

export function visibleMarks(frame: Frame, config: Presentation, preferences: Preferences, trigger?: Pattern | null) {
  const selected: { time: number; label: string; above: boolean; pattern: boolean }[] = [];
  const used = new Set<number>();
  const add = (p: Pattern) => {
    if (used.has(p.time) || selected.length >= config.markers_max) return;
    used.add(p.time); selected.push({time:p.time,label:patternShort[p.pattern] ?? p.pattern,
      above:p.pattern.includes('BEAR') || p.pattern==='SHOOTING_STAR',pattern:true});
  };
  if (preferences.show_patterns !== false) {
    if (trigger && frame.candles.some(c=>c.time===trigger.time)) add(trigger);
    const major = new Set(['ENGULF_BULL','ENGULF_BEAR','PIN_BULL','SHOOTING_STAR','MARUBOZU_BULL','MARUBOZU_BEAR']);
    for (const p of [...frame.patterns].reverse()) {
      if (used.size >= config.markers_recent_signals + (trigger ? 1 : 0)) break;
      if (major.has(p.pattern) && p.metrics.atr > 0 && p.metrics.range >= config.marker_min_range_atr*p.metrics.atr) add(p);
    }
  }
  if (preferences.show_pivots !== false) {
    const counts: Record<string,number> = {};
    for (const p of [...frame.pivots].reverse()) {
      if (selected.length >= config.markers_max) break;
      if ((counts[p.label] ?? 0) >= config.pivots_per_type) continue;
      counts[p.label] = (counts[p.label] ?? 0) + 1;
      selected.push({time:p.time,label:p.label,above:p.kind==='H',pattern:false});
    }
  }
  return selected.sort((a,b)=>a.time-b.time);
}

export function reasonLabel(code: string, config?: Presentation) {
  const [key, suffix] = code.split(':');
  const label = config?.reason_labels[key] ?? (key === 'INVALID_PLAN' ? 'Plano inválido: entrada, stop ou risco inconsistente' : 'Condição de análise indisponível');
  return suffix ? `${label} (${suffix.toUpperCase()})` : label;
}

export interface FlowReading {
  state: string; reason?: string | null; ratio?: number | null; net_usdt?: number | null;
  coverage?: number; usable_minutes?: number; expected_minutes?: number; buy_persistence?: number | null;
  window_start?: string; window_end?: string;
}
export interface PumpFlowContext {
  config?: {asset_window?: Timeframe};
  confirmation: string; capital: Partial<Record<Timeframe, FlowReading>>; asset: Partial<FlowReading>;
  captured_at?: string; cutoff?: string; config_hash?: string; reasons?: string[];
}
export interface FlowComparison {
  status: string; minimum_sample: number;
  groups: {flow_config_hash: string; mtf_config_hash: string; cohort: string; observations: number;
    closed: number; mean_net_pct: number | null; ambiguous: number}[];
}
