'use client';

import { useTradingConfig } from '@/hooks/useTradingConfig';
import { EngineStatusBar } from '@/components/trading-desk/shared/EngineStatusBar';
import { ConfigSection } from '@/components/trading-desk/shared/ConfigSection';
import { SliderWithValue } from '@/components/trading-desk/shared/SliderWithValue';
import { SaveConfigBar } from '@/components/trading-desk/shared/SaveConfigBar';
import {
  Layers,
  DollarSign,
  ShieldAlert,
  Target,
  TrendingUp,
  Activity,
  Globe,
  AlertOctagon,
} from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────────────
// Inline reusable primitives
// ─────────────────────────────────────────────────────────────────────────────

function Toggle({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          cursor: 'pointer',
          gap: '12px',
        }}
      >
        <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
          {label}
        </span>
        <span
          onClick={() => onChange(!checked)}
          role="switch"
          aria-checked={checked}
          tabIndex={0}
          onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && onChange(!checked)}
          style={{
            position: 'relative',
            display: 'inline-flex',
            alignItems: 'center',
            width: '36px',
            height: '20px',
            borderRadius: '100px',
            background: checked ? 'var(--accent-primary)' : 'var(--bg-hover)',
            border: `1px solid ${checked ? 'var(--accent-primary)' : 'var(--border-default)'}`,
            transition: 'background var(--transition-fast), border-color var(--transition-fast)',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <span
            style={{
              position: 'absolute',
              left: checked ? '17px' : '2px',
              width: '14px',
              height: '14px',
              borderRadius: '50%',
              background: 'white',
              boxShadow: '0 1px 3px rgba(0,0,0,0.4)',
              transition: 'left var(--transition-fast)',
            }}
          />
        </span>
      </label>
      {hint && (
        <p style={{ fontSize: '11px', color: 'var(--text-tertiary)', margin: 0, lineHeight: 1.5 }}>
          {hint}
        </p>
      )}
    </div>
  );
}

function NumberInput({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  hint,
  width = 80,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  width?: number;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
          {label}
        </label>
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            if (!isNaN(v)) onChange(v);
          }}
          style={{
            width: `${width}px`,
            height: '32px',
            padding: '0 8px',
            background: 'var(--bg-input)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-sm)',
            fontFamily: 'var(--font-mono)',
            fontSize: '13px',
            fontWeight: 600,
            color: 'var(--text-primary)',
            textAlign: 'right',
            outline: 'none',
            MozAppearance: 'textfield',
          } as React.CSSProperties}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent-primary)';
            e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-primary-muted)';
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-default)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        />
      </div>
      {hint && (
        <p style={{ fontSize: '11px', color: 'var(--text-tertiary)', margin: 0, lineHeight: 1.5 }}>
          {hint}
        </p>
      )}
    </div>
  );
}

function Subsection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          paddingBottom: '8px',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <span
          style={{
            fontSize: '11px',
            fontWeight: 700,
            letterSpacing: '0.07em',
            textTransform: 'uppercase',
            color: 'var(--text-tertiary)',
          }}
        >
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

function InfoBanner({
  children,
  variant = 'accent',
}: {
  children: React.ReactNode;
  variant?: 'accent' | 'warning';
}) {
  const isWarning = variant === 'warning';
  return (
    <div
      style={{
        padding: '10px 14px',
        borderRadius: 'var(--radius-md)',
        background: isWarning ? 'rgba(251, 191, 36, 0.08)' : 'var(--accent-primary-muted)',
        border: `1px solid ${isWarning ? 'rgba(251, 191, 36, 0.25)' : 'var(--accent-primary-border)'}`,
        fontSize: '12px',
        fontWeight: 500,
        color: isWarning ? 'var(--color-warning)' : 'var(--accent-primary)',
        lineHeight: 1.5,
      }}
    >
      {children}
    </div>
  );
}

function SectionGrid({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
        gap: '20px',
      }}
    >
      {children}
    </div>
  );
}

function RadioGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
        {label}
      </span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            style={{
              padding: '5px 12px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '12px',
              fontWeight: 500,
              border: `1px solid ${value === opt ? 'var(--accent-primary)' : 'var(--border-default)'}`,
              background: value === opt ? 'var(--accent-primary-muted)' : 'var(--bg-input)',
              color: value === opt ? 'var(--accent-primary)' : 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
            }}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          height: '36px',
          padding: '0 10px',
          background: 'var(--bg-input)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-sm)',
          fontSize: '13px',
          color: 'var(--text-primary)',
          outline: 'none',
          cursor: 'pointer',
          appearance: 'none',
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%236B7280' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E")`,
          backgroundRepeat: 'no-repeat',
          backgroundPosition: 'right 10px center',
          paddingRight: '28px',
        }}
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Anti-Liquidation Diagram
// ─────────────────────────────────────────────────────────────────────────────

