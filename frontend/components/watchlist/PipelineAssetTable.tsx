'use client';

import { Fragment, useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, RefreshCw } from 'lucide-react';
import { EvaluationTraceBreakdown, type EvaluationTraceItem } from './EvaluationTraceBreakdown';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ScoreRule {
  id: string;
  indicator: string;
  label: string;
  operator: string;
  target_value: number | string | null;
  min: number | null;
  max: number | null;
  actual_value: number | boolean | string | null;
  passed: boolean;
  points_awarded: number;
  points_possible: number;
  type?: "positive" | "penalty";
  condition_text: string;
  category: string;
  scheduler_group?: string | null;
  indicator_age_seconds?: number | null;
}

// ── Shared score-rule colour tokens ──────────────────────────────────────────
export const RULE_COLORS = {
  positiveMatched:   { text: 'text-[#34D399]', bg: 'bg-[#061E14] border-[#14532D]/40' },
  penaltyFired:      { text: 'text-[#F87171]', bg: 'bg-[#1C0808] border-[#7F1D1D]/60' },
  positiveUnmatched: { text: 'text-[#4B5563]', bg: 'bg-[#0A0C14] border-[#1A2035]/50' },
  penaltyIdle:       { text: 'text-[#4B5563]', bg: 'bg-[#0A0C14] border-[#1A2035]/30' },
} as const;

/** Format a points value for display in score badges.
 *  - Avoids "-0" by checking Math.abs < 0.5 before rounding.
 *  - Badge uses integer; tooltip should use .toFixed(2) for precision. */
export function fmtPts(v: number): string {
  if (Math.abs(v) < 0.5) return '0';
  const r = Math.round(v);
  return r > 0 ? `+${r}` : `${r}`;
}

/** Sort score rules: positive rules first (descending by points_possible),
 *  then penalty rules (ascending by magnitude — most negative first). */
export function sortScoreRules(rules: ScoreRule[]): ScoreRule[] {
  const pos = rules.filter(r => (r.type ?? 'positive') !== 'penalty');
  const pen = rules.filter(r => r.type === 'penalty');
  pos.sort((a, b) => b.points_possible - a.points_possible);
  pen.sort((a, b) => a.points_possible - b.points_possible);
  return [...pos, ...pen];
}

type IndicatorValue = number | boolean | string | null | undefined;

export interface PipelineAssetWithScore {
  id: string;
  symbol: string;
  current_price: number | null;
  price_change_24h: number | null;
  volume_24h: number | null;
  market_cap: number | null;
  alpha_score: number | null;
  level_direction: string | null;
  blocked: boolean;
  block_reasons: string[];
  indicators: Record<string, IndicatorValue>;
  score_rules: ScoreRule[];
  score_classification?: string | null;
  evaluation_trace?: EvaluationTraceItem[];
}

