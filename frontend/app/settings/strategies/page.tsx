"use client";

import { useState, useEffect } from "react";
import {
  Save, RefreshCw, Brain, ChevronDown, ChevronUp, TrendingDown,
  Shield, Activity, Bot, Zap, Crosshair, Radar, ScanLine,
  ShoppingCart, AlertTriangle, BarChart3, Gauge,
} from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

// ─── Type Definitions ─────────────────────────────────────────────────────────

interface ScannerConfig {
  scan_interval_seconds: number;
  universe_source: string;
  buy_threshold_score: number;
  strong_buy_threshold: number;
  max_opportunities_per_scan: number;
  symbol_cooldown_seconds: number;
  global_cooldown_after_n_buys: number;
}

interface BuyingConfig {
  capital_per_trade_pct: number;
  capital_per_trade_min_usdt: number;
  capital_reserve_pct: number;
  max_capital_in_use_pct: number;
  max_positions_total: number;
  max_positions_per_asset: number;
  max_exposure_per_asset_pct: number;
  order_type: "market" | "limit";
  limit_order_timeout_seconds: number;
}

interface SellingConfig {
  take_profit_pct: number;
  min_profit_pct: number;
  never_sell_at_loss: boolean;
  safety_margin_above_entry_pct: number;
  enable_ai_consultation: boolean;
  ai_rate_limit_seconds: number;
  ai_model: string;
}

interface MeanReversionConfig {
  enabled: boolean;
  rsi_overbought: number;
  zscore_threshold: number;
  bollinger_deviation: number;
  volume_decline_pct: number;
}

interface MomentumExitConfig {
  enabled: boolean;
  adx_min: number;
  bb_width_threshold: number;
  volume_spike_multiplier: number;
}

interface AIConsultationConfig {
  enabled: boolean;
  trigger_profit_pct: number;
}

interface TrailingConfig {
  enabled: boolean;
  hwm_trail_pct: number;
  activation_profit_pct: number;
}

interface KillSwitchConfig {
  enabled: boolean;
  atr_stop_multiplier: number;
  max_drawdown_from_hwm_pct: number;
}

interface TargetConfig {
  volatility_filter_enabled: boolean;
  min_volume_multiplier: number;
  liquidity_check_enabled: boolean;
}

interface SellFlowConfig {
  mean_reversion: MeanReversionConfig;
  momentum_exit: MomentumExitConfig;
  ai_consultation: AIConsultationConfig;
  trailing: TrailingConfig;
  kill_switch: KillSwitchConfig;
  target: TargetConfig;
}

interface SpotEngineConfig {
  scanner: ScannerConfig;
  buying: BuyingConfig;
  selling: SellingConfig;
  sell_flow: SellFlowConfig;
}

interface Strategy {
  id: string;
  name: string;
  enabled: boolean;
  params: Record<string, number>;
}

// ─── Defaults ─────────────────────────────────────────────────────────────────

const DEFAULT_SCANNER: ScannerConfig = {
  scan_interval_seconds: 30,
  universe_source: "dynamic",
  buy_threshold_score: 75.0,
  strong_buy_threshold: 85.0,
  max_opportunities_per_scan: 3,
  symbol_cooldown_seconds: 300,
  global_cooldown_after_n_buys: 0,
};

const DEFAULT_BUYING: BuyingConfig = {
  capital_per_trade_pct: 10.0,
  capital_per_trade_min_usdt: 20.0,
  capital_reserve_pct: 10.0,
  max_capital_in_use_pct: 80.0,
  max_positions_total: 20,
  max_positions_per_asset: 5,
  max_exposure_per_asset_pct: 25.0,
  order_type: "market",
  limit_order_timeout_seconds: 120,
};

const DEFAULT_SELLING: SellingConfig = {
  take_profit_pct: 1.5,
  min_profit_pct: 0.5,
  never_sell_at_loss: true,
  safety_margin_above_entry_pct: 0.3,
  enable_ai_consultation: false,
  ai_rate_limit_seconds: 60,
  ai_model: "google/gemini-2.5-flash",
};

