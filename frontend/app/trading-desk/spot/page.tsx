'use client';

import { useTradingConfig } from '@/hooks/useTradingConfig';
import { useEngineStatus } from '@/hooks/useEngineStatus';
import EngineStatusBar from '@/components/trading-desk/shared/EngineStatusBar';
import ConfigSection from '@/components/trading-desk/shared/ConfigSection';
import SliderWithValue from '@/components/trading-desk/shared/SliderWithValue';
import SaveConfigBar from '@/components/trading-desk/shared/SaveConfigBar';

// ─── Inline Toggle ────────────────────────────────────────────────────────────

interface ToggleProps {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  id?: string;
}

function Toggle({ checked, onChange, disabled = false, id }: ToggleProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        width: '36px',
        height: '20px',
        borderRadius: '999px',
        padding: '2px',
        border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        background: checked ? 'var(--accent-primary)' : 'var(--bg-hover)',
        transition: 'background var(--transition-fast)',
        flexShrink: 0,
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <span
        style={{
          display: 'block',
          width: '16px',
          height: '16px',
          borderRadius: '50%',
          background: '#ffffff',
          transform: checked ? 'translateX(16px)' : 'translateX(0)',
          transition: 'transform var(--transition-fast)',
          flexShrink: 0,
        }}
      />
    </button>
  );
}

// ─── Inline helpers ───────────────────────────────────────────────────────────

const numInputStyle: React.CSSProperties = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border-default)',
  borderRadius: 'var(--radius-sm)',
  padding: '4px 8px',
  color: 'var(--text-primary)',
  fontFamily: 'var(--font-mono)',
  width: '80px',
  fontSize: '13px',
  outline: 'none',
};

function SubHeader({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="label"
      style={{
        marginTop: '20px',
        marginBottom: '10px',
        letterSpacing: '0.08em',
        fontSize: '11px',
        color: 'var(--text-tertiary)',
      }}
    >
      {children}
    </div>
  );
}

interface ToggleRowProps {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}