function AntiLiqDiagram({
  stopDist,
  liqBuffer,
  alertDist,
  critDist,
  tpDist = 10,
}: {
  stopDist: number;
  liqBuffer: number;
  alertDist: number;
  critDist: number;
  tpDist?: number;
}) {
  const totalRange = tpDist + stopDist + liqBuffer + alertDist + 2;
  const zones = [
    { label: 'TP Zone', pct: `+${tpDist.toFixed(1)}%`, color: 'var(--color-profit)', bg: 'var(--color-profit-muted)', icon: '' },
    { label: 'Entry', pct: '0%', color: 'var(--accent-primary)', bg: 'var(--accent-primary-muted)', icon: '' },
    { label: 'Stop Loss', pct: `-${stopDist.toFixed(1)}%`, color: 'var(--color-warning)', bg: 'var(--color-warning-muted)', icon: '' },
    { label: 'Alert Zone', pct: `-${alertDist.toFixed(1)}%`, color: '#FBBF24', bg: 'rgba(251,191,36,0.08)', icon: '⚠' },
    { label: 'Critical', pct: `-${critDist.toFixed(1)}%`, color: '#F97316', bg: 'rgba(249,115,22,0.08)', icon: '🔴' },
    { label: 'Liq Buffer', pct: `-${(stopDist + liqBuffer).toFixed(1)}%`, color: 'var(--text-tertiary)', bg: 'var(--bg-hover)', icon: '' },
    { label: 'Liquidation', pct: `-${(stopDist + liqBuffer + 2).toFixed(1)}%`, color: 'var(--color-loss)', bg: 'var(--color-loss-muted)', icon: '☠' },
  ];

  return (
    <div
      style={{
        padding: '14px',
        background: 'var(--bg-elevated)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-subtle)',
        display: 'flex',
        flexDirection: 'column',
        gap: '0',
      }}
    >
      <p
        style={{
          fontSize: '11px',
          fontWeight: 700,
          letterSpacing: '0.07em',
          textTransform: 'uppercase',
          color: 'var(--text-tertiary)',
          marginBottom: '12px',
        }}
      >
        Anti-Liquidation Zone Map
      </p>
      {zones.map((z, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '7px 10px',
            borderRadius: 'var(--radius-sm)',
            background: z.bg,
            marginBottom: i < zones.length - 1 ? '3px' : 0,
          }}
        >
          <div
            style={{
              width: '3px',
              height: '24px',
              borderRadius: '2px',
              background: z.color,
              flexShrink: 0,
            }}
          />
          <span
            style={{
              flex: 1,
              fontSize: '12px',
              fontWeight: 500,
              color: z.color,
            }}
          >
            {z.label}
          </span>
          {z.icon && (
            <span style={{ fontSize: '12px' }} aria-hidden="true">
              {z.icon}
            </span>
          )}
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '12px',
              fontWeight: 700,
              color: z.color,
            }}
          >
            {z.pct}
          </span>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Risk Preview Panel
// ─────────────────────────────────────────────────────────────────────────────

function RiskPreviewPanel({ config }: { config: Record<string, any> }) {
  const capital = 10000;
  const riskPct = config?.position_sizing?.risk_per_trade_pct ?? 1;
  const convictionRisk = config?.position_sizing?.conviction_risk_pct ?? 2;
  const dailyLossPct = config?.loss_limits?.daily_loss_limit_pct ?? 5;
  const weeklyLossPct = config?.loss_limits?.weekly_loss_limit_pct ?? 10;
  const maxCapitalPct = config?.position_sizing?.max_capital_deployed_pct ?? 70;
  const maxPositions = config?.position_sizing?.max_positions_total ?? 5;
  const maxCorrelated = config?.position_sizing?.max_correlated ?? 3;

  const riskDollar = (capital * riskPct) / 100;
  const convictionDollar = (capital * convictionRisk) / 100;
  const dailyLossDollar = (capital * dailyLossPct) / 100;
  const weeklyLossDollar = (capital * weeklyLossPct) / 100;
  const maxMarginDollar = (capital * maxCapitalPct) / 100;
  const tradesAtMaxRisk = riskDollar > 0 ? Math.floor(dailyLossDollar / riskDollar) : 0;

  const rows: [string, string][] = [
    ['Capital (est.)', `$${capital.toLocaleString()}`],
    ['Max risk / trade', `$${riskDollar.toFixed(0)} (${riskPct}%) — $${convictionDollar.toFixed(0)} conviction`],
    ['Max daily loss', `$${dailyLossDollar.toFixed(0)} (${tradesAtMaxRisk} trades at max risk)`],
    ['Max weekly loss', `$${weeklyLossDollar.toFixed(0)} (stops trading)`],
    ['Max margin in use', `$${maxMarginDollar.toFixed(0)} (${maxCapitalPct}%)`],
    ['Max positions', `${maxPositions} (max ${maxCorrelated} correlated)`],
  ];

  return (
    <div
      style={{
        padding: '16px',
        background: 'var(--bg-elevated)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-subtle)',
      }}
    >
      <p
        style={{
          fontSize: '11px',
          fontWeight: 700,
          letterSpacing: '0.07em',
          textTransform: 'uppercase',
          color: 'var(--text-tertiary)',
          marginBottom: '12px',
        }}
      >
        Risk Preview (live)
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {rows.map(([label, val]) => (
          <div
            key={label}
            style={{
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'space-between',
              gap: '12px',
            }}
          >
            <span className="label" style={{ flexShrink: 0 }}>
              {label}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                fontWeight: 600,
                color: 'var(--text-primary)',
                textAlign: 'right',
              }}
            >
              {val}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export default function FuturesTradingPage() {
  const { config, updateConfig, saveConfig, resetConfig, isDirty, isSaving, isLoading } =
    useTradingConfig('futures');

  // ── Macro weights sum ──────────────────────────────────────────────────────
  const macroWeights = config?.macro_gate?.regime_weights ?? {};
  const weightsTotal = Object.values(macroWeights).reduce(
    (sum: number, v: unknown) => sum + (typeof v === 'number' ? v : 0),
    0
  ) as number;

  if (isLoading) {
    return (
      <div style={{ padding: '32px 24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="skeleton"
            style={{ height: '80px', borderRadius: 'var(--radius-lg)' }}
          />
        ))}
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '20px',
        padding: '24px',
        paddingBottom: '100px',
        maxWidth: '1200px',
        margin: '0 auto',
      }}
    >
      {/* ── Page Header ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <h1
          style={{
            fontSize: '22px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            letterSpacing: '-0.02em',
            margin: 0,
          }}
        >
          Futures Trading
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0 }}>
          Institutional-grade futures engine with 5-layer scoring
        </p>
      </div>

      {/* ── Engine Status ─────────────────────────────────────────────────── */}
      <EngineStatusBar profile="futures" />

      {/* ── SECTION 1: Scanner & 5-Layer Scoring ─────────────────────────── */}
      <ConfigSection
        title="Scanner & 5-Layer Scoring"
        icon={<Layers size={16} />}
        defaultOpen={true}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <SectionGrid>
            <SliderWithValue
              label="Scan Interval"
              value={config?.scoring?.scan_interval_s ?? 60}
              onChange={(v) => updateConfig('scoring.scan_interval_s', v)}
              min={10}
              max={300}
              step={5}
              unit="s"
            />
            <SliderWithValue
              label="Min Total Score"
              value={config?.scoring?.min_total_score ?? 70}
              onChange={(v) => updateConfig('scoring.min_total_score', v)}
              min={50}
              max={100}
            />
            <SliderWithValue
              label="Min Layer Score"
              value={config?.scoring?.min_layer_score ?? 10}
              onChange={(v) => updateConfig('scoring.min_layer_score', v)}
              min={0}
              max={20}
            />
            <SliderWithValue
              label="L1 Hard Reject Below"
              value={config?.scoring?.l1_hard_reject_below ?? 5}
              onChange={(v) => updateConfig('scoring.l1_hard_reject_below', v)}
              min={0}
              max={20}
            />
            <SliderWithValue
              label="Max Opportunities per Scan"
              value={config?.scoring?.max_opportunities_per_scan ?? 3}
              onChange={(v) => updateConfig('scoring.max_opportunities_per_scan', v)}
              min={1}
              max={5}
            />
          </SectionGrid>

          <Subsection title="Direction">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <SectionGrid>
                <Toggle
                  label="Allow Long"
                  checked={config?.direction?.allow_long ?? true}
                  onChange={(v) => updateConfig('direction.allow_long', v)}
                />
                <Toggle
                  label="Allow Short"
                  checked={config?.direction?.allow_short ?? true}
                  onChange={(v) => updateConfig('direction.allow_short', v)}
                />
                <Toggle
                  label="Macro overrides direction"
                  checked={config?.direction?.macro_overrides ?? true}
                  onChange={(v) => updateConfig('direction.macro_overrides', v)}
                />
                <Toggle
                  label="Allow hedge (long+short same asset)"
                  checked={config?.direction?.allow_hedge ?? false}
                  onChange={(v) => updateConfig('direction.allow_hedge', v)}
                />
              </SectionGrid>
              <SelectField
                label="Direction Source"
                value={config?.direction?.source ?? 'L2 Market Structure'}
                options={['L2 Market Structure', 'L1 Momentum', 'Manual']}
                onChange={(v) => updateConfig('direction.source', v)}
              />
            </div>
          </Subsection>
        </div>
      </ConfigSection>

      {/* ── SECTION 2: Position Sizing ────────────────────────────────────── */}
      <ConfigSection
        title="Position Sizing"
        icon={<DollarSign size={16} />}
        defaultOpen={true}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <InfoBanner variant="accent">
            Method: Risk-Based — size = risk$ / stop distance
          </InfoBanner>

          <SectionGrid>
            <SliderWithValue
              label="Risk per Trade %"
              value={config?.position_sizing?.risk_per_trade_pct ?? 1}
              onChange={(v) => updateConfig('position_sizing.risk_per_trade_pct', v)}
              min={0.1}
              max={5}
              step={0.05}
              decimals={2}
              unit="%"
              hint="Base risk for valid setups"
            />
            <SliderWithValue
              label="Risk (conviction 90+) %"
              value={config?.position_sizing?.conviction_risk_pct ?? 2}
              onChange={(v) => updateConfig('position_sizing.conviction_risk_pct', v)}
              min={0.1}
              max={10}
              step={0.05}
              decimals={2}
              unit="%"
              hint="Higher risk for institutional-grade setups"
            />
          </SectionGrid>

          <Subsection title="Score → Size Multiplier">
            <div
              style={{
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-subtle)',
                overflow: 'hidden',
              }}
            >
              {[
                {
                  label: '90+ (Institutional)',
                  path: 'position_sizing.score_multiplier.institutional',
                  default: 1.5,
                },
                {
                  label: '80-89 (Strong)',
                  path: 'position_sizing.score_multiplier.strong',
                  default: 1.2,
                },
                {
                  label: '70-79 (Valid)',
                  path: 'position_sizing.score_multiplier.valid',
                  default: 1.0,
                },
              ].map((row, i, arr) => (
                <div
                  key={row.path}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 14px',
                    borderBottom: i < arr.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                    gap: '12px',
                  }}
                >
                  <span style={{ fontSize: '13px', color: 'var(--text-secondary)', flex: 1 }}>
                    {row.label}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>× </span>
                    <input
                      type="number"
                      value={
                        row.path.split('.').reduce((acc: any, k) => acc?.[k], config) ?? row.default
                      }
                      min={0.1}
                      max={5}
                      step={0.1}
                      onChange={(e) => {
                        const v = parseFloat(e.target.value);
                        if (!isNaN(v)) updateConfig(row.path, v);
                      }}
                      style={{
                        width: '72px',
                        height: '30px',
                        padding: '0 8px',
                        background: 'var(--bg-input)',
                        border: '1px solid var(--border-default)',
                        borderRadius: 'var(--radius-sm)',
                        fontFamily: 'var(--font-mono)',
                        fontSize: '13px',
                        fontWeight: 700,
                        color: 'var(--accent-primary)',
                        textAlign: 'right',
                        outline: 'none',
                        MozAppearance: 'textfield',
                      } as React.CSSProperties}
                      onFocus={(e) => {
                        e.currentTarget.style.borderColor = 'var(--accent-primary)';
                        e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-primary-muted)';
                      }}
                      onBlur={(e) => {
                        e.currentTarget.style.borderColor = 'var(--border-default)';
                        e.currentTarget.style.boxShadow = 'none';
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Subsection>

          <SectionGrid>
            <SliderWithValue
              label="Max Capital Deployed %"
              value={config?.position_sizing?.max_capital_deployed_pct ?? 70}
              onChange={(v) => updateConfig('position_sizing.max_capital_deployed_pct', v)}
              min={10}
              max={90}
              unit="%"
            />
          </SectionGrid>

          <SectionGrid>
            <NumberInput
              label="Max Positions Total"
              value={config?.position_sizing?.max_positions_total ?? 5}
              onChange={(v) => updateConfig('position_sizing.max_positions_total', v)}
              min={1}
              max={50}
            />
            <NumberInput
              label="Max per Asset"
              value={config?.position_sizing?.max_per_asset ?? 2}
              onChange={(v) => updateConfig('position_sizing.max_per_asset', v)}
              min={1}
              max={10}
            />
            <NumberInput
              label="Max Correlated"
              value={config?.position_sizing?.max_correlated ?? 3}
              onChange={(v) => updateConfig('position_sizing.max_correlated', v)}
              min={1}
              max={20}
            />
          </SectionGrid>

          <SliderWithValue
            label="Correlation Threshold"
            value={config?.position_sizing?.correlation_threshold ?? 0.7}
            onChange={(v) => updateConfig('position_sizing.correlation_threshold', v)}
            min={0}
            max={1}
            step={0.05}
            decimals={2}
            hint="Assets with correlation above this are considered correlated"
          />
        </div>
      </ConfigSection>

      {/* ── SECTION 3: Leverage & Anti-Liquidation ───────────────────────── */}
      <ConfigSection
        title="Leverage & Anti-Liquidation"
        icon={<ShieldAlert size={16} />}
        defaultOpen={true}
        badge="CALCULATED"
        badgeVariant="required"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <InfoBanner variant="warning">
            LEVERAGE IS CALCULATED, NEVER CHOSEN — leverage = risk$ / (stop_distance × position_size)
          </InfoBanner>

          <Subsection title="Max Leverage Caps">
            <div
              style={{
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-subtle)',
                overflow: 'hidden',
              }}
            >
              {[
                {
                  label: '90+ (Institutional)',
                  path: 'leverage.max_leverage.institutional',
                  default: 10,
                },
                {
                  label: '80-89 (Strong)',
                  path: 'leverage.max_leverage.strong',
                  default: 7,
                },
                {
                  label: '70-79 (Valid)',
                  path: 'leverage.max_leverage.valid',
                  default: 5,
                },
                {
                  label: 'Risk-Off macro',
                  path: 'leverage.max_leverage.risk_off',
                  default: 3,
                },
              ].map((row, i, arr) => (
                <div
                  key={row.path}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 14px',
                    borderBottom: i < arr.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                    gap: '12px',
                  }}
                >
                  <span style={{ fontSize: '13px', color: 'var(--text-secondary)', flex: 1 }}>
                    {row.label}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <input
                      type="number"
                      value={
                        row.path.split('.').reduce((acc: any, k) => acc?.[k], config) ?? row.default
                      }
                      min={1}
                      max={20}
                      step={1}
                      onChange={(e) => {
                        const v = parseFloat(e.target.value);
                        if (!isNaN(v)) updateConfig(row.path, v);
                      }}
                      style={{
                        width: '64px',
                        height: '30px',
                        padding: '0 8px',
                        background: 'var(--bg-input)',
                        border: '1px solid var(--border-default)',
                        borderRadius: 'var(--radius-sm)',
                        fontFamily: 'var(--font-mono)',
                        fontSize: '13px',
                        fontWeight: 700,
                        color: 'var(--color-warning)',
                        textAlign: 'right',
                        outline: 'none',
                        MozAppearance: 'textfield',
                      } as React.CSSProperties}
                      onFocus={(e) => {
                        e.currentTarget.style.borderColor = 'var(--accent-primary)';
                        e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-primary-muted)';
                      }}
                      onBlur={(e) => {
                        e.currentTarget.style.borderColor = 'var(--border-default)';
                        e.currentTarget.style.boxShadow = 'none';
                      }}
                    />
                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>x</span>
                  </div>
                </div>
              ))}
            </div>
          </Subsection>

          <AntiLiqDiagram
            stopDist={config?.anti_liquidation?.min_stop_to_liq_pct ?? 3}
            liqBuffer={config?.anti_liquidation?.liq_safety_buffer_pct ?? 2}
            alertDist={config?.anti_liquidation?.alert_zone_pct ?? 8}
            critDist={config?.anti_liquidation?.critical_zone_pct ?? 5}
          />

          <SectionGrid>
            <SliderWithValue
              label="Min Stop-to-Liq Distance %"
              value={config?.anti_liquidation?.min_stop_to_liq_pct ?? 3}
              onChange={(v) => updateConfig('anti_liquidation.min_stop_to_liq_pct', v)}
              min={0.5}
              max={10}
              step={0.1}
              decimals={1}
              unit="%"
            />
            <SliderWithValue
              label="Liq Safety Buffer %"
              value={config?.anti_liquidation?.liq_safety_buffer_pct ?? 2}
              onChange={(v) => updateConfig('anti_liquidation.liq_safety_buffer_pct', v)}
              min={0.5}
              max={10}
              step={0.1}
              decimals={1}
              unit="%"
            />
            <SliderWithValue
              label="Alert Zone Distance %"
              value={config?.anti_liquidation?.alert_zone_pct ?? 8}
              onChange={(v) => updateConfig('anti_liquidation.alert_zone_pct', v)}
              min={2}
              max={20}
              unit="%"
            />
            <SliderWithValue
              label="Critical Zone Distance %"
              value={config?.anti_liquidation?.critical_zone_pct ?? 5}
              onChange={(v) => updateConfig('anti_liquidation.critical_zone_pct', v)}
              min={1}
              max={10}
              unit="%"
            />
            <SliderWithValue
              label="Force Close At %"
              value={config?.anti_liquidation?.force_close_pct ?? 3}
              onChange={(v) => updateConfig('anti_liquidation.force_close_pct', v)}
              min={1}
              max={10}
              unit="%"
            />
          </SectionGrid>
          <Toggle
            label="Force close on critical"
            checked={config?.anti_liquidation?.force_close_on_critical ?? true}
            onChange={(v) => updateConfig('anti_liquidation.force_close_on_critical', v)}
          />
        </div>
      </ConfigSection>

      {/* ── SECTION 4: Stop Loss & Take Profit ───────────────────────────── */}
      <ConfigSection
        title="Stop Loss & Take Profit"
        icon={<Target size={16} />}
        defaultOpen={true}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <Subsection title="Stop Loss">
            <SelectField
              label="Method Priority"
              value={config?.stop_loss?.method ?? 'Structure → Liquidity → ATR'}
              options={['Structure → Liquidity → ATR', 'ATR → Structure', 'ATR only']}
              onChange={(v) => updateConfig('stop_loss.method', v)}
            />
            <SectionGrid>
              <SliderWithValue
                label="ATR Multiplier"
                value={config?.stop_loss?.atr_multiplier ?? 2.0}
                onChange={(v) => updateConfig('stop_loss.atr_multiplier', v)}
                min={0.5}
                max={5.0}
                step={0.1}
                decimals={1}
              />
              <SliderWithValue
                label="Max Stop Distance %"
                value={config?.stop_loss?.max_stop_distance_pct ?? 8}
                onChange={(v) => updateConfig('stop_loss.max_stop_distance_pct', v)}
                min={1}
                max={20}
                step={0.5}
                decimals={1}
                unit="%"
              />
              <SliderWithValue
                label="Min Stop Distance %"
                value={config?.stop_loss?.min_stop_distance_pct ?? 0.5}
                onChange={(v) => updateConfig('stop_loss.min_stop_distance_pct', v)}
                min={0.1}
                max={3.0}
                step={0.05}
                decimals={2}
                unit="%"
              />
            </SectionGrid>
            <RadioGroup
              label="Move SL to Breakeven"
              options={['At TP1', 'At TP2', 'Never']}
              value={config?.stop_loss?.breakeven_at ?? 'At TP1'}
              onChange={(v) => updateConfig('stop_loss.breakeven_at', v)}
            />
          </Subsection>

          <Subsection title="Take Profit">
            <SectionGrid>
              <SliderWithValue
                label="TP1 R:R"
                value={config?.take_profit?.tp1_rr ?? 1.5}
                onChange={(v) => updateConfig('take_profit.tp1_rr', v)}
                min={0.5}
                max={5.0}
                step={0.1}
                decimals={1}
              />
              <SliderWithValue
                label="TP1 Close %"
                value={config?.take_profit?.tp1_close_pct ?? 40}
                onChange={(v) => updateConfig('take_profit.tp1_close_pct', v)}
                min={10}
                max={90}
                unit="%"
              />
              <SliderWithValue
                label="TP2 R:R"
                value={config?.take_profit?.tp2_rr ?? 2.5}
                onChange={(v) => updateConfig('take_profit.tp2_rr', v)}
                min={1.0}
                max={8.0}
                step={0.1}
                decimals={1}
              />
              <SliderWithValue
                label="TP2 Close %"
                value={config?.take_profit?.tp2_close_pct ?? 40}
                onChange={(v) => updateConfig('take_profit.tp2_close_pct', v)}
                min={10}
                max={90}
                unit="%"
              />
              <SliderWithValue
                label="TP3 R:R"
                value={config?.take_profit?.tp3_rr ?? 4.0}
                onChange={(v) => updateConfig('take_profit.tp3_rr', v)}
                min={2.0}
                max={15.0}
                step={0.1}
                decimals={1}
              />
            </SectionGrid>
            <RadioGroup
              label="TP3 Method"
              options={['Limit order', 'Trailing stop']}
              value={config?.take_profit?.tp3_method ?? 'Trailing stop'}
              onChange={(v) => updateConfig('take_profit.tp3_method', v)}
            />
          </Subsection>

          <Subsection title="Volatility Adjustment">
            <SectionGrid>
              <SliderWithValue
                label="Squeeze TP Multiplier"
                value={config?.take_profit?.squeeze_tp_multiplier ?? 1.3}
                onChange={(v) => updateConfig('take_profit.squeeze_tp_multiplier', v)}
                min={1.0}
                max={2.0}
                step={0.05}
                decimals={2}
                hint="Extend TP targets during low-volatility squeeze"
              />
              <SliderWithValue
                label="Expanding TP Multiplier"
                value={config?.take_profit?.expanding_tp_multiplier ?? 0.85}
                onChange={(v) => updateConfig('take_profit.expanding_tp_multiplier', v)}
                min={0.5}
                max={1.5}
                step={0.05}
                decimals={2}
                hint="Tighten TP targets during high-volatility expansion"
              />
            </SectionGrid>
          </Subsection>
        </div>
      </ConfigSection>

      {/* ── SECTION 5: Trailing Stop ──────────────────────────────────────── */}
      <ConfigSection
        title="Trailing Stop"
        icon={<TrendingUp size={16} />}
        defaultOpen={false}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <SectionGrid>
            <RadioGroup
              label="Activate After"
              options={['TP1', 'TP2', 'TP3']}
              value={config?.trailing_stop?.activate_after ?? 'TP1'}
              onChange={(v) => updateConfig('trailing_stop.activate_after', v)}
            />
            <RadioGroup
              label="Method"
              options={['Fixed', 'ATR-based']}
              value={config?.trailing_stop?.method ?? 'ATR-based'}
              onChange={(v) => updateConfig('trailing_stop.method', v)}
            />
          </SectionGrid>
          <SectionGrid>
            <SliderWithValue
              label="ATR Multiplier"
              value={config?.trailing_stop?.atr_multiplier ?? 1.5}
              onChange={(v) => updateConfig('trailing_stop.atr_multiplier', v)}
              min={0.5}
              max={3.0}
              step={0.1}
              decimals={1}
            />
            <SliderWithValue
              label="Tighten Above Profit %"
              value={config?.trailing_stop?.tighten_above_profit_pct ?? 10}
              onChange={(v) => updateConfig('trailing_stop.tighten_above_profit_pct', v)}
              min={1}
              max={30}
              unit="%"
            />
            <SliderWithValue
              label="Tighten Factor"
              value={config?.trailing_stop?.tighten_factor ?? 0.7}
              onChange={(v) => updateConfig('trailing_stop.tighten_factor', v)}
              min={0.3}
              max={1.0}
              step={0.05}
              decimals={2}
              hint="Multiplier applied to trail distance when tightening"
            />
          </SectionGrid>
          <RadioGroup
            label="Floor"
            options={['Breakeven', 'Entry', 'Custom %']}
            value={config?.trailing_stop?.floor ?? 'Breakeven'}
            onChange={(v) => updateConfig('trailing_stop.floor', v)}
          />
        </div>
      </ConfigSection>

      {/* ── SECTION 6: Guards ─────────────────────────────────────────────── */}
      <ConfigSection
        title="Guards (Funding, OI, Emergency)"
        icon={<Activity size={16} />}
        defaultOpen={false}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
          <Subsection title="Funding Rate Guard">
            <Toggle
              label="Enabled"
              checked={config?.guards?.funding?.enabled ?? true}
              onChange={(v) => updateConfig('guards.funding.enabled', v)}
            />
            <SectionGrid>
              <SliderWithValue
                label="Max Funding for Long"
                value={config?.guards?.funding?.max_funding_long ?? 0.03}
                onChange={(v) => updateConfig('guards.funding.max_funding_long', v)}
                min={0}
                max={0.1}
                step={0.001}
                decimals={3}
                unit="%"
              />
              <SliderWithValue
                label="Min Funding for Short"
                value={config?.guards?.funding?.min_funding_short ?? -0.03}
                onChange={(v) => updateConfig('guards.funding.min_funding_short', v)}
                min={-0.1}
                max={0}
                step={0.001}
                decimals={3}
                unit="%"
              />
              <SliderWithValue
                label="Extreme Funding Rate"
                value={config?.guards?.funding?.extreme_funding_rate ?? 0.05}
                onChange={(v) => updateConfig('guards.funding.extreme_funding_rate', v)}
                min={0}
                max={0.15}
                step={0.005}
                decimals={3}
              />
              <SliderWithValue
                label="Size Reduction %"
                value={config?.guards?.funding?.size_reduction_pct ?? 50}
                onChange={(v) => updateConfig('guards.funding.size_reduction_pct', v)}
                min={0}
                max={100}
                unit="%"
              />
              <SliderWithValue
                label="Max Drain % of Profit"
                value={config?.guards?.funding?.max_drain_pct ?? 30}
                onChange={(v) => updateConfig('guards.funding.max_drain_pct', v)}
                min={0}
                max={100}
                unit="%"
              />
            </SectionGrid>
          </Subsection>

          <Subsection title="Open Interest Guard">
            <Toggle
              label="Enabled"
              checked={config?.guards?.oi?.enabled ?? true}
              onChange={(v) => updateConfig('guards.oi.enabled', v)}
            />
            <SectionGrid>
              <SliderWithValue
                label="Extreme OI Percentile"
                value={config?.guards?.oi?.extreme_oi_percentile ?? 90}
                onChange={(v) => updateConfig('guards.oi.extreme_oi_percentile', v)}
                min={50}
                max={99}
              />
              <SliderWithValue
                label="Size Reduction %"
                value={config?.guards?.oi?.size_reduction_pct ?? 50}
                onChange={(v) => updateConfig('guards.oi.size_reduction_pct', v)}
                min={0}
                max={100}
                unit="%"
              />
              <SliderWithValue
                label="Stop Tighten %"
                value={config?.guards?.oi?.stop_tighten_pct ?? 20}
                onChange={(v) => updateConfig('guards.oi.stop_tighten_pct', v)}
                min={0}
                max={50}
                unit="%"
              />
            </SectionGrid>
          </Subsection>

          <Subsection title="Emergency Exits">
            <Toggle
              label="Exit on macro shift to Strong Risk-Off"
              checked={config?.guards?.emergency?.exit_on_strong_risk_off ?? true}
              onChange={(v) => updateConfig('guards.emergency.exit_on_strong_risk_off', v)}
            />
            <SectionGrid>
              <SliderWithValue
                label="BTC Crash Threshold %"
                value={config?.guards?.emergency?.btc_crash_threshold_pct ?? -10}
                onChange={(v) => updateConfig('guards.emergency.btc_crash_threshold_pct', v)}
                min={-20}
                max={0}
                step={0.5}
                decimals={1}
                unit="%"
              />
              <SliderWithValue
                label="Funding Emergency Rate"
                value={config?.guards?.emergency?.funding_emergency_rate ?? 0.1}
                onChange={(v) => updateConfig('guards.emergency.funding_emergency_rate', v)}
                min={0}
                max={0.2}
                step={0.005}
                decimals={3}
              />
              <SliderWithValue
                label="Exchange Max Latency"
                value={config?.guards?.emergency?.max_latency_ms ?? 2000}
                onChange={(v) => updateConfig('guards.emergency.max_latency_ms', v)}
                min={500}
                max={10000}
                step={100}
                unit="ms"
              />
            </SectionGrid>
          </Subsection>
        </div>
      </ConfigSection>

      {/* ── SECTION 7: Macro Gate ─────────────────────────────────────────── */}
      <ConfigSection
        title="Macro Gate"
        icon={<Globe size={16} />}
        defaultOpen={false}
        badge="REQUIRED"
        badgeVariant="required"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <Toggle
              label="Enable Macro Gate"
              checked={config?.macro_gate?.enabled ?? true}
              onChange={(v) => updateConfig('macro_gate.enabled', v)}
            />
            {!(config?.macro_gate?.enabled ?? true) && (
              <div
                style={{
                  padding: '8px 12px',
                  background: 'var(--color-loss-muted)',
                  border: '1px solid rgba(239,68,68,0.25)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '12px',
                  color: 'var(--color-loss)',
                  fontWeight: 500,
                }}
              >
                Required for futures trading — enabling macro gate significantly reduces liquidation risk
              </div>
            )}
          </div>

          <SliderWithValue
            label="Update Interval"
            value={config?.macro_gate?.update_interval_min ?? 15}
            onChange={(v) => updateConfig('macro_gate.update_interval_min', v)}
            min={5}
            max={120}
            step={5}
            unit="min"
          />

          <Subsection title="Regime Weights">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {[
                { label: 'BTC Trend', path: 'macro_gate.regime_weights.btc_trend' },
                { label: 'DXY Direction', path: 'macro_gate.regime_weights.dxy_direction' },
                { label: 'Funding Market', path: 'macro_gate.regime_weights.funding_market' },
                {
                  label: 'Liquidation Pressure',
                  path: 'macro_gate.regime_weights.liquidation_pressure',
                },
                { label: 'Stablecoin Flow', path: 'macro_gate.regime_weights.stablecoin_flow' },
                { label: 'VIX', path: 'macro_gate.regime_weights.vix' },
              ].map((w) => (
                <SliderWithValue
                  key={w.path}
                  label={w.label}
                  value={w.path.split('.').reduce((acc: any, k) => acc?.[k], config) ?? 15}
                  onChange={(v) => updateConfig(w.path, v)}
                  min={0}
                  max={100}
                />
              ))}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  gap: '8px',
                  padding: '8px 12px',
                  borderRadius: 'var(--radius-sm)',
                  background: weightsTotal === 100 ? 'var(--color-profit-muted)' : 'var(--color-loss-muted)',
                  border: `1px solid ${weightsTotal === 100 ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
                }}
              >
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  Weights total:
                </span>
                <span
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '14px',
                    fontWeight: 700,
                    color: weightsTotal === 100 ? 'var(--color-profit)' : 'var(--color-loss)',
                  }}
                >
                  {weightsTotal}
                </span>
                <span
                  style={{
                    fontSize: '12px',
                    color: weightsTotal === 100 ? 'var(--color-profit)' : 'var(--color-loss)',
                  }}
                >
                  / 100
                </span>
                {weightsTotal !== 100 && (
                  <span style={{ fontSize: '11px', color: 'var(--color-loss)' }}>
                    — must equal 100
                  </span>
                )}
              </div>
            </div>
          </Subsection>

          <Subsection title="Thresholds">
            <div
              style={{
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-subtle)',
                overflow: 'hidden',
              }}
            >
              {[
                {
                  label: 'Strong Risk-On >',
                  path: 'macro_gate.thresholds.strong_risk_on',
                  default: 80,
                  color: 'var(--color-profit)',
                },
                {
                  label: 'Risk-On >',
                  path: 'macro_gate.thresholds.risk_on',
                  default: 60,
                  color: '#6EE7B7',
                },
                {
                  label: 'Neutral >',
                  path: 'macro_gate.thresholds.neutral',
                  default: 40,
                  color: 'var(--text-secondary)',
                },
                {
                  label: 'Risk-Off >',
                  path: 'macro_gate.thresholds.risk_off',
                  default: 20,
                  color: 'var(--color-warning)',
                },
              ].map((row, i, arr) => (
                <div
                  key={row.path}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 14px',
                    borderBottom: i < arr.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                    gap: '12px',
                  }}
                >
                  <span style={{ fontSize: '13px', color: row.color, flex: 1 }}>{row.label}</span>
                  <input
                    type="number"
                    value={
                      row.path.split('.').reduce((acc: any, k) => acc?.[k], config) ?? row.default
                    }
                    min={0}
                    max={100}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!isNaN(v)) updateConfig(row.path, v);
                    }}
                    style={{
                      width: '72px',
                      height: '30px',
                      padding: '0 8px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border-default)',
                      borderRadius: 'var(--radius-sm)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '13px',
                      fontWeight: 700,
                      color: row.color,
                      textAlign: 'right',
                      outline: 'none',
                      MozAppearance: 'textfield',
                    } as React.CSSProperties}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = 'var(--accent-primary)';
                      e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-primary-muted)';
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = 'var(--border-default)';
                      e.currentTarget.style.boxShadow = 'none';
                    }}
                  />
                </div>
              ))}
              <div
                style={{
                  padding: '8px 14px',
                  fontSize: '11px',
                  color: 'var(--text-tertiary)',
                  fontStyle: 'italic',
                  borderTop: '1px solid var(--border-subtle)',
                  background: 'var(--bg-hover)',
                }}
              >
                (Below {config?.macro_gate?.thresholds?.risk_off ?? 20} = Strong Risk-Off — trading halted)
              </div>
            </div>
          </Subsection>

          <SectionGrid>
            <SliderWithValue
              label="Neutral Size Reduction %"
              value={config?.macro_gate?.neutral_size_reduction_pct ?? 50}
              onChange={(v) => updateConfig('macro_gate.neutral_size_reduction_pct', v)}
              min={0}
              max={100}
              unit="%"
            />
            <SliderWithValue
              label="Pre-Event Buffer"
              value={config?.macro_gate?.pre_event_buffer_hours ?? 4}
              onChange={(v) => updateConfig('macro_gate.pre_event_buffer_hours', v)}
              min={0}
              max={24}
              unit="h"
            />
            <SliderWithValue
              label="Pre-Event Size Cut %"
              value={config?.macro_gate?.pre_event_size_cut_pct ?? 50}
              onChange={(v) => updateConfig('macro_gate.pre_event_size_cut_pct', v)}
              min={0}
              max={100}
              unit="%"
            />
          </SectionGrid>
        </div>
      </ConfigSection>

      {/* ── SECTION 8: Loss Limits & Circuit Breaker ──────────────────────── */}
      <ConfigSection
        title="Loss Limits & Circuit Breaker"
        icon={<AlertOctagon size={16} />}
        defaultOpen={false}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <SectionGrid>
            <SliderWithValue
              label="Daily Loss Limit %"
              value={config?.loss_limits?.daily_loss_limit_pct ?? 5}
              onChange={(v) => updateConfig('loss_limits.daily_loss_limit_pct', v)}
              min={0.5}
              max={20}
              step={0.5}
              decimals={1}
              unit="%"
              hint={`≈ $${((10000 * (config?.loss_limits?.daily_loss_limit_pct ?? 5)) / 100).toFixed(0)} on $10k capital`}
            />
            <SliderWithValue
              label="Weekly Loss Limit %"
              value={config?.loss_limits?.weekly_loss_limit_pct ?? 10}
              onChange={(v) => updateConfig('loss_limits.weekly_loss_limit_pct', v)}
              min={0.5}
              max={30}
              step={0.5}
              decimals={1}
              unit="%"
              hint={`≈ $${((10000 * (config?.loss_limits?.weekly_loss_limit_pct ?? 10)) / 100).toFixed(0)} on $10k capital`}
            />
            <SliderWithValue
              label="Weekly Loss → Size Cut %"
              value={config?.loss_limits?.weekly_loss_size_cut_pct ?? 50}
              onChange={(v) => updateConfig('loss_limits.weekly_loss_size_cut_pct', v)}
              min={0}
              max={100}
              unit="%"
              hint="Cut position sizes by this % after hitting weekly loss threshold"
            />
          </SectionGrid>

          <SectionGrid>
            <NumberInput
              label="Circuit Breaker After N Losses"
              value={config?.loss_limits?.circuit_breaker_losses ?? 3}
              onChange={(v) => updateConfig('loss_limits.circuit_breaker_losses', v)}
              min={1}
              max={10}
              hint="Consecutive losing trades before pausing engine"
            />
            <NumberInput
              label="Pause Duration (minutes)"
              value={config?.loss_limits?.pause_duration_min ?? 60}
              onChange={(v) => updateConfig('loss_limits.pause_duration_min', v)}
              min={5}
              max={1440}
              step={5}
              width={100}
              hint="Engine pause time after circuit breaker triggers"
            />
          </SectionGrid>

          <RiskPreviewPanel config={config} />
        </div>
      </ConfigSection>

      {/* ── Save Bar ──────────────────────────────────────────────────────── */}
      <SaveConfigBar
        isDirty={isDirty}
        isSaving={isSaving}
        onSave={saveConfig}
        onReset={resetConfig}
      />
    </div>
  );
}
