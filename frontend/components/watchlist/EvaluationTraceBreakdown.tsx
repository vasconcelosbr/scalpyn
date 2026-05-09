'use client';

export interface EvaluationTraceItem {
  type: 'filter' | 'block_rule' | 'entry_trigger' | 'signal';
  indicator: string;
  condition: string;
  expected?: string | null;
  current_value?: unknown;
  status: 'PASS' | 'FAIL' | 'SKIPPED';
  reason?: string | null;
  // Block-rule-only authoritative fields (Task #253). `outcome` is the
  // circuit-breaker label that replaces the misleading PASS/FAIL on
  // block rules; `condition_matched` is the raw mathematical result.
  outcome?: 'OK' | 'TRIPPED' | 'SKIPPED';
  condition_matched?: boolean | null;
}

// Block rules use OK/TRIPPED instead of PASS/FAIL (Task #253). When the
// backend hasn't been redeployed yet, derive the outcome from the legacy
// status (FAIL ⇒ TRIPPED, PASS ⇒ OK) so historical snapshots keep working.
export function blockRuleOutcome(item: EvaluationTraceItem): 'OK' | 'TRIPPED' | 'SKIPPED' {
  if (item.outcome) return item.outcome;
  if (item.status === 'FAIL') return 'TRIPPED';
  if (item.status === 'PASS') return 'OK';
  return 'SKIPPED';
}

type SkipDisplay = {
  kind: 'cascade' | 'invalid' | 'no_data';
  label: string;
  cls: string;
  currentText: string | null;
  expectedOverride?: string;
};

export function classifySkip(item: { status: string; reason?: string | null; current_value?: unknown }): SkipDisplay | null {
  const reason = item.reason ?? null;
  if (reason === 'cascade_short_circuit') {
    return {
      kind: 'cascade',
      label: 'PULADO',
      cls: 'border-[#1E2433] bg-[#0B1220] text-[#64748B]',
      currentText: '—',
      expectedOverride: 'bloco anterior já rejeitou',
    };
  }
  if (reason === 'indicator_invalid_value') {
    return {
      kind: 'invalid',
      label: 'VALOR INVÁLIDO',
      cls: 'border-[#7C2D12]/50 bg-[#1A0E05] text-[#FB923C]',
      currentText: null,
    };
  }
  const isNoData =
    item.status === 'SKIPPED' || (item.status === 'FAIL' && item.current_value == null);
  if (isNoData) {
    return {
      kind: 'no_data',
      label: 'SEM DADOS',
      cls: 'border-[#78350F]/40 bg-[#1A1205] text-[#FCD34D]',
      currentText: 'aguardando coleta',
    };
  }
  return null;
}

export function formatEvaluationTraceValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'number') {
    const abs = Math.abs(value);
    if (abs >= 100) return value.toFixed(1);
    if (abs >= 1) return value.toFixed(2);
    return value.toFixed(4);
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  if (Array.isArray(value) || typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function EvaluationTraceBreakdown({
  items,
  emptyMessage = 'No rules configured.',
}: {
  items: EvaluationTraceItem[];
  emptyMessage?: string;
}) {
  const blockRules = items.filter((item) => item.type === 'block_rule');
  const filters = items.filter((item) => item.type === 'filter');
  const entryTriggers = items.filter((item) => item.type === 'entry_trigger');
  const signals = items.filter((item) => item.type === 'signal');

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <TraceSection title="Block Rules" items={blockRules} emptyMessage={emptyMessage} />
      <TraceSection title="Filters" items={filters} emptyMessage={emptyMessage} />
      <TraceSection title="Entry Triggers" items={entryTriggers} emptyMessage={emptyMessage} />
      <TraceSection title="Signals" items={signals} emptyMessage={emptyMessage} />
    </div>
  );
}

function TraceSection({
  title,
  items,
  emptyMessage,
}: {
  title: string;
  items: EvaluationTraceItem[];
  emptyMessage: string;
}) {
  return (
    <div className="rounded-xl border border-[#1E2433] bg-[#0A0B10] p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[#4B5563]">{title}</div>
      <div className="space-y-2">
        {items.map((item, index) => {
          const skip = classifySkip(item);
          const isBlockRule = item.type === 'block_rule';
          const blockOutcome = isBlockRule ? blockRuleOutcome(item) : null;
          // For block rules the green/purple palette tracks OK/TRIPPED
          // rather than PASS/FAIL — the underlying status is inverted.
          const cls = isBlockRule
            ? skip
              ? skip.cls
              : blockOutcome === 'OK'
                ? 'border-[#14532D]/40 bg-[#061E14] text-[#86EFAC]'
                : blockOutcome === 'TRIPPED'
                  ? 'border-[#6B21A8]/40 bg-[#1A0A2A] text-[#D8B4FE]'
                  : 'border-[#1E2433] bg-[#06080E] text-[#64748B]'
            : item.status === 'PASS'
              ? 'border-[#14532D]/40 bg-[#061E14] text-[#86EFAC]'
              : skip
                ? skip.cls
                : item.status === 'FAIL'
                  ? 'border-[#7F1D1D]/25 bg-[#150A0A] text-[#FCA5A5]'
                  : 'border-[#1E2433] bg-[#06080E] text-[#64748B]';

          const badgeLabel = skip
            ? skip.label
            : isBlockRule
              ? blockOutcome
              : item.status;

          const intentLine = isBlockRule && !skip
            ? blockOutcome === 'TRIPPED'
              ? 'condição disparou — ativo bloqueado'
              : blockOutcome === 'OK'
                ? 'condição não disparou — ativo livre'
                : null
            : null;

          // "Expected" makes no sense for block rules — the operator does
          // not *expect* the dangerous condition to trip. Use "Threshold"
          // there instead.
          const expectedLabel = isBlockRule ? 'Threshold' : 'Expected';

          return (
            <div key={index} className={`rounded-lg border px-3 py-2 text-xs ${cls}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold">{item.indicator}</span>
                <span className="font-mono text-[10px]">{badgeLabel}</span>
              </div>
              <div className="mt-1 text-[#CBD5E1]">{item.condition}</div>
              {intentLine && (
                <div className="mt-0.5 text-[10px] italic opacity-75">{intentLine}</div>
              )}
              <div className="mt-1 flex flex-wrap gap-3 text-[11px]">
                <span>
                  Current:{' '}
                  <span className="font-mono">
                    {skip && skip.currentText
                      ? <span className="italic opacity-60">{skip.currentText}</span>
                      : formatEvaluationTraceValue(item.current_value)}
                  </span>
                </span>
                <span>
                  {expectedLabel}:{' '}
                  <span className="font-mono">
                    {skip?.expectedOverride ?? (item.expected ?? '—')}
                  </span>
                </span>
              </div>
            </div>
          );
        })}
        {items.length === 0 && <div className="text-xs text-[#4B5563]">{emptyMessage}</div>}
      </div>
    </div>
  );
}