function ToggleRow({ label, description, checked, onChange, disabled }: ToggleRowProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '12px',
        padding: '8px 0',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ color: 'var(--text-primary)', fontSize: '14px', fontWeight: 500 }}>
          {label}
        </div>
        {description && (
          <div style={{ color: 'var(--text-tertiary)', fontSize: '12px', marginTop: '2px' }}>
            {description}
          </div>
        )}
      </div>
      <Toggle checked={checked} onChange={onChange} disabled={disabled} />
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SpotTradingPage() {
  const { config, updateConfig, saveConfig, resetConfig, isDirty, isSaving } =
    useTradingConfig('spot');

  const capital: number = config?.account?.capital ?? 10000;

  // ── Derived values (safe access) ─────────────────────────────────────────

  const scanInterval = config?.scanner?.interval ?? 60;
  const buyScoreThreshold = config?.buying?.score_threshold ?? 75;
  const strongBuyScore = config?.buying?.strong_buy_score ?? 85;
  const maxOpportunitiesPerScan = config?.scanner?.max_opportunities ?? 5;

  const perTradePct = config?.capital?.per_trade_pct ?? 10;
  const minPerTrade = config?.capital?.min_per_trade ?? 50;
  const maxPerTrade = config?.capital?.max_per_trade ?? 500;
  const capitalReservePct = config?.capital?.reserve_pct ?? 10;
  const maxCapitalInUsePct = config?.capital?.max_in_use_pct ?? 80;

  const maxPositionsTotal = config?.limits?.max_positions_total ?? 5;
  const maxPerAsset = config?.limits?.max_per_asset ?? 2;
  const maxExposurePerAssetPct = config?.limits?.max_exposure_per_asset_pct ?? 20;

  const orderType: 'market' | 'limit' = config?.orders?.type ?? 'market';
  const limitOrderTimeout = config?.orders?.limit_timeout_seconds ?? 30;
  const maxSlippagePct = config?.orders?.max_slippage_pct ?? 0.5;

  // Sell rules
  const takeProfitPct = config?.sell?.take_profit_pct ?? 2.0;
  const minProfitToSellPct = config?.sell?.min_profit_pct ?? 0.5;
  const safetyMarginPct = config?.sell?.safety_margin_pct ?? 0.1;

  const layerRanging = config?.sell?.layers?.ranging ?? true;
  const layerExhaustion = config?.sell?.layers?.exhaustion ?? true;
  const layerAiOpportunity = config?.sell?.layers?.ai_opportunity ?? false;
  const layerTargetHit = config?.sell?.layers?.target_hit ?? true;
  const layerAiTrailing = config?.sell?.layers?.ai_trailing ?? false;

  const showAiConfig = layerAiOpportunity || layerAiTrailing;
  const aiModel = config?.sell?.ai?.model ?? 'google/gemini-2.5-flash';
  const aiRateLimit = config?.sell?.ai?.rate_limit_seconds ?? 60;

  // Holding & Recovery
  const alertAfterUnderwaterHours = config?.holding?.alert_after_hours ?? 24;
  const repeatAlertEveryHours = config?.holding?.repeat_alert_hours ?? 12;
  const showOpportunityCost = config?.holding?.show_opportunity_cost ?? false;
  const showRecoveryPct = config?.holding?.show_recovery_pct ?? true;

  const dcaEnabled = config?.dca?.enabled ?? false;
  const dcaTriggerDropPct = config?.dca?.trigger_drop_pct ?? 5;
  const dcaMinScore = config?.dca?.min_score ?? 60;
  const dcaMaxLayers = config?.dca?.max_layers ?? 3;
  const dcaBaseAmount = config?.dca?.base_amount ?? 100;
  const dcaDecayFactor = config?.dca?.decay_factor ?? 0.7;
  const dcaMaxExposurePct = config?.dca?.max_total_exposure_pct ?? 30;
  const dcaRequireMacroSafe = config?.dca?.require_macro_not_risk_off ?? false;

  // Macro filter
  const macroEnabled = config?.macro?.enabled ?? false;
  const macroBlockOnStrongRiskOff = config?.macro?.block_on_strong_risk_off ?? true;
  const macroReduceBuysPct = config?.macro?.reduce_buys_pct ?? 50;
  const macroBtcGuard = config?.macro?.btc_correlation_guard ?? true;
  const macroBtcDumpThreshold1h = config?.macro?.btc_dump_threshold_1h_pct ?? -3.0;
  const macroBtcGuardAction: 'reduce' | 'alert' = config?.macro?.btc_guard_action ?? 'reduce';

  // Trailing stop
  const trailingMethod: 'fixed' | 'atr' = config?.trailing?.method ?? 'fixed';
  const trailingAtrPeriod = config?.trailing?.atr_period ?? 14;
  const trailingAtrMultiplier = config?.trailing?.atr_multiplier ?? 1.5;
  const trailingMarginFloor = config?.trailing?.margin_floor_pct ?? 0.3;
  const trailingMarginCeiling = config?.trailing?.margin_ceiling_pct ?? 1.5;
  const trailingTightenAbovePct = config?.trailing?.tighten_above_profit_pct ?? 5;
  const trailingTightenFactor = config?.trailing?.tighten_factor ?? 0.7;

  return (
    <div style={{ paddingBottom: '80px' }}>
      {/* Page Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1
          style={{
            fontSize: '24px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            margin: 0,
            fontFamily: 'var(--font-sans)',
          }}
        >
          Spot Trading
        </h1>
        <p
          style={{
            fontSize: '14px',
            color: 'var(--text-secondary)',
            margin: '4px 0 0',
            fontFamily: 'var(--font-sans)',
          }}
        >
          Configure and control the spot trading engine
        </p>
      </div>

      {/* Engine Status Bar */}
      <EngineStatusBar profile="spot" />

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '16px' }}>

        {/* ── SECTION: Scanner & Buying ───────────────────────────────────── */}
        <ConfigSection title="Scanner & Buying" icon="🔍" defaultOpen={true}>
          <SliderWithValue
            label="Scan Interval"
            value={scanInterval}
            onChange={(v) => updateConfig('scanner.interval', v)}
            min={10}
            max={300}
            step={5}
            unit="s"
            hint="How often to scan for new opportunities"
          />
          <SliderWithValue
            label="Buy Score Threshold"
            value={buyScoreThreshold}
            onChange={(v) => updateConfig('buying.score_threshold', v)}
            min={50}
            max={100}
            hint="Minimum score required to place a buy"
          />
          <SliderWithValue
            label="Strong Buy Score"
            value={strongBuyScore}
            onChange={(v) => updateConfig('buying.strong_buy_score', v)}
            min={60}
            max={100}
            hint="Score considered a strong conviction buy"
          />
          <SliderWithValue
            label="Max Opportunities per Scan"
            value={maxOpportunitiesPerScan}
            onChange={(v) => updateConfig('scanner.max_opportunities', v)}
            min={1}
            max={10}
            hint="Limit how many signals are acted on per scan cycle"
          />

          <SubHeader>CAPITAL ALLOCATION</SubHeader>

          <SliderWithValue
            label="Per Trade %"
            value={perTradePct}
            onChange={(v) => updateConfig('capital.per_trade_pct', v)}
            min={1}
            max={50}
            unit="%"
            hint={`≈ $${((perTradePct / 100) * capital).toFixed(0)} per trade`}
          />

          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', padding: '6px 0' }}>
            <div>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Min per Trade $
              </div>
              <input
                type="number"
                value={minPerTrade}
                onChange={(e) => updateConfig('capital.min_per_trade', Number(e.target.value))}
                style={numInputStyle}
                min={1}
              />
            </div>
            <div>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Max per Trade $
              </div>
              <input
                type="number"
                value={maxPerTrade}
                onChange={(e) => updateConfig('capital.max_per_trade', Number(e.target.value))}
                style={numInputStyle}
                min={1}
              />
            </div>
          </div>

          <SliderWithValue
            label="Capital Reserve %"
            value={capitalReservePct}
            onChange={(v) => updateConfig('capital.reserve_pct', v)}
            min={0}
            max={30}
            unit="%"
            hint="Always keep this portion of capital unused"
          />
          <SliderWithValue
            label="Max Capital in Use %"
            value={maxCapitalInUsePct}
            onChange={(v) => updateConfig('capital.max_in_use_pct', v)}
            min={30}
            max={95}
            unit="%"
            hint="Hard cap on total deployed capital"
          />

          <SubHeader>POSITION LIMITS</SubHeader>

          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', padding: '6px 0' }}>
            <div>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Max Positions Total
              </div>
              <input
                type="number"
                value={maxPositionsTotal}
                onChange={(e) =>
                  updateConfig('limits.max_positions_total', Number(e.target.value))
                }
                style={numInputStyle}
                min={1}
              />
            </div>
            <div>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Max per Asset
              </div>
              <input
                type="number"
                value={maxPerAsset}
                onChange={(e) => updateConfig('limits.max_per_asset', Number(e.target.value))}
                style={numInputStyle}
                min={1}
              />
            </div>
          </div>

          <SliderWithValue
            label="Max Exposure per Asset %"
            value={maxExposurePerAssetPct}
            onChange={(v) => updateConfig('limits.max_exposure_per_asset_pct', v)}
            min={5}
            max={50}
            unit="%"
            hint="Maximum portfolio share for a single asset"
          />

          <SubHeader>ORDER SETTINGS</SubHeader>

          <div style={{ padding: '6px 0' }}>
            <div
              style={{
                fontSize: '13px',
                color: 'var(--text-secondary)',
                marginBottom: '8px',
                fontFamily: 'var(--font-sans)',
              }}
            >
              Order Type
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {(['market', 'limit'] as const).map((type) => (
                <label
                  key={type}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    cursor: 'pointer',
                    fontSize: '14px',
                    color: orderType === type ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  <input
                    type="radio"
                    name="orderType"
                    value={type}
                    checked={orderType === type}
                    onChange={() => updateConfig('orders.type', type)}
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </label>
              ))}
            </div>
          </div>

          {orderType === 'limit' && (
            <div style={{ padding: '6px 0' }}>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Limit Order Timeout (seconds)
              </div>
              <input
                type="number"
                value={limitOrderTimeout}
                onChange={(e) =>
                  updateConfig('orders.limit_timeout_seconds', Number(e.target.value))
                }
                style={numInputStyle}
                min={5}
              />
            </div>
          )}

          <SliderWithValue
            label="Max Slippage %"
            value={maxSlippagePct}
            onChange={(v) => updateConfig('orders.max_slippage_pct', v)}
            min={0}
            max={2}
            step={0.01}
            unit="%"
            decimals={2}
            hint="Maximum acceptable slippage when filling orders"
          />
        </ConfigSection>

        {/* ── SECTION: Sell Rules ─────────────────────────────────────────── */}
        <ConfigSection title="Sell Rules" icon="📤" defaultOpen={true}>
          {/* Core Rule Banner */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              padding: '12px 14px',
              background: 'var(--color-warning-muted)',
              border: '1px solid var(--color-warning)',
              borderRadius: 'var(--radius-md)',
              marginBottom: '16px',
            }}
          >
            <span style={{ fontSize: '20px', flexShrink: 0 }}>🔒</span>
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: '13px',
                  fontWeight: 700,
                  color: 'var(--color-warning)',
                  fontFamily: 'var(--font-sans)',
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                }}
              >
                NEVER SELL AT LOSS
              </div>
              <div
                style={{
                  fontSize: '12px',
                  color: 'var(--text-secondary)',
                  marginTop: '2px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                This rule is permanently enforced and cannot be disabled.
              </div>
            </div>
            <Toggle checked={true} onChange={() => {}} disabled={true} />
          </div>

          <SliderWithValue
            label="Take Profit Target %"
            value={takeProfitPct}
            onChange={(v) => updateConfig('sell.take_profit_pct', v)}
            min={0.5}
            max={10}
            unit="%"
            decimals={2}
            hint="Target gain to trigger a sell evaluation"
          />
          <SliderWithValue
            label="Min Profit to Sell %"
            value={minProfitToSellPct}
            onChange={(v) => updateConfig('sell.min_profit_pct', v)}
            min={0.1}
            max={5}
            unit="%"
            decimals={2}
            hint="Absolute floor — position will not sell below this"
          />
          <SliderWithValue
            label="Safety Margin %"
            value={safetyMarginPct}
            onChange={(v) => updateConfig('sell.safety_margin_pct', v)}
            min={0}
            max={2}
            step={0.05}
            unit="%"
            decimals={2}
            hint="Buffer added above min profit to absorb fees"
          />

          <SubHeader>SELL FLOW LAYERS</SubHeader>

          <ToggleRow
            label="Ranging Detection"
            description="Market lateralized → sell if profit ≥ TP"
            checked={layerRanging}
            onChange={(v) => updateConfig('sell.layers.ranging', v)}
          />
          <ToggleRow
            label="Exhaustion Detection"
            description="Trend weakening signals"
            checked={layerExhaustion}
            onChange={(v) => updateConfig('sell.layers.exhaustion', v)}
          />
          <ToggleRow
            label="AI Opportunity"
            description="Consult AI model for EXTEND/SELL decision"
            checked={layerAiOpportunity}
            onChange={(v) => updateConfig('sell.layers.ai_opportunity', v)}
          />
          <ToggleRow
            label="Target Hit"
            description="Direct profit ≥ take profit"
            checked={layerTargetHit}
            onChange={(v) => updateConfig('sell.layers.target_hit', v)}
          />
          <ToggleRow
            label="AI Trailing"
            description="HWM trailing stop after AI Hold"
            checked={layerAiTrailing}
            onChange={(v) => updateConfig('sell.layers.ai_trailing', v)}
          />

          {showAiConfig && (
            <div
              style={{
                marginTop: '16px',
                padding: '14px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                display: 'flex',
                flexDirection: 'column',
                gap: '12px',
              }}
            >
              <div
                className="label"
                style={{ fontSize: '11px', color: 'var(--text-tertiary)', letterSpacing: '0.08em' }}
              >
                AI CONFIGURATION
              </div>

              <div>
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    marginBottom: '6px',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  AI Model
                </div>
                <select
                  value={aiModel}
                  onChange={(e) => updateConfig('sell.ai.model', e.target.value)}
                  style={{
                    ...numInputStyle,
                    width: 'auto',
                    minWidth: '220px',
                    paddingRight: '12px',
                  }}
                >
                  <option value="google/gemini-2.5-flash">google/gemini-2.5-flash</option>
                  <option value="google/gemini-2.0-flash">google/gemini-2.0-flash</option>
                  <option value="anthropic/claude-3-haiku">anthropic/claude-3-haiku</option>
                </select>
              </div>

              <div>
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    marginBottom: '6px',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  AI Rate Limit (seconds)
                </div>
                <input
                  type="number"
                  value={aiRateLimit}
                  onChange={(e) =>
                    updateConfig('sell.ai.rate_limit_seconds', Number(e.target.value))
                  }
                  style={numInputStyle}
                  min={10}
                />
              </div>
            </div>
          )}
        </ConfigSection>

        {/* ── SECTION: Holding & Recovery ────────────────────────────────── */}
        <ConfigSection title="Holding & Recovery" icon="⏳" defaultOpen={false}>
          <div
            style={{
              padding: '10px 14px',
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              marginBottom: '16px',
              fontSize: '13px',
              color: 'var(--text-secondary)',
              fontFamily: 'var(--font-sans)',
              lineHeight: 1.5,
            }}
          >
            Underwater positions are held indefinitely until the minimum profit target is reached.
          </div>

          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', padding: '6px 0' }}>
            <div>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Alert after underwater (hours)
              </div>
              <input
                type="number"
                value={alertAfterUnderwaterHours}
                onChange={(e) =>
                  updateConfig('holding.alert_after_hours', Number(e.target.value))
                }
                style={numInputStyle}
                min={1}
              />
            </div>
            <div>
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginBottom: '6px',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Repeat alert every (hours)
              </div>
              <input
                type="number"
                value={repeatAlertEveryHours}
                onChange={(e) =>
                  updateConfig('holding.repeat_alert_hours', Number(e.target.value))
                }
                style={numInputStyle}
                min={1}
              />
            </div>
          </div>

          <ToggleRow
            label="Show opportunity cost estimate"
            checked={showOpportunityCost}
            onChange={(v) => updateConfig('holding.show_opportunity_cost', v)}
          />
          <ToggleRow
            label="Show recovery % needed"
            checked={showRecoveryPct}
            onChange={(v) => updateConfig('holding.show_recovery_pct', v)}
          />

          <SubHeader>DCA (DOLLAR COST AVERAGE)</SubHeader>

          <ToggleRow
            label="Enable DCA"
            description="Accumulate additional position on significant drops"
            checked={dcaEnabled}
            onChange={(v) => updateConfig('dca.enabled', v)}
          />

          {dcaEnabled && (
            <div
              style={{
                marginTop: '12px',
                paddingLeft: '14px',
                borderLeft: '2px solid var(--accent-primary-border)',
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
              }}
            >
              <SliderWithValue
                label="Trigger after drop %"
                value={dcaTriggerDropPct}
                onChange={(v) => updateConfig('dca.trigger_drop_pct', v)}
                min={1}
                max={20}
                unit="%"
                hint="DCA kicks in when position drops this much"
              />
              <SliderWithValue
                label="Min score for DCA"
                value={dcaMinScore}
                onChange={(v) => updateConfig('dca.min_score', v)}
                min={50}
                max={100}
                hint="Asset must still score above this to DCA"
              />

              <div style={{ padding: '6px 0' }}>
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    marginBottom: '6px',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  Max DCA Layers
                </div>
                <input
                  type="number"
                  value={dcaMaxLayers}
                  onChange={(e) => updateConfig('dca.max_layers', Number(e.target.value))}
                  style={numInputStyle}
                  min={1}
                  max={5}
                />
              </div>

              <div style={{ padding: '6px 0' }}>
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    marginBottom: '6px',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  Base Amount $
                </div>
                <input
                  type="number"
                  value={dcaBaseAmount}
                  onChange={(e) => updateConfig('dca.base_amount', Number(e.target.value))}
                  style={numInputStyle}
                  min={1}
                />
              </div>

              <SliderWithValue
                label="Decay Factor"
                value={dcaDecayFactor}
                onChange={(v) => updateConfig('dca.decay_factor', v)}
                min={0.3}
                max={1.0}
                step={0.05}
                decimals={1}
                hint="Multiplier applied to each successive DCA layer amount"
              />
              <SliderWithValue
                label="Max Total Exposure %"
                value={dcaMaxExposurePct}
                onChange={(v) => updateConfig('dca.max_total_exposure_pct', v)}
                min={10}
                max={60}
                unit="%"
                hint="Cap on total capital committed via DCA for one position"
              />
              <ToggleRow
                label="Require macro ≠ risk_off"
                description="Pause DCA when macro regime is risk-off"
                checked={dcaRequireMacroSafe}
                onChange={(v) => updateConfig('dca.require_macro_not_risk_off', v)}
              />
            </div>
          )}
        </ConfigSection>

        {/* ── SECTION: Macro Filter ───────────────────────────────────────── */}
        <ConfigSection title="Macro Filter" icon="🌐" defaultOpen={false}>
          <ToggleRow
            label="Enable Macro Filter"
            description="Use macro regime to gate buy decisions"
            checked={macroEnabled}
            onChange={(v) => updateConfig('macro.enabled', v)}
          />
          <ToggleRow
            label="Block buys on Strong Risk-Off"
            description="No new positions when macro regime is strongly bearish"
            checked={macroBlockOnStrongRiskOff}
            onChange={(v) => updateConfig('macro.block_on_strong_risk_off', v)}
          />

          <SliderWithValue
            label="Reduce buys on Risk-Off %"
            value={macroReduceBuysPct}
            onChange={(v) => updateConfig('macro.reduce_buys_pct', v)}
            min={0}
            max={100}
            unit="%"
            hint="Scale back buy activity by this amount during risk-off regime"
          />

          <ToggleRow
            label="BTC Correlation Guard"
            description="Monitor BTC for sudden dumps that affect correlated assets"
            checked={macroBtcGuard}
            onChange={(v) => updateConfig('macro.btc_correlation_guard', v)}
          />

          <SliderWithValue
            label="BTC Dump Threshold 1h %"
            value={macroBtcDumpThreshold1h}
            onChange={(v) => updateConfig('macro.btc_dump_threshold_1h_pct', v)}
            min={-10}
            max={0}
            step={0.1}
            unit="%"
            decimals={1}
            hint="Trigger BTC guard when 1h change falls below this value"
            disabled={!macroBtcGuard}
          />

          <div style={{ padding: '6px 0' }}>
            <div
              style={{
                fontSize: '13px',
                color: 'var(--text-secondary)',
                marginBottom: '8px',
                fontFamily: 'var(--font-sans)',
              }}
            >
              BTC Guard Action
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {([
                { value: 'reduce', label: 'Reduce targets' },
                { value: 'alert', label: 'Alert only' },
              ] as const).map(({ value, label }) => (
                <label
                  key={value}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    cursor: macroBtcGuard ? 'pointer' : 'not-allowed',
                    fontSize: '14px',
                    color:
                      macroBtcGuardAction === value
                        ? 'var(--text-primary)'
                        : 'var(--text-secondary)',
                    fontFamily: 'var(--font-sans)',
                    opacity: macroBtcGuard ? 1 : 0.5,
                  }}
                >
                  <input
                    type="radio"
                    name="btcGuardAction"
                    value={value}
                    checked={macroBtcGuardAction === value}
                    disabled={!macroBtcGuard}
                    onChange={() => updateConfig('macro.btc_guard_action', value)}
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>
        </ConfigSection>

        {/* ── SECTION: Trailing Stop Config ──────────────────────────────── */}
        <ConfigSection title="Trailing Stop Config" icon="📉" defaultOpen={false}>
          <div style={{ padding: '6px 0' }}>
            <div
              style={{
                fontSize: '13px',
                color: 'var(--text-secondary)',
                marginBottom: '8px',
                fontFamily: 'var(--font-sans)',
              }}
            >
              Method
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {([
                { value: 'fixed', label: 'Fixed margins' },
                { value: 'atr', label: 'ATR-based' },
              ] as const).map(({ value, label }) => (
                <label
                  key={value}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    cursor: 'pointer',
                    fontSize: '14px',
                    color:
                      trailingMethod === value ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  <input
                    type="radio"
                    name="trailingMethod"
                    value={value}
                    checked={trailingMethod === value}
                    onChange={() => updateConfig('trailing.method', value)}
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          {trailingMethod === 'atr' && (
            <>
              <div style={{ padding: '6px 0' }}>
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    marginBottom: '6px',
                    fontFamily: 'var(--font-sans)',
                  }}
                >
                  ATR Period
                </div>
                <input
                  type="number"
                  value={trailingAtrPeriod}
                  onChange={(e) => updateConfig('trailing.atr_period', Number(e.target.value))}
                  style={numInputStyle}
                  min={2}
                />
              </div>
              <SliderWithValue
                label="ATR Multiplier"
                value={trailingAtrMultiplier}
                onChange={(v) => updateConfig('trailing.atr_multiplier', v)}
                min={0.5}
                max={3.0}
                step={0.1}
                decimals={1}
                hint="Scales the ATR value for the trailing distance"
              />
            </>
          )}

          <SliderWithValue
            label="Margin Floor %"
            value={trailingMarginFloor}
            onChange={(v) => updateConfig('trailing.margin_floor_pct', v)}
            min={0.1}
            max={2.0}
            step={0.05}
            unit="%"
            decimals={2}
            hint="Minimum trailing distance — stop cannot be tighter than this"
          />
          <SliderWithValue
            label="Margin Ceiling %"
            value={trailingMarginCeiling}
            onChange={(v) => updateConfig('trailing.margin_ceiling_pct', v)}
            min={0.5}
            max={5.0}
            step={0.1}
            unit="%"
            decimals={1}
            hint="Maximum trailing distance — stop cannot be wider than this"
          />
          <SliderWithValue
            label="Tighten above profit %"
            value={trailingTightenAbovePct}
            onChange={(v) => updateConfig('trailing.tighten_above_profit_pct', v)}
            min={1}
            max={20}
            unit="%"
            decimals={1}
            hint="Once profit exceeds this level, begin tightening the trail"
          />
          <SliderWithValue
            label="Tighten Factor"
            value={trailingTightenFactor}
            onChange={(v) => updateConfig('trailing.tighten_factor', v)}
            min={0.3}
            max={1.0}
            step={0.05}
            decimals={2}
            hint="Multiplier applied to trail margin as profit grows"
          />
        </ConfigSection>

      </div>

      {/* ── Save Config Bar ─────────────────────────────────────────────── */}
      <SaveConfigBar
        isDirty={isDirty}
        isSaving={isSaving}
        onSave={saveConfig}
        onReset={resetConfig}
      />
    </div>
  );
}