export interface IndicatorColumn {
  key: string;
  label: string;
  field: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtMarketCap(val: number | null | undefined): string {
  if (val == null || val === 0) return '—';
  if (val >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(1)}B`;
  if (val >= 1_000_000)     return `$${(val / 1_000_000).toFixed(0)}M`;
  if (val >= 1_000)         return `$${(val / 1_000).toFixed(0)}K`;
  return `$${val.toFixed(0)}`;
}

function marketCapColor(val: number | null | undefined): string {
  if (val == null || val === 0) return 'text-[#4B5563]';
  if (val < 50_000_000)  return 'text-[#F87171]';  // microcap red
  if (val <= 500_000_000) return 'text-[#FBBF24]';  // yellow
  return 'text-[#34D399]';  // green
}

function fmtDepth(val: number | null | undefined): string {
  if (val == null) return '—';
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(0)}k`;
  return val.toFixed(0);
}

function fmtVolume(val: number | null | undefined): string {
  if (val == null || val === 0) return '—';
  if (val >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(1)}B`;
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

// Backend sends market metadata columns with an "_meta:" prefix so they can share
// one table schema with computed indicators without colliding with raw field names.
function normalizeIndicatorKey(key: string): string {
  return key.startsWith('_meta:') ? key.slice(6) : key;
}

/** Color-coded indicator cell value + emoji dot */
function getIndicatorColor(key: string, value: IndicatorValue): { text: string; cls: string; dot: string } {
  if (value == null) return { text: '—', cls: 'text-[#4B5563]', dot: '' };
  const normalizedKey = normalizeIndicatorKey(key);

  if (typeof value === 'boolean') {
    if (normalizedKey === 'di_trend') {
      return {
        text: value ? '▲ DI+' : '▼ DI-',
        cls: value ? 'text-[#34D399]' : 'text-[#F87171]',
        dot: '',
      };
    }
    if (normalizedKey === 'ema_full_alignment') {
      return {
        text: value ? '9>50>200' : '—',
        cls: value ? 'text-[#34D399]' : 'text-[#4B5563]',
        dot: '',
      };
    }
    return {
      text: value ? '✓' : '✗',
      cls: value ? 'text-[#34D399]' : 'text-[#F87171]',
      dot: '',
    };
  }

  if (typeof value === 'string') {
    if (value === '9>50>200') return { text: '9›50›200', cls: 'text-[#34D399]', dot: '' };
    if (value === '9>50') return { text: '9›50', cls: 'text-[#FBBF24]', dot: '' };
    if (value === '9<50<200') return { text: '9‹50‹200', cls: 'text-[#F87171]', dot: '' };
    if (value === 'mix') return { text: 'mix', cls: 'text-[#94A3B8]', dot: '' };
    if (value === 'positive') return { text: value, cls: 'text-[#34D399]', dot: '' };
    if (value === 'negative') return { text: value, cls: 'text-[#F87171]', dot: '' };
    return { text: value, cls: 'text-[#CBD5E1]', dot: '' };
  }

  const n = Number(value);
  if (isNaN(n)) return { text: String(value), cls: 'text-[#4B5563]', dot: '' };

  switch (normalizedKey) {
    case 'market_cap':
      return {
        text: fmtMarketCap(n),
        cls: marketCapColor(n),
        dot: '',
      };
    case 'volume_24h':
      return {
        text: fmtVolume(n),
        cls: 'text-[#E2E8F0]',
        dot: '',
      };
    case 'price_change_24h':
    case 'change_24h':
      return {
        text: `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`,
        cls: n >= 0 ? 'text-[#34D399]' : 'text-[#F87171]',
        dot: n >= 0 ? '🟢' : '🔴',
      };
    case 'rsi':
      return {
        text: n.toFixed(1),
        cls: (n >= 45 && n <= 65) ? 'text-[#34D399]' : (n > 75 || n < 35) ? 'text-[#F87171]' : 'text-[#E2E8F0]',
        dot: (n >= 45 && n <= 65) ? '🟢' : (n > 75 || n < 35) ? '🔴' : '',
      };
    case 'volume_spike':
      return {
        text: n.toFixed(1),
        cls: n > 2.0 ? 'text-[#34D399]' : n >= 1.3 ? 'text-[#FBBF24]' : 'text-[#F87171]',
        dot: n > 2.0 ? '🟢' : n >= 1.3 ? '🟡' : '🔴',
      };
    case 'taker_ratio':
      // Canonical scale since #82: buy/(buy+sell) ∈ [0, 1]. Equilibrium = 0.5.
      // 3-decimal display so 0.512 vs 0.498 is distinguishable next to thresholds.
      return {
        text: n.toFixed(3),
        cls: n > 0.6 ? 'text-[#34D399]' : n < 0.4 ? 'text-[#F87171]' : 'text-[#94A3B8]',
        dot: n > 0.6 ? '🟢' : n < 0.4 ? '🔴' : '⚪',
      };
    case 'spread_pct':
      return {
        text: n.toFixed(2) + '%',
        cls: n <= 0.8 ? 'text-[#34D399]' : n <= 1.5 ? 'text-[#FBBF24]' : 'text-[#F87171]',
        dot: n <= 0.8 ? '🟢' : n <= 1.5 ? '🟡' : '🔴',
      };
    case 'orderbook_depth_usdt':
      return {
        text: fmtDepth(n),
        cls: n >= 5000 ? 'text-[#E2E8F0]' : 'text-[#F87171]',
        dot: n < 5000 ? '🔴' : '',
      };
    case 'ema9_distance_pct':
      return {
        text: (n >= 0 ? '+' : '') + n.toFixed(2) + '%',
        cls: Math.abs(n) > 3 ? 'text-[#FBBF24]' : 'text-[#E2E8F0]',
        dot: '',
      };
    default:
      return {
        text: Math.abs(n) >= 100 ? n.toFixed(1) : Math.abs(n) >= 1 ? n.toFixed(2) : n.toFixed(4),
        cls: 'text-[#E2E8F0]',
        dot: '',
      };
  }
}

function fmtIndValue(key: string, value: IndicatorValue): string {
  if (value == null) return '—';
  const normalizedKey = normalizeIndicatorKey(key);
  if (typeof value === 'boolean') {
    if (normalizedKey === 'ema9_gt_ema50' || normalizedKey === 'ema_trend') return value ? '9>50' : '9<50';
    if (normalizedKey === 'ema_full_alignment') return value ? '9>50>200' : '—';
    if (normalizedKey === 'di_trend') return value ? '▲ DI+' : '▼ DI-';
    return value ? '✓' : '✗';
  }
  if (normalizedKey === 'macd_histogram') {
    const n = Number(value);
    return (n >= 0 ? '+' : '') + n.toFixed(4);
  }
  const n = Number(value);
  if (isNaN(n)) return String(value);
  if (normalizedKey === 'market_cap') return fmtMarketCap(n);
  if (normalizedKey === 'volume_24h') return fmtVolume(n);
  if (normalizedKey === 'price_change_24h' || normalizedKey === 'change_24h') return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
  if (normalizedKey === 'orderbook_depth_usdt') return fmtDepth(n);
  if (normalizedKey === 'spread_pct' || normalizedKey === 'ema9_distance_pct') return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
  if (Math.abs(n) >= 100) return n.toFixed(1);
  if (Math.abs(n) >= 1)   return n.toFixed(2);
  return n.toFixed(4);
}

function getRuleForIndicator(keys: string[], rules: ScoreRule[]): ScoreRule | undefined {
  let r = rules.find((rule) => keys.includes(rule.indicator));
  if (!r && keys.includes('price_change_24h')) r = rules.find((rule) => rule.indicator === 'change_24h');
  if (!r && keys.includes('change_24h')) r = rules.find((rule) => rule.indicator === 'price_change_24h');
  if (!r && keys.includes('ema9_gt_ema50')) {
    r = rules.find((rule) => rule.operator === 'ema9>ema50' || rule.operator === 'ema9>ema50>ema200');
  }
  return r;
}

function getStatus(score: number, classification?: string | null, blocked: boolean = false) {
  if (blocked) return { label: 'BLOCKED', cls: 'text-[#F87171]', dot: 'bg-[#F87171]' };
  if (classification === 'strong_buy') return { label: 'STRONG', cls: 'text-[#34D399]', dot: 'bg-[#34D399]' };
  if (classification === 'buy') return { label: 'GOOD', cls: 'text-[#4ADE80]', dot: 'bg-[#4ADE80]' };
  if (classification === 'neutral') return { label: 'MIXED', cls: 'text-[#FBBF24]', dot: 'bg-[#FBBF24]' };
  if (classification === 'avoid' || classification === 'no_data') {
    return { label: 'WEAK', cls: 'text-[#F87171]', dot: 'bg-[#F87171]' };
  }
  return { label: score > 0 ? 'SCORED' : 'WEAK', cls: 'text-[#94A3B8]', dot: 'bg-[#94A3B8]' };
}

function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

const GROUP_LABEL: Record<string, string> = {
  structural: 'struct',
  microstructure: 'micro',
  combined: 'cmbd',
};

const GROUP_COLOR: Record<string, string> = {
  structural: 'text-[#60A5FA]',
  microstructure: 'text-[#34D399]',
  combined: 'text-[#94A3B8]',
};

function getWeaknesses(rules: ScoreRule[]): string {
  const failed = rules
    .filter(r => !r.passed && r.points_possible > 0)
    .sort((a, b) => b.points_possible - a.points_possible)
    .slice(0, 2);
  return failed.map(r => r.label).join(' · ');
}

function scoreBarColor(score: number, classification?: string | null, blocked: boolean = false) {
  const status = getStatus(score, classification, blocked);
  if (status.label === 'STRONG') return '#34D399';
  if (status.label === 'GOOD') return '#4ADE80';
  if (status.label === 'MIXED') return '#FBBF24';
  if (status.label === 'SCORED') return '#94A3B8';
  return '#F87171';
}

// ── Sub-components ────────────────────────────────────────────────────────────

function IndicatorCell({ column, value, rules }: { column: IndicatorColumn; value: IndicatorValue; rules: ScoreRule[] }) {
  const { text, cls, dot } = getIndicatorColor(column.key, value);
  const normalizedKey = normalizeIndicatorKey(column.key);
  // Deduplicate alias candidates because meta columns may resolve to the same rule key.
  const rule = getRuleForIndicator(
    Array.from(new Set([column.field, column.key, normalizedKey])),
    rules,
  );
  const tip = rule
    ? `${rule.condition_text}  →  ${rule.passed ? fmtPts(rule.points_awarded) + ' pts (' + rule.points_awarded.toFixed(2) + ')' : 'falhou'}`
    : undefined;

  return (
    <div className="flex items-center justify-end gap-1" title={tip}>
      {dot && <span className="text-[10px]">{dot}</span>}
      <span className={`font-mono text-xs ${cls}`}>{text}</span>
    </div>
  );
}

function ScoreBar({
  score,
  classification,
  blocked = false,
}: {
  score: number;
  classification?: string | null;
  blocked?: boolean;
}) {
  const color = scoreBarColor(score, classification, blocked);
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="relative flex-1 h-1.5 bg-[#1A2035] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${Math.min(100, score)}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-sm font-bold text-[#E2E8F0] tabular-nums w-7 text-right"
            style={{ color }}>
        {Math.round(score)}
      </span>
    </div>
  );
}

// ── Drilldown Panel ───────────────────────────────────────────────────────────

const CATEGORY_ORDER = ['momentum', 'market_structure', 'liquidity', 'signal', 'other'];
const CATEGORY_LABELS: Record<string, string> = {
  momentum: 'Momentum', market_structure: 'Estrutura de Mercado',
  liquidity: 'Liquidez', signal: 'Sinal', other: 'Outros',
};

function DrilldownPanel({
  rules,
  score,
  classification,
  blocked = false,
  evaluationTrace = [],
}: {
  rules: ScoreRule[];
  score: number;
  classification?: string | null;
  blocked?: boolean;
  evaluationTrace?: EvaluationTraceItem[];
}) {
  // Separate positive rules from penalty rules for correct header totals.
  const totalPossible   = rules.filter(r => (r.type ?? 'positive') !== 'penalty').reduce((s, r) => s + r.points_possible, 0);
  const earnedPositive  = rules.filter(r => (r.type ?? 'positive') !== 'penalty').reduce((s, r) => s + r.points_awarded, 0);
  const totalPenalties  = rules.filter(r => r.type === 'penalty').reduce((s, r) => s + r.points_awarded, 0);

  // Group by category; within each category sort positive-first then penalties.
  const byCategory = CATEGORY_ORDER.reduce<Record<string, ScoreRule[]>>((acc, cat) => {
    const items = sortScoreRules(rules.filter(r => r.category === cat));
    if (items.length) acc[cat] = items;
    return acc;
  }, {});

  return (
    <div className="px-4 pt-3 pb-4 bg-[#06080E] border-t border-[#1A2035]">
      {/* Summary bar */}
      <div className="flex items-center gap-3 mb-1">
        <span className="text-[10px] font-semibold text-[#4B5563] uppercase tracking-wider">
          Score Breakdown
        </span>
        <span className="text-xs text-[#334155]">
          Score: {fmtPts(earnedPositive)}/{totalPossible.toFixed(0)} pts
        </span>
        <div className="flex-1 h-1 bg-[#1A2035] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{
              width: `${totalPossible > 0 ? Math.max(0, Math.min(100, (earnedPositive / totalPossible) * 100)) : 0}%`,
              backgroundColor: scoreBarColor(score, classification, blocked),
            }}
          />
        </div>
        <span className="text-xs font-semibold" style={{ color: scoreBarColor(score, classification, blocked) }}>
          {score.toFixed(1)}
        </span>
      </div>
      {totalPenalties < 0 && (
        <div className="mb-3">
          <span className="text-[10px] text-[#F87171]">
            Penalty: {totalPenalties.toFixed(0)}
          </span>
        </div>
      )}
      {!totalPenalties && <div className="mb-3" />}

      {/* Rules by category */}
      <div className="space-y-3">
        {Object.entries(byCategory).map(([cat, catRules]) => (
          <div key={cat}>
            <div className="text-[10px] font-medium text-[#334155] uppercase tracking-wider mb-1.5">
              {CATEGORY_LABELS[cat]}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1">
              {catRules.map((rule) => {
                const isPenalty = rule.type === 'penalty';
                const isFired   = rule.passed;
                // Semantics: a penalty that fired is "bad"; a penalty not fired is "safe"
                const isGood    = !isPenalty && isFired;
                const isBad     = (isPenalty && isFired) || (!isPenalty && !isFired);
                const colors    = isPenalty
                  ? (isFired ? RULE_COLORS.penaltyFired : RULE_COLORS.penaltyIdle)
                  : (isFired ? RULE_COLORS.positiveMatched : RULE_COLORS.positiveUnmatched);

                const grpKey = rule.scheduler_group ?? '';
                const grpLabel = GROUP_LABEL[grpKey] ?? grpKey;
                const grpColor = GROUP_COLOR[grpKey] ?? 'text-[#4B5563]';
                const ageStr = fmtAge(rule.indicator_age_seconds);

                const awardedDisplay = rule.passed ? fmtPts(rule.points_awarded) : '0';
                const possibleDisplay = fmtPts(rule.points_possible);

                return (
                  <div
                    key={rule.id}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${colors.bg}`}
                    data-testid={`drilldown-rule-${rule.indicator}`}
                  >
                    {isGood || (isPenalty && !isFired)
                      ? <CheckCircle2 size={11} className={`${isGood ? 'text-[#34D399]' : 'text-[#4B5563]'} shrink-0`} />
                      : <XCircle      size={11} className={`${isBad && !isPenalty ? 'text-[#F87171]' : 'text-[#F87171]'} shrink-0`} />
                    }
                    <span className={`flex-1 truncate ${isFired && !isPenalty ? 'text-[#94A3B8]' : isPenalty && isFired ? 'text-[#F87171]' : 'text-[#4B5563]'}`}>
                      {rule.condition_text}
                    </span>
                    <span className={`font-mono text-[10px] shrink-0 ${
                      rule.actual_value != null
                        ? isFired && !isPenalty ? 'text-[#CBD5E1]' : isPenalty && isFired ? 'text-[#FCA5A5]' : 'text-[#64748B]'
                        : 'text-[#334155]'
                    }`}>
                      {rule.actual_value != null ? fmtIndValue(rule.indicator, rule.actual_value) : '—'}
                    </span>
                    {grpLabel && (
                      <span className={`font-mono text-[9px] shrink-0 ${grpColor} opacity-70`}
                            title={`${rule.scheduler_group ?? ''}${ageStr ? ` · ${ageStr} ago` : ''}`}>
                        {grpLabel}{ageStr ? `·${ageStr}` : ''}
                      </span>
                    )}
                    <span
                      className={`font-mono text-[10px] shrink-0 w-16 text-right ${colors.text}`}
                      title={`${rule.passed ? rule.points_awarded.toFixed(2) : '0'} / ${rule.points_possible.toFixed(2)} pts`}
                    >
                      {awardedDisplay}/{possibleDisplay}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {rules.length === 0 && (
        <p className="text-xs text-[#334155] text-center py-2">
          Sem regras de scoring configuradas — configure em Settings → Score.
        </p>
      )}