const DEFAULT_SELL_FLOW: SellFlowConfig = {
  mean_reversion: { enabled: true, rsi_overbought: 72.0, zscore_threshold: 2.0, bollinger_deviation: 2.0, volume_decline_pct: 20.0 },
  momentum_exit: { enabled: true, adx_min: 18.0, bb_width_threshold: 0.03, volume_spike_multiplier: 2.0 },
  ai_consultation: { enabled: false, trigger_profit_pct: 1.0 },
  trailing: { enabled: false, hwm_trail_pct: 0.5, activation_profit_pct: 2.0 },
  kill_switch: { enabled: true, atr_stop_multiplier: 2.0, max_drawdown_from_hwm_pct: 5.0 },
  target: { volatility_filter_enabled: true, min_volume_multiplier: 0.8, liquidity_check_enabled: true },
};

const DEFAULT_SE: SpotEngineConfig = {
  scanner: DEFAULT_SCANNER,
  buying: DEFAULT_BUYING,
  selling: DEFAULT_SELLING,
  sell_flow: DEFAULT_SELL_FLOW,
};

// ─── Shared Components ────────────────────────────────────────────────────────

function Toggle({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  return (
    <div className={`toggle ${active ? "active" : ""}`} onClick={onToggle} style={{ flexShrink: 0 }}>
      <div className="knob" />
    </div>
  );
}

function SliderField({
  label, value, min, max, step, suffix, caption, onChange,
}: {
  label: string; value: number; min: number; max: number; step: number;
  suffix?: string; caption?: string; onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between items-center">
        <label className="text-[12px] text-[var(--text-secondary)] font-medium">{label}</label>
        <span className="text-[12px] font-mono text-[var(--accent-primary)] font-semibold tabular-nums">
          {value}{suffix}
        </span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-[var(--accent-primary)] bg-[var(--border-default)]"
      />
      <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
        <span>{min}{suffix}</span>
        <span>{max}{suffix}</span>
      </div>
      {caption && <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">{caption}</p>}
    </div>
  );
}

function NumericField({ label, value, suffix, caption, onChange, step = 1, min, max }: {
  label: string; value: number; suffix?: string; caption?: string;
  onChange: (v: number) => void; step?: number; min?: number; max?: number;
}) {
  return (
    <div className="space-y-1">
      <label className="text-[12px] text-[var(--text-secondary)] font-medium block">{label}</label>
      <div className="input-group">
        <input type="number" className="input numeric" value={value} step={step}
          min={min} max={max}
          onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        />
        {suffix && <span className="suffix">{suffix}</span>}
      </div>
      {caption && <p className="text-[11px] text-[var(--text-tertiary)] mt-1">{caption}</p>}
    </div>
  );
}

function SectionHeader({ icon, title, subtitle, badge }: {
  icon: React.ReactNode; title: string; subtitle?: string; badge?: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 mb-4">
      <div className="p-2 rounded-[var(--radius-sm)] bg-[var(--accent-primary-muted)] text-[var(--accent-primary)] mt-0.5">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">{title}</h2>
          {badge}
        </div>
        {subtitle && <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">{subtitle}</p>}
      </div>
    </div>
  );
}

// ─── Layer Card ───────────────────────────────────────────────────────────────

function LayerCard({
  layer, label, description, icon, color, active, hasToggle, onToggle,
  children,
}: {
  layer: number; label: string; description: string; icon: React.ReactNode;
  color: string; active: boolean; hasToggle: boolean; onToggle?: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className={`card transition-all ${active ? "border-[var(--accent-primary-border)]" : ""}`}
      style={{ borderColor: active ? `${color}40` : undefined }}>
      <div className="p-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2.5 flex-1 min-w-0">
            <div className="p-2 rounded-[var(--radius-sm)] flex-shrink-0"
              style={{ background: `${color}18`, color }}>
              {icon}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-mono text-[var(--text-tertiary)]">Layer {layer}</span>
                <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
                  style={{
                    background: active ? `${color}18` : "var(--bg-elevated)",
                    color: active ? color : "var(--text-tertiary)",
                  }}>
                  {active ? "ON" : "OFF"}
                </span>
              </div>
              <h3 className="font-semibold text-[14px] text-[var(--text-primary)] truncate">{label}</h3>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {hasToggle && onToggle && <Toggle active={active} onToggle={onToggle} />}
            <button className="btn-ghost p-1" onClick={() => setOpen(!open)}>
              {open ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          </div>
        </div>
        {!open && (
          <p className="text-[12px] text-[var(--text-secondary)] mt-2 ml-11">{description}</p>
        )}
        {open && (
          <div className="mt-4 pt-4 border-t border-[var(--border-subtle)] space-y-5">
            <p className="text-[12px] text-[var(--text-secondary)]">{description}</p>
            {children}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function StrategySettings() {
  const { config: stratConfig, updateConfig: updateStratConfig, isLoading: stratLoading } = useConfig("strategy");
  const { config: seConfig, updateConfig: updateSeConfig, isLoading: seLoading } = useConfig("spot_engine");

  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [expandedStrat, setExpandedStrat] = useState<string | null>(null);
  const [se, setSe] = useState<SpotEngineConfig>(DEFAULT_SE);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (stratConfig?.strategies) setStrategies(stratConfig.strategies as Strategy[]);
  }, [stratConfig]);

  useEffect(() => {
    if (!seConfig || Object.keys(seConfig).length === 0) return;
    const loaded = seConfig as Partial<SpotEngineConfig>;
    setSe((prev) => {
      const next = { ...prev, ...loaded };
      if (loaded.scanner)       next.scanner       = { ...prev.scanner, ...loaded.scanner };
      if (loaded.buying)        next.buying        = { ...prev.buying, ...loaded.buying };
      if (loaded.selling)       next.selling       = { ...prev.selling, ...loaded.selling };
      if (loaded.sell_flow) {
        const lf = loaded.sell_flow as Partial<SellFlowConfig>;
        next.sell_flow = {
          mean_reversion: { ...prev.sell_flow.mean_reversion,  ...(lf.mean_reversion  ?? {}) },
          momentum_exit:  { ...prev.sell_flow.momentum_exit,   ...(lf.momentum_exit   ?? {}) },
          ai_consultation:{ ...prev.sell_flow.ai_consultation, ...(lf.ai_consultation ?? {}) },
          trailing:       { ...prev.sell_flow.trailing,        ...(lf.trailing        ?? {}) },
          kill_switch:    { ...prev.sell_flow.kill_switch,     ...(lf.kill_switch     ?? {}) },
          target:         { ...prev.sell_flow.target,          ...(lf.target          ?? {}) },
        };
      }
      return next;
    });
  }, [seConfig]);

  const setScanner   = (u: Partial<ScannerConfig>) => setSe(p => ({ ...p, scanner: { ...p.scanner, ...u } }));
  const setBuying    = (u: Partial<BuyingConfig>)  => setSe(p => ({ ...p, buying: { ...p.buying, ...u } }));
  const setSelling   = (u: Partial<SellingConfig>) => setSe(p => ({ ...p, selling: { ...p.selling, ...u } }));
  const setLayer     = <K extends keyof SellFlowConfig>(k: K, u: Partial<SellFlowConfig[K]>) =>
    setSe(p => ({ ...p, sell_flow: { ...p.sell_flow, [k]: { ...p.sell_flow[k], ...u } } }));

  const handleSave = async () => {
    setSaving(true); setSaved(false);
    try {
      await Promise.all([
        updateStratConfig({ strategies }),
        updateSeConfig(se),
      ]);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) { console.error(e); }
    setSaving(false);
  };

  if (stratLoading || seLoading) {
    return (
      <div className="p-8 space-y-4">
        {[1, 2, 3].map(i => <div key={i} className="skeleton h-40 w-full rounded-xl" />)}
      </div>
    );
  }

  const { scanner, buying, selling, sell_flow: sf } = se;

  return (
    <div className="space-y-8">
      {/* ── Header ── */}
      <div className="flex justify-between items-start gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Strategies Module</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Pipeline completo: Scanner → Compra → 5 camadas de saída. Zero hardcode.
          </p>
        </div>
        <button onClick={handleSave} disabled={saving} className={`btn ${saved ? "btn-success" : "btn-primary"} flex-shrink-0`}>
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Salvando..." : saved ? "Salvo!" : "Salvar Tudo"}
        </button>
      </div>

      {/* ── Scanner Config ── */}
      <section className="card">
        <div className="p-5">
          <SectionHeader
            icon={<ScanLine className="w-4 h-4" />}
            title="Scanner — Entrada"
            subtitle="Define quando e como o engine busca oportunidades de compra no universo de ativos."
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
            <SliderField
              label="Score Mínimo para Compra"
              value={scanner.buy_threshold_score}
              min={40} max={99} step={0.5} suffix=""
              caption="Candidatos abaixo desse score são ignorados pelo buy engine."
              onChange={(v) => setScanner({ buy_threshold_score: v })}
            />
            <SliderField
              label="Score de Compra Forte"
              value={scanner.strong_buy_threshold}
              min={60} max={99} step={0.5} suffix=""
              caption="Acima desse score o engine prioriza o ativo na fila."
              onChange={(v) => setScanner({ strong_buy_threshold: v })}
            />
            <SliderField
              label="Máx. Oportunidades por Scan"
              value={scanner.max_opportunities_per_scan}
              min={1} max={10} step={1} suffix=""
              caption="Limita compras simultâneas por ciclo de 60s."
              onChange={(v) => setScanner({ max_opportunities_per_scan: v })}
            />
            <SliderField
              label="Cooldown por Símbolo"
              value={scanner.symbol_cooldown_seconds}
              min={0} max={3600} step={30} suffix="s"
              caption="Tempo mínimo entre duas compras do mesmo ativo."
              onChange={(v) => setScanner({ symbol_cooldown_seconds: v })}
            />
          </div>
        </div>
      </section>

      {/* ── Buying Config ── */}
      <section className="card">
        <div className="p-5">
          <SectionHeader
            icon={<ShoppingCart className="w-4 h-4" />}
            title="Buying — Gestão de Capital"
            subtitle="Controla quanto capital é alocado por trade e os limites de exposição total."
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
            <SliderField
              label="Capital por Trade"
              value={buying.capital_per_trade_pct}
              min={1} max={50} step={0.5} suffix="%"
              caption="Percentual do capital disponível alocado por compra."
              onChange={(v) => setBuying({ capital_per_trade_pct: v })}
            />
            <SliderField
              label="Máx. Capital em Uso"
              value={buying.max_capital_in_use_pct}
              min={10} max={100} step={5} suffix="%"
              caption="Teto de capital comprometido em posições abertas."
              onChange={(v) => setBuying({ max_capital_in_use_pct: v })}
            />
            <SliderField
              label="Máx. Posições Totais"
              value={buying.max_positions_total}
              min={1} max={100} step={1} suffix=""
              caption="Número máximo de posições abertas simultaneamente."
              onChange={(v) => setBuying({ max_positions_total: v })}
            />
            <SliderField
              label="Reserva de Capital"
              value={buying.capital_reserve_pct}
              min={0} max={50} step={1} suffix="%"
              caption="Percentual mantido como caixa, nunca alocado."
              onChange={(v) => setBuying({ capital_reserve_pct: v })}
            />
            <SliderField
              label="Exposição Máx. por Ativo"
              value={buying.max_exposure_per_asset_pct}
              min={1} max={100} step={1} suffix="%"
              caption="Percentual máximo do capital total em um único ativo."
              onChange={(v) => setBuying({ max_exposure_per_asset_pct: v })}
            />
            <NumericField
              label="Trade Mínimo"
              value={buying.capital_per_trade_min_usdt}
              suffix="USDT"
              caption="Valor mínimo em USDT por ordem."
              onChange={(v) => setBuying({ capital_per_trade_min_usdt: v })}
            />
          </div>
          <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Order Type */}
            <div className="p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
              <h4 className="text-[13px] font-semibold text-[var(--text-primary)] mb-3">Tipo de Ordem</h4>
              <div className="flex gap-2">
                {(["market", "limit"] as const).map((t) => (
                  <button key={t} onClick={() => setBuying({ order_type: t })}
                    className={`flex-1 py-2 text-[12px] font-semibold rounded-[var(--radius-sm)] border transition-all ${
                      buying.order_type === t
                        ? "bg-[var(--accent-primary)] border-[var(--accent-primary)] text-white"
                        : "bg-transparent border-[var(--border-default)] text-[var(--text-secondary)]"
                    }`}>
                    {t.toUpperCase()}
                  </button>
                ))}
              </div>
              {buying.order_type === "limit" && (
                <div className="mt-3">
                  <NumericField
                    label="Timeout da Ordem Limit"
                    value={buying.limit_order_timeout_seconds}
                    suffix="s" min={10}
                    caption="Se não preenchida no tempo, cancela e converte para market."
                    onChange={(v) => setBuying({ limit_order_timeout_seconds: v })}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* ── Buy Strategies ── */}
      {strategies.length > 0 && (
        <section className="space-y-4">
          <SectionHeader
            icon={<Brain className="w-4 h-4" />}
            title="Buy Strategies"
            subtitle="Estratégias de entrada ativas no score engine."
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {strategies.map((strat) => (
              <div key={strat.id}
                className={`card transition-all ${strat.enabled ? "border-[var(--accent-primary-border)]" : ""}`}>
                <div className="p-5">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <Brain className={`w-5 h-5 ${strat.enabled ? "text-[var(--accent-primary)]" : "text-[var(--text-tertiary)]"}`} />
                      <h3 className="font-semibold text-[14px] text-[var(--text-primary)]">{strat.name}</h3>
                    </div>
                    <div className="flex items-center gap-2">
                      <Toggle active={strat.enabled}
                        onToggle={() => setStrategies(strategies.map(s => s.id === strat.id ? { ...s, enabled: !s.enabled } : s))}
                      />
                      <button className="btn-ghost p-1"
                        onClick={() => setExpandedStrat(expandedStrat === strat.id ? null : strat.id)}>
                        {expandedStrat === strat.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                  <span className="text-[11px] font-mono text-[var(--text-tertiary)]">{strat.id}</span>
                  {expandedStrat === strat.id && strat.params && (
                    <div className="mt-4 pt-4 border-t border-[var(--border-subtle)] space-y-3">
                      {Object.entries(strat.params).map(([key, val]) => (
                        <div key={key} className="flex items-center justify-between">
                          <label className="text-[12px] text-[var(--text-secondary)] font-medium capitalize">
                            {key.replace(/_/g, " ")}
                          </label>
                          <input type="number" step="any" className="input numeric w-24 h-8 text-[13px]"
                            value={val}
                            onChange={(e) => setStrategies(strategies.map(s =>
                              s.id === strat.id ? { ...s, params: { ...s.params, [key]: parseFloat(e.target.value) || 0 } } : s
                            ))}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Sell Engine ── */}
      <section className="space-y-4">
        <div className="flex items-start gap-3">
          <div className="flex-1">
            <SectionHeader
              icon={<TrendingDown className="w-4 h-4" />}
              title="Sell Engine — Pipeline de Saída"
              subtitle="5 camadas de decisão avaliadas em sequência. A venda é sempre 100% da posição."
              badge={
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-[#3f1a1a] text-[#ef4444] border border-[#7f1d1d]">
                  <Shield className="w-3 h-3" />NUNCA VENDE NO PREJUÍZO
                </span>
              }
            />
          </div>
        </div>

        {/* Global Sell Config */}
        <div className="card">
          <div className="p-5 space-y-5">
            <div className="flex items-center gap-2 mb-2">
              <Crosshair className="w-4 h-4 text-[var(--text-tertiary)]" />
              <h3 className="text-[12px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">
                Layer 0 — Profit Filter (sempre ativo)
              </h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
              <SliderField label="Take Profit" value={selling.take_profit_pct}
                min={0.1} max={20} step={0.1} suffix="%"
                caption="Alvo de lucro para acionar o pipeline de venda."
                onChange={(v) => setSelling({ take_profit_pct: v })}
              />
              <SliderField label="Lucro Mínimo para Venda" value={selling.min_profit_pct}
                min={0} max={5} step={0.1} suffix="%"
                caption="Nenhuma camada pode vender abaixo desse patamar."
                onChange={(v) => setSelling({ min_profit_pct: v })}
              />
              <SliderField label="Margem de Segurança acima da Entrada" value={selling.safety_margin_above_entry_pct}
                min={0} max={5} step={0.1} suffix="%"
                caption="Buffer adicional acima do preço de entrada."
                onChange={(v) => setSelling({ safety_margin_above_entry_pct: v })}
              />
              <SliderField label="Rate Limit Consulta IA" value={selling.ai_rate_limit_seconds}
                min={10} max={300} step={10} suffix="s"
                caption="Intervalo mínimo entre chamadas ao modelo de IA."
                onChange={(v) => setSelling({ ai_rate_limit_seconds: v })}
              />
            </div>
            <div className="flex items-center justify-between p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
              <div>
                <h4 className="font-semibold text-[14px] text-[var(--text-primary)] flex items-center gap-2">
                  <Bot className="w-4 h-4 text-[var(--accent-primary)]" />
                  Consulta de IA Global
                </h4>
                <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
                  Habilita a Layer 3 (AI Hold) para todos os ativos.
                </p>
              </div>
              <Toggle active={selling.enable_ai_consultation}
                onToggle={() => setSelling({ enable_ai_consultation: !selling.enable_ai_consultation })} />
            </div>
            {selling.enable_ai_consultation && (
              <div className="space-y-2">
                <label className="text-[12px] text-[var(--text-secondary)] font-medium">Modelo de IA</label>
                <input type="text" className="input w-full text-[13px]"
                  value={selling.ai_model}
                  onChange={(e) => setSelling({ ai_model: e.target.value })}
                  placeholder="google/gemini-2.5-flash"
                />
                <p className="text-[11px] text-[var(--text-tertiary)]">
                  Ex: google/gemini-2.5-flash · openai/gpt-4o · anthropic/claude-3-5-sonnet
                </p>
              </div>
            )}
          </div>
        </div>

        {/* L1 — Mean Reversion */}
        <LayerCard layer={1} label="Mean Reversion" color="#8b5cf6"
          icon={<Activity className="w-4 h-4" />}
          description="Vende quando RSI está sobrecomprado, Z-score estendido e preço acima da banda superior de Bollinger — sinalizando reversão iminente."
          active={sf.mean_reversion.enabled} hasToggle
          onToggle={() => setLayer("mean_reversion", { enabled: !sf.mean_reversion.enabled })}>
          <div className="space-y-5">
            <SliderField label="RSI — Zona de Sobrecompra"
              value={sf.mean_reversion.rsi_overbought} min={55} max={100} step={0.5}
              caption="RSI acima desse valor sinaliza sobrecompra."
              onChange={(v) => setLayer("mean_reversion", { rsi_overbought: v })}
            />
            <SliderField label="Z-score — Desvio Padrão"
              value={sf.mean_reversion.zscore_threshold} min={0.5} max={5} step={0.1} suffix="σ"
              caption="Preço mais de X desvios-padrão acima da média sinaliza extensão."
              onChange={(v) => setLayer("mean_reversion", { zscore_threshold: v })}
            />
            <SliderField label="Bollinger Bands — Desvio"
              value={sf.mean_reversion.bollinger_deviation} min={0.5} max={4} step={0.1} suffix="σ"
              caption="Desvio para cálculo das bandas. Padrão: 2σ."
              onChange={(v) => setLayer("mean_reversion", { bollinger_deviation: v })}
            />
            <SliderField label="Queda de Volume para Confirmar Exaustão"
              value={sf.mean_reversion.volume_decline_pct} min={5} max={80} step={1} suffix="%"
              caption="Volume deve cair ao menos X% em relação à média para confirmar reversão."
              onChange={(v) => setLayer("mean_reversion", { volume_decline_pct: v })}
            />
          </div>
        </LayerCard>

        {/* L2 — Momentum Exit */}
        <LayerCard layer={2} label="Momentum Exit" color="#f59e0b"
          icon={<BarChart3 className="w-4 h-4" />}
          description="Vende quando a força do trend se deteriora — ADX abaixo do mínimo e compressão das Bollinger Bands, sinalizando fim do movimento direcional."
          active={sf.momentum_exit.enabled} hasToggle
          onToggle={() => setLayer("momentum_exit", { enabled: !sf.momentum_exit.enabled })}>
          <div className="space-y-5">
            <SliderField label="ADX Mínimo de Direcionalidade"
              value={sf.momentum_exit.adx_min} min={5} max={50} step={0.5}
              caption="ADX abaixo desse valor indica mercado sem trend — sinal de saída."
              onChange={(v) => setLayer("momentum_exit", { adx_min: v })}
            />
            <SliderField label="BB Width — Compressão das Bandas"
              value={sf.momentum_exit.bb_width_threshold} min={0.001} max={0.2} step={0.001}
              caption="Bandas estreitas indicam consolidação e perda de momentum."
              onChange={(v) => setLayer("momentum_exit", { bb_width_threshold: v })}
            />
            <SliderField label="Volume Spike Mínimo"
              value={sf.momentum_exit.volume_spike_multiplier} min={1} max={10} step={0.1} suffix="x"
              caption="Pico de volume acima da média indica reversão com força. Sai se ausente."
              onChange={(v) => setLayer("momentum_exit", { volume_spike_multiplier: v })}
            />
          </div>
        </LayerCard>

        {/* L3 — AI Hold */}
        <LayerCard layer={3} label="AI Hold" color="#3b82f6"
          icon={<Bot className="w-4 h-4" />}
          description="Consulta a IA antes de vender quando o lucro atinge o threshold. A IA analisa ADX, volume, RSI, volatilidade e estrutura de candles para gerar um trend_continuation_score (0–100)."
          active={sf.ai_consultation.enabled} hasToggle
          onToggle={() => setLayer("ai_consultation", { enabled: !sf.ai_consultation.enabled })}>
          <div className="space-y-5">
            <SliderField label="Lucro Mínimo para Acionar Consulta IA"
              value={sf.ai_consultation.trigger_profit_pct} min={0} max={10} step={0.1} suffix="%"
              caption="Abaixo desse lucro a IA não é consultada — decisão puramente quantitativa."
              onChange={(v) => setLayer("ai_consultation", { trigger_profit_pct: v })}
            />
            <div className="p-3 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)] space-y-2">
              <p className="text-[11px] text-[var(--text-tertiary)] font-semibold uppercase tracking-wider">Como funciona</p>
              <ul className="text-[11px] text-[var(--text-tertiary)] space-y-1 list-none">
                <li>→ Score 0–40: trend fraco → prioriza Mean Reversion → <strong className="text-[var(--color-profit)]">VENDE</strong></li>
                <li>→ Score 40–70: neutro → aplica camadas quant normalmente</li>
                <li>→ Score 70–100: trend forte → prioriza Momentum → <strong className="text-[var(--color-loss)]">SEGURA</strong></li>
              </ul>
              <p className="text-[11px] text-[var(--text-tertiary)]">
                Modelo configurado em: <code className="font-mono">{selling.ai_model}</code>
              </p>
            </div>
          </div>
        </LayerCard>

        {/* L4 — Trailing Stop */}
        <LayerCard layer={4} label="Trailing Stop" color="#10b981"
          icon={<Gauge className="w-4 h-4" />}
          description="Ativa após o lucro atingir o limiar de ativação. A partir daí, o stop segue o High Water Mark — se o preço cair mais que a distância definida, a venda é executada."
          active={sf.trailing.enabled} hasToggle
          onToggle={() => setLayer("trailing", { enabled: !sf.trailing.enabled })}>
          <div className="space-y-5">
            <SliderField label="Lucro de Ativação"
              value={sf.trailing.activation_profit_pct} min={0.1} max={20} step={0.1} suffix="%"
              caption="O trailing só começa a seguir o HWM após atingir esse lucro."
              onChange={(v) => setLayer("trailing", { activation_profit_pct: v })}
            />
            <SliderField label="Distância do HWM"
              value={sf.trailing.hwm_trail_pct} min={0.1} max={20} step={0.1} suffix="%"
              caption="Se o preço cair X% abaixo do High Water Mark, vende imediatamente."
              onChange={(v) => setLayer("trailing", { hwm_trail_pct: v })}
            />
            <div className="p-3 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)]">
              <p className="text-[11px] text-[var(--text-tertiary)]">
                Exemplo: ativo sobe 5% (HWM), distância = 2%. Se cair para 3% de lucro, vende. O trailing ajusta dinamicamente conforme o ativo sobe.
              </p>
            </div>
          </div>
        </LayerCard>

        {/* L5 — Kill Switch */}
        <LayerCard layer={5} label="Kill Switch" color="#ef4444"
          icon={<AlertTriangle className="w-4 h-4" />}
          description="Camada de emergência — sempre avaliada por último. Força saída imediata se a perda a partir do HWM ultrapassar o limite ou o ATR sinalizar volatilidade extrema. Independe de lucro."
          active={sf.kill_switch.enabled} hasToggle
          onToggle={() => setLayer("kill_switch", { enabled: !sf.kill_switch.enabled })}>
          <div className="space-y-5">
            <SliderField label="Stop por ATR (Dynamic Stop Loss)"
              value={sf.kill_switch.atr_stop_multiplier} min={0.5} max={10} step={0.1} suffix="x"
              caption="Para se o preço cair X × ATR abaixo do preço de entrada. Protege contra gaps."
              onChange={(v) => setLayer("kill_switch", { atr_stop_multiplier: v })}
            />
            <SliderField label="Máx. Drawdown do HWM"
              value={sf.kill_switch.max_drawdown_from_hwm_pct} min={0.5} max={30} step={0.5} suffix="%"
              caption="Se o preço cair X% abaixo do HWM, sai imediatamente — independente de lucro."
              onChange={(v) => setLayer("kill_switch", { max_drawdown_from_hwm_pct: v })}
            />
            <div className="p-3 rounded-[var(--radius-sm)] border flex items-start gap-2.5"
              style={{ background: "#ef444410", borderColor: "#ef444430" }}>
              <AlertTriangle className="w-3.5 h-3.5 text-[#ef4444] mt-0.5 flex-shrink-0" />
              <p className="text-[11px]" style={{ color: "#ef4444" }}>
                Kill Switch ignora a regra "nunca vende no prejuízo" e pode executar com perda. É a proteção de último recurso contra movimentos catastróficos.
              </p>
            </div>
          </div>
        </LayerCard>

        {/* Execution Filters */}
        <div className="card">
          <div className="p-5">
            <div className="flex items-center gap-2 mb-4">
              <Radar className="w-4 h-4 text-[var(--text-tertiary)]" />
              <h3 className="text-[12px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">
                Filtros de Execução (pré-ordem)
              </h3>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
                <div>
                  <h4 className="text-[13px] font-medium text-[var(--text-primary)]">Filtro de Volatilidade</h4>
                  <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">Bloqueia venda em mercados com volatilidade extrema.</p>
                </div>
                <Toggle active={sf.target.volatility_filter_enabled}
                  onToggle={() => setLayer("target", { volatility_filter_enabled: !sf.target.volatility_filter_enabled })} />
              </div>
              <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
                <div>
                  <h4 className="text-[13px] font-medium text-[var(--text-primary)]">Verificação de Liquidez</h4>
                  <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">Confirma liquidez suficiente antes de executar a ordem.</p>
                </div>
                <Toggle active={sf.target.liquidity_check_enabled}
                  onToggle={() => setLayer("target", { liquidity_check_enabled: !sf.target.liquidity_check_enabled })} />
              </div>
              <SliderField label="Multiplicador Mínimo de Volume"
                value={sf.target.min_volume_multiplier} min={0.1} max={5} step={0.1} suffix="x"
                caption="Volume atual deve ser ao menos X vezes a média histórica."
                onChange={(v) => setLayer("target", { min_volume_multiplier: v })}
              />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
