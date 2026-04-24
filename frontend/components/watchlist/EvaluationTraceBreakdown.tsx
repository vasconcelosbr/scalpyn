'use client';

export interface EvaluationTraceItem {
  type: 'filter' | 'block_rule' | 'entry_trigger' | 'signal';
  indicator: string;
  condition: string;
  expected?: string | null;
  current_value?: unknown;
  status: 'PASS' | 'FAIL' | 'SKIPPED';
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
  emptyMessage = 'No profile rules configured.',
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
          const noData = item.status === 'FAIL' && item.current_value == null;
          const cls =
            item.status === 'PASS'
              ? 'border-[#14532D]/40 bg-[#061E14] text-[#86EFAC]'
              : noData
                ? 'border-[#78350F]/40 bg-[#1A1205] text-[#FCD34D]'
                : item.status === 'FAIL'
                  ? item.type === 'block_rule'
                    ? 'border-[#6B21A8]/40 bg-[#1A0A2A] text-[#D8B4FE]'
                    : item.type === 'entry_trigger'
                      ? 'border-[#1E40AF]/40 bg-[#060E28] text-[#93C5FD]'
                      : item.type === 'signal'
                        ? 'border-[#78350F]/40 bg-[#1C1206] text-[#FCD34D]'
                        : 'border-[#7F1D1D]/25 bg-[#150A0A] text-[#FCA5A5]'
                  : 'border-[#1E2433] bg-[#06080E] text-[#64748B]';

          return (
            <div key={index} className={`rounded-lg border px-3 py-2 text-xs ${cls}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold">{item.indicator}</span>
                <span className="font-mono text-[10px]">
                  {noData ? 'SEM DADOS' : item.status}
                </span>
              </div>
              <div className="mt-1 text-[#CBD5E1]">{item.condition}</div>
              <div className="mt-1 flex flex-wrap gap-3 text-[11px]">
                <span>
                  Current:{' '}
                  <span className="font-mono">
                    {noData ? <span className="italic opacity-60">aguardando coleta</span> : formatEvaluationTraceValue(item.current_value)}
                  </span>
                </span>
                <span>Expected: <span className="font-mono">{item.expected ?? '—'}</span></span>
              </div>
            </div>
          );
        })}
        {items.length === 0 && <div className="text-xs text-[#4B5563]">{emptyMessage}</div>}
      </div>
    </div>
  );
}