      {evaluationTrace.length > 0 && (
        <div className="mt-4 space-y-3">
          <div className="text-[10px] font-semibold text-[#4B5563] uppercase tracking-wider">
            Profile Evaluation
          </div>
          <EvaluationTraceBreakdown items={evaluationTrace} />
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PipelineAssetTable({
  assets,
  indicatorCols = [],
  onRefresh,
  refreshing,
  liveDirections = {},
  showScore = true,
}: {
  assets: PipelineAssetWithScore[];
  indicatorCols?: IndicatorColumn[];
  onRefresh: () => void;
  refreshing: boolean;
  liveDirections?: Record<string, string>;
  showScore?: boolean;
}) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  if (assets.length === 0) {
    return (
      <div className="px-4 py-8 text-center">
        <p className="text-sm text-[#4B5563]">No assets. Click refresh to resolve the pipeline.</p>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="mt-3 px-4 py-1.5 text-xs rounded-lg bg-[#1E2433] text-[#94A3B8] hover:bg-[#263048] transition-colors disabled:opacity-40"
          data-testid="pipeline-refresh-btn"
        >
          {refreshing ? <span className="flex items-center gap-1.5"><RefreshCw size={11} className="animate-spin" />Refreshing…</span> : 'Refresh Now'}
        </button>
      </div>
    );
  }

  const totalPts  = (rules: ScoreRule[]) =>
    rules.filter(r => (r.type ?? 'positive') !== 'penalty').reduce((s, r) => s + r.points_possible, 0);
  const earnedPts = (rules: ScoreRule[]) =>
    rules.filter(r => (r.type ?? 'positive') !== 'penalty').reduce((s, r) => s + r.points_awarded, 0);
  const visibleColumns = indicatorCols;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs min-w-[960px]">
        <thead>
          <tr className="border-b border-[#1A2035] bg-[#060810] sticky top-0">
            <th className="w-8 px-2 py-2.5" />
            <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium">Symbol</th>
            {showScore && (
              <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium min-w-[130px]">Score</th>
            )}
            {visibleColumns.map((col) => (
              <th key={col.key} className="px-3 py-2.5 text-right text-[#4B5563] font-medium whitespace-nowrap">
                {col.label}
              </th>
            ))}
            <th className="px-3 py-2.5 text-center text-[#4B5563] font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => {
            const score     = asset.alpha_score ?? 0;
            const rules     = asset.score_rules ?? [];
            const isBlocked = asset.blocked ?? false;
            const blockReasons = asset.block_reasons ?? [];
            const classification = asset.score_classification;
            const status    = getStatus(score, classification, isBlocked);
            const weakness  = getWeaknesses(rules);
            const isExpanded = expandedRow === asset.symbol;
            const effectiveDir = liveDirections[asset.symbol] ?? asset.level_direction;

            const hasDivergence =
              rules.some(r => r.indicator === 'macd_histogram' && !r.passed) && score >= 65;
            const hasVolSpike =
              Number(asset.indicators?.['volume_spike'] ?? 0) > 2.5;
            const hasHighADX =
              Number(asset.indicators?.['adx'] ?? 0) > 40;

            const rowAlert = isBlocked        ? 'border-l-2 border-l-[#F87171]/60'
                           : hasDivergence    ? 'border-l-2 border-l-[#FBBF24]/60'
                           : hasVolSpike      ? 'border-l-2 border-l-[#60A5FA]/60'
                           : hasHighADX       ? 'border-l-2 border-l-[#A78BFA]/60'
                           : '';

            const getColumnValue = (column: IndicatorColumn) => {
              const topLevelValue = (asset as unknown as Record<string, unknown>)[column.field] as IndicatorValue;
              return asset.indicators?.[column.key]
                ?? asset.indicators?.[normalizeIndicatorKey(column.key)]
                ?? asset.indicators?.[column.field]
                ?? topLevelValue
                ?? null;
            };

            return (
              <Fragment key={asset.symbol}>
                <tr
                  className={`border-b border-[#1A2035]/60 cursor-pointer transition-colors ${rowAlert} ${
                    isExpanded ? 'bg-[#0C1020]' : 'hover:bg-[#0D1118]'
                  } ${effectiveDir === 'up' ? 'row-level-up' : effectiveDir === 'down' ? 'row-level-down' : ''}`}
                  onClick={() => setExpandedRow(isExpanded ? null : asset.symbol)}
                  data-testid={`pipeline-asset-row-${asset.symbol}`}
                >
                  {/* Expander */}
                  <td className="px-2 py-2.5 text-[#334155]">
                    {isExpanded
                      ? <ChevronDown  size={13} className="text-[#60A5FA]" />
                      : <ChevronRight size={13} />
                    }
                  </td>

                  {/* Symbol */}
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-[#E2E8F0] tracking-wide">{asset.symbol}</span>
                      {isBlocked && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-[#F87171]/10 text-[#F87171] border border-[#F87171]/20" title={`Bloqueado: ${blockReasons.join(', ')}`}>
                          🚨
                        </span>
                      )}
                      {hasDivergence && !isBlocked && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-[#FBBF24]/10 text-[#FBBF24] border border-[#FBBF24]/20" title="Score alto mas MACD negativo">
                          DIV
                        </span>
                      )}
                      {hasHighADX && !isBlocked && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-[#A78BFA]/10 text-[#A78BFA] border border-[#A78BFA]/20" title="ADX alto — possível breakout">
                          ADX
                        </span>
                      )}
                    </div>
                    {showScore && rules.length > 0 && (
                      <div className="mt-0.5 text-[10px] text-[#334155]">
                        {earnedPts(rules).toFixed(0)}/{totalPts(rules).toFixed(0)} pts
                        {weakness ? ` · ${weakness}` : ''}
                      </div>
                    )}
                  </td>

                   {/* Score bar — hidden for Stage 0 (POOL/custom) and Stage 1 (L1) */}
                  {showScore && (
                    <td className="px-3 py-2.5">
                      <ScoreBar score={score} classification={classification} blocked={isBlocked} />
                    </td>
                  )}

                  {/* Dynamic indicator columns from the profile */}
                  {visibleColumns.map((col) => (
                    <td key={col.key} className="px-3 py-2.5 text-right">
                      <IndicatorCell
                        column={col}
                        value={getColumnValue(col)}
                        rules={rules}
                      />
                    </td>
                  ))}

                  {/* Status */}
                  <td className="px-3 py-2.5 text-center">
                    <div className="inline-flex items-center gap-1.5" title={isBlocked ? blockReasons.join(', ') : ''}>
                      <div className={`w-1.5 h-1.5 rounded-full ${status.dot} shrink-0`} />
                      <span className={`font-semibold text-[10px] tracking-wide ${status.cls}`}>
                        {status.label}
                      </span>
                      {isBlocked && <span className="text-[9px]">🚨</span>}
                    </div>
                  </td>
                </tr>

                {/* Drilldown row */}
                {isExpanded && (
                  <tr className="border-b border-[#1A2035]">
                    <td colSpan={2 + (showScore ? 1 : 0) + 1 + visibleColumns.length} className="p-0">
                      <DrilldownPanel
                        rules={rules}
                        score={score}
                        classification={classification}
                        blocked={isBlocked}
                        evaluationTrace={asset.evaluation_trace ?? []}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
