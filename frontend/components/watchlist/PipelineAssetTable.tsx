'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, MinusCircle, RefreshCw } from 'lucide-react';

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
  condition_text: string;
  category: string;
}

export interface PipelineAssetWithScore {
  id: string;
  symbol: string;
  current_price: number | null;
  price_change_24h: number | null;
  volume_24h: number | null;
  market_cap: number | null;
  alpha_score: number | null;
  level_direction: string | null;
  indicators: Record<string, any>;
  score_rules: ScoreRule[];
}

// ── Fixed indicator columns shown in the table ─────────────────────────────────

const INDICATOR_COLS = [
  { key: 'rsi',            label: 'RSI' },
  { key: 'volume_spike',   label: 'Vol Spike' },
  { key: 'taker_ratio',    label: 'Taker Ratio' },
  { key: 'adx',            label: 'ADX' },
  { key: 'macd_histogram', label: 'MACD Hist' },
  { key: 'ema9_gt_ema50',  label: 'EMA Trend' },
] as const;

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtIndValue(key: string, value: any): string {
  if (value == null) return '—';
  if (typeof value === 'boolean') {
    if (key === 'ema9_gt_ema50' || key === 'ema_trend') return value ? '9>50' : '9<50';
    if (key === 'ema_full_alignment') return value ? '9>50>200' : '—';
    return value ? '✓' : '✗';
  }
  if (key === 'macd_histogram') {
    const n = Number(value);
    return (n >= 0 ? '+' : '') + n.toFixed(4);
  }
  const n = Number(value);
  if (isNaN(n)) return String(value);
  if (Math.abs(n) >= 100) return n.toFixed(1);
  if (Math.abs(n) >= 1)   return n.toFixed(2);
  return n.toFixed(4);
}

function getRuleForIndicator(key: string, rules: ScoreRule[]): ScoreRule | undefined {
  let r = rules.find(r => r.indicator === key);
  if (!r && key === 'ema9_gt_ema50')
    r = rules.find(r => r.operator === 'ema9>ema50' || r.operator === 'ema9>ema50>ema200');
  return r;
}

function getStatus(score: number) {
  if (score >= 75) return { label: 'STRONG', cls: 'text-[#34D399]', dot: 'bg-[#34D399]' };
  if (score >= 60) return { label: 'GOOD',   cls: 'text-[#4ADE80]', dot: 'bg-[#4ADE80]' };
  if (score >= 40) return { label: 'MIXED',  cls: 'text-[#FBBF24]', dot: 'bg-[#FBBF24]' };
  return               { label: 'WEAK',   cls: 'text-[#F87171]', dot: 'bg-[#F87171]' };
}

function getWeaknesses(rules: ScoreRule[]): string {
  const failed = rules
    .filter(r => !r.passed && r.points_possible > 0)
    .sort((a, b) => b.points_possible - a.points_possible)
    .slice(0, 2);
  return failed.map(r => r.label).join(' · ');
}

function scoreBarColor(score: number) {
  if (score >= 75) return '#34D399';
  if (score >= 60) return '#4ADE80';
  if (score >= 40) return '#FBBF24';
  return '#F87171';
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: 'pass' | 'fail' | 'neutral' }) {
  if (status === 'pass') return <CheckCircle2 size={12} className="text-[#34D399] shrink-0" />;
  if (status === 'fail') return <XCircle      size={12} className="text-[#F87171] shrink-0" />;
  return                        <MinusCircle  size={12} className="text-[#334155] shrink-0" />;
}

function IndicatorCell({ indKey, value, rules }: { indKey: string; value: any; rules: ScoreRule[] }) {
  const rule   = getRuleForIndicator(indKey, rules);
  const status = !rule ? 'neutral' : rule.passed ? 'pass' : 'fail';
  const disp   = fmtIndValue(indKey, value);
  const tip    = rule ? `${rule.condition_text}  →  ${rule.passed ? '+' + rule.points_awarded.toFixed(0) + ' pts' : 'falhou'}` : undefined;

  const textCls = status === 'pass' ? 'text-[#E2E8F0]'
                : status === 'fail' ? 'text-[#64748B]'
                : 'text-[#4B5563]';

  return (
    <div className="flex items-center justify-end gap-1" title={tip}>
      <span className={`font-mono text-xs ${textCls}`}>{disp}</span>
      <StatusIcon status={status} />
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const color = scoreBarColor(score);
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

function DrilldownPanel({ rules, score }: { rules: ScoreRule[]; score: number }) {
  const totalPossible = rules.reduce((s, r) => s + r.points_possible, 0);
  const totalAwarded  = rules.reduce((s, r) => s + r.points_awarded, 0);

  // Group by category
  const byCategory = CATEGORY_ORDER.reduce<Record<string, ScoreRule[]>>((acc, cat) => {
    const items = rules.filter(r => r.category === cat);
    if (items.length) acc[cat] = items;
    return acc;
  }, {});

  return (
    <div className="px-4 pt-3 pb-4 bg-[#06080E] border-t border-[#1A2035]">
      {/* Summary bar */}
      <div className="flex items-center gap-3 mb-4">
        <span className="text-[10px] font-semibold text-[#4B5563] uppercase tracking-wider">
          Score Breakdown
        </span>
        <span className="text-xs text-[#334155]">
          {totalAwarded.toFixed(0)} / {totalPossible.toFixed(0)} pts brutos
        </span>
        <div className="flex-1 h-1 bg-[#1A2035] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{
              width: `${totalPossible > 0 ? (totalAwarded / totalPossible) * 100 : 0}%`,
              backgroundColor: scoreBarColor(score),
            }}
          />
        </div>
        <span className="text-xs font-semibold" style={{ color: scoreBarColor(score) }}>
          {score.toFixed(1)}
        </span>
      </div>

      {/* Rules by category */}
      <div className="space-y-3">
        {Object.entries(byCategory).map(([cat, catRules]) => (
          <div key={cat}>
            <div className="text-[10px] font-medium text-[#334155] uppercase tracking-wider mb-1.5">
              {CATEGORY_LABELS[cat]}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1">
              {catRules.map((rule) => (
                <div
                  key={rule.id}
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${
                    rule.passed
                      ? 'bg-[#061E14] border-[#14532D]/40'
                      : 'bg-[#150A0A] border-[#7F1D1D]/25'
                  }`}
                  data-testid={`drilldown-rule-${rule.indicator}`}
                >
                  {rule.passed
                    ? <CheckCircle2 size={11} className="text-[#34D399] shrink-0" />
                    : <XCircle      size={11} className="text-[#F87171] shrink-0" />
                  }
                  <span className={`flex-1 truncate ${rule.passed ? 'text-[#94A3B8]' : 'text-[#4B5563]'}`}>
                    {rule.condition_text}
                  </span>
                  <span className={`font-mono text-[10px] shrink-0 ${
                    rule.actual_value != null
                      ? rule.passed ? 'text-[#CBD5E1]' : 'text-[#64748B]'
                      : 'text-[#334155]'
                  }`}>
                    {rule.actual_value != null ? fmtIndValue(rule.indicator, rule.actual_value) : '—'}
                  </span>
                  <span className={`font-mono text-[10px] shrink-0 w-14 text-right ${
                    rule.passed ? 'text-[#34D399]' : 'text-[#4B5563]'
                  }`}>
                    {rule.passed ? `+${rule.points_awarded.toFixed(0)}` : '+0'}/{rule.points_possible.toFixed(0)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {rules.length === 0 && (
        <p className="text-xs text-[#334155] text-center py-2">
          Sem regras de scoring configuradas — configure em Settings → Score.
        </p>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PipelineAssetTable({
  assets,
  onRefresh,
  refreshing,
  liveDirections = {},
}: {
  assets: PipelineAssetWithScore[];
  onRefresh: () => void;
  refreshing: boolean;
  liveDirections?: Record<string, string>;
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

  const totalPts = (rules: ScoreRule[]) => rules.reduce((s, r) => s + r.points_possible, 0);
  const earnedPts = (rules: ScoreRule[]) => rules.reduce((s, r) => s + r.points_awarded, 0);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs min-w-[960px]">
        <thead>
          <tr className="border-b border-[#1A2035] bg-[#060810] sticky top-0">
            <th className="w-8 px-2 py-2.5" />
            <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium">Symbol</th>
            <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium min-w-[130px]">Score</th>
            {INDICATOR_COLS.map(col => (
              <th key={col.key} className="px-3 py-2.5 text-right text-[#4B5563] font-medium whitespace-nowrap">
                {col.label}
              </th>
            ))}
            <th className="px-3 py-2.5 text-center text-[#4B5563] font-medium">Status</th>
            <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium">Weakness</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => {
            const score     = asset.alpha_score ?? 0;
            const rules     = asset.score_rules ?? [];
            const status    = getStatus(score);
            const weakness  = getWeaknesses(rules);
            const isExpanded = expandedRow === asset.symbol;
            const effectiveDir = liveDirections[asset.symbol] ?? asset.level_direction;

            const hasDivergence =
              rules.some(r => r.indicator === 'macd_histogram' && !r.passed) && score >= 65;
            const hasVolSpike =
              (asset.indicators?.['volume_spike'] ?? 0) > 2.5;
            const hasHighADX =
              (asset.indicators?.['adx'] ?? 0) > 40;

            const rowAlert = hasDivergence ? 'border-l-2 border-l-[#FBBF24]/60'
                           : hasVolSpike   ? 'border-l-2 border-l-[#60A5FA]/60'
                           : hasHighADX    ? 'border-l-2 border-l-[#A78BFA]/60'
                           : '';

            return (
              <>
                <tr
                  key={asset.symbol}
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
                      {hasDivergence && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-[#FBBF24]/10 text-[#FBBF24] border border-[#FBBF24]/20" title="Score alto mas MACD negativo">
                          DIV
                        </span>
                      )}
                      {hasHighADX && (
                        <span className="text-[9px] px-1 py-0.5 rounded bg-[#A78BFA]/10 text-[#A78BFA] border border-[#A78BFA]/20" title="ADX alto — possível breakout">
                          ADX
                        </span>
                      )}
                    </div>
                    {rules.length > 0 && (
                      <div className="mt-0.5 text-[10px] text-[#334155]">
                        {earnedPts(rules).toFixed(0)}/{totalPts(rules).toFixed(0)} pts
                      </div>
                    )}
                  </td>

                  {/* Score bar */}
                  <td className="px-3 py-2.5">
                    <ScoreBar score={score} />
                  </td>

                  {/* Fixed indicator columns */}
                  {INDICATOR_COLS.map(col => (
                    <td key={col.key} className="px-3 py-2.5 text-right">
                      <IndicatorCell
                        indKey={col.key}
                        value={asset.indicators?.[col.key]}
                        rules={rules}
                      />
                    </td>
                  ))}

                  {/* Status */}
                  <td className="px-3 py-2.5 text-center">
                    <div className="inline-flex items-center gap-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full ${status.dot} shrink-0`} />
                      <span className={`font-semibold text-[10px] tracking-wide ${status.cls}`}>
                        {status.label}
                      </span>
                    </div>
                  </td>

                  {/* Weakness */}
                  <td className="px-3 py-2.5 max-w-[140px]">
                    {weakness ? (
                      <span className="text-[#F87171]/70 text-[10px] leading-tight">{weakness}</span>
                    ) : (
                      <span className="text-[#1E2433]">—</span>
                    )}
                  </td>
                </tr>

                {/* Drilldown row */}
                {isExpanded && (
                  <tr key={`${asset.symbol}-drill`} className="border-b border-[#1A2035]">
                    <td colSpan={10} className="p-0">
                      <DrilldownPanel rules={rules} score={score} />
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
