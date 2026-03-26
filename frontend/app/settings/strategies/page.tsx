"use client";

import { useState, useEffect } from "react";
import {
  Save,
  RefreshCw,
  Brain,
  ChevronDown,
  ChevronUp,
  TrendingDown,
  Shield,
  Activity,
  Bot,
  Target,
  Zap,
} from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

const DEFAULT_SELLING = {
  take_profit_pct: 1.5,
  min_profit_pct: 0.5,
  never_sell_at_loss: true,
  safety_margin_above_entry_pct: 0.3,
  enable_ai_consultation: false,
  ai_rate_limit_seconds: 60,
  ai_model: "google/gemini-2.5-flash",
};

const DEFAULT_SELL_FLOW = {
  ranging: { enabled: true, adx_threshold: 18.0, bb_width_threshold: 0.03 },
  exhaustion: { enabled: true, rsi_overbought: 72.0, volume_decline_pct: 20.0 },
  ai_consultation: { enabled: false, trigger_profit_pct: 1.0 },
  target: { volatility_filter_enabled: true, min_volume_multiplier: 0.8, liquidity_check_enabled: true },
  trailing: { enabled: false, hwm_trail_pct: 0.5, activation_profit_pct: 2.0 },
};

type SellLayer = {
  key: string;
  layer: number;
  label: string;
  description: string;
  icon: React.ReactNode;
  hasToggle: boolean;
  toggleKey?: string;
};

const SELL_LAYERS: SellLayer[] = [
  {
    key: "ranging",
    layer: 1,
    label: "Ranging",
    description: "Vende quando o mercado perde direcionalidade (ADX baixo + Bandas de Bollinger estreitas).",
    icon: <Activity className="w-4 h-4" />,
    hasToggle: true,
    toggleKey: "enabled",
  },
  {
    key: "exhaustion",
    layer: 2,
    label: "Exhaustion",
    description: "Vende quando o uptrend mostra sinais de exaustão (RSI sobrecomprado + queda de volume).",
    icon: <TrendingDown className="w-4 h-4" />,
    hasToggle: true,
    toggleKey: "enabled",
  },
  {
    key: "ai_consultation",
    layer: 3,
    label: "AI Hold",
    description: "Consulta IA antes de vender para decidir se o ativo deve ser mantido.",
    icon: <Bot className="w-4 h-4" />,
    hasToggle: true,
    toggleKey: "enabled",
  },
  {
    key: "target",
    layer: 4,
    label: "Target",
    description: "Verifica filtros de liquidez e volatilidade antes de executar o take profit.",
    icon: <Target className="w-4 h-4" />,
    hasToggle: false,
  },
  {
    key: "trailing",
    layer: 5,
    label: "Trailing Stop",
    description: "Segue o preço com trailing stop a partir do HWM após o lucro mínimo de ativação.",
    icon: <Zap className="w-4 h-4" />,
    hasToggle: true,
    toggleKey: "enabled",
  },
];

function Toggle({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  return (
    <div className={`toggle ${active ? "active" : ""}`} onClick={onToggle}>
      <div className="knob" />
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between items-center">
        <label className="text-[12px] text-[var(--text-secondary)] font-medium">{label}</label>
        <span className="text-[12px] font-mono text-[var(--accent-primary)] font-semibold">
          {value}{suffix}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-[var(--accent-primary)] bg-[var(--border-default)]"
      />
      <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
        <span>{min}{suffix}</span>
        <span>{max}{suffix}</span>
      </div>
    </div>
  );
}

export default function StrategySettings() {
  const { config: stratConfig, updateConfig: updateStratConfig, isLoading: stratLoading } = useConfig("strategy");
  const { config: seConfig, updateConfig: updateSeConfig, isLoading: seLoading } = useConfig("spot_engine");

  const [strategies, setStrategies] = useState<any[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [expandedLayer, setExpandedLayer] = useState<string | null>(null);
  const [selling, setSelling] = useState<any>(DEFAULT_SELLING);
  const [sellFlow, setSellFlow] = useState<any>(DEFAULT_SELL_FLOW);
  const [seConfigFull, setSeConfigFull] = useState<any>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (stratConfig?.strategies) setStrategies(stratConfig.strategies);
  }, [stratConfig]);

  useEffect(() => {
    if (!seConfig || Object.keys(seConfig).length === 0) return;
    setSeConfigFull(seConfig);
    if (seConfig.selling) setSelling((prev: any) => ({ ...prev, ...seConfig.selling }));
    if (seConfig.sell_flow) {
      setSellFlow((prev: any) => ({
        ranging: { ...prev.ranging, ...(seConfig.sell_flow.ranging ?? {}) },
        exhaustion: { ...prev.exhaustion, ...(seConfig.sell_flow.exhaustion ?? {}) },
        ai_consultation: { ...prev.ai_consultation, ...(seConfig.sell_flow.ai_consultation ?? {}) },
        target: { ...prev.target, ...(seConfig.sell_flow.target ?? {}) },
        trailing: { ...prev.trailing, ...(seConfig.sell_flow.trailing ?? {}) },
      }));
    }
  }, [seConfig]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await Promise.all([
        updateStratConfig({ strategies }),
        updateSeConfig({ ...seConfigFull, selling, sell_flow: sellFlow }),
      ]);
    } catch (e) {
      console.error(e);
    }
    setSaving(false);
  };

  const toggleStrategy = (id: string) => {
    setStrategies(strategies.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)));
  };

  const updateParam = (stratId: string, paramKey: string, value: any) => {
    setStrategies(strategies.map((s) =>
      s.id === stratId ? { ...s, params: { ...s.params, [paramKey]: value } } : s
    ));
  };

  const updateSelling = (key: string, value: any) =>
    setSelling((prev: any) => ({ ...prev, [key]: value }));

  const updateLayer = (layerKey: string, field: string, value: any) =>
    setSellFlow((prev: any) => ({
      ...prev,
      [layerKey]: { ...prev[layerKey], [field]: value },
    }));

  if (stratLoading || seLoading) {
    return (
      <div className="p-8 space-y-4">
        <div className="skeleton h-8 w-64" />
        <div className="skeleton h-48 w-full" />
        <div className="skeleton h-48 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Strategies Module</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Configure estratégias de compra e as regras das 5 camadas de venda.
          </p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save All"}
        </button>
      </div>

      {/* Buy Strategies */}
      <section className="space-y-4">
        <div>
          <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">Buy Strategies</h2>
          <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
            Estratégias de entrada ativas no scanner.
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {strategies.map((strat) => (
            <div
              key={strat.id}
              className={`card transition-all ${strat.enabled ? "border-[var(--accent-primary-border)]" : ""}`}
            >
              <div className="p-5">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <Brain
                      className={`w-5 h-5 ${strat.enabled ? "text-[var(--accent-primary)]" : "text-[var(--text-tertiary)]"}`}
                    />
                    <h3 className="font-semibold text-[15px] text-[var(--text-primary)]">{strat.name}</h3>
                  </div>
                  <div className="flex items-center gap-2">
                    <Toggle active={strat.enabled} onToggle={() => toggleStrategy(strat.id)} />
                    <button
                      className="btn-ghost p-1"
                      onClick={() => setExpanded(expanded === strat.id ? null : strat.id)}
                    >
                      {expanded === strat.id ? (
                        <ChevronUp className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>
                <span className="text-[11px] font-mono text-[var(--text-tertiary)]">{strat.id}</span>
                {expanded === strat.id && strat.params && (
                  <div className="mt-4 pt-4 border-t border-[var(--border-subtle)] space-y-3">
                    {Object.entries(strat.params).map(([key, val]) => (
                      <div key={key} className="flex items-center justify-between">
                        <label className="text-[12px] text-[var(--text-secondary)] font-medium capitalize">
                          {key.replace(/_/g, " ")}
                        </label>
                        <input
                          type="number"
                          step="any"
                          className="input numeric w-24 h-8 text-[13px]"
                          value={val as number}
                          onChange={(e) => updateParam(strat.id, key, parseFloat(e.target.value) || 0)}
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

      {/* Sell Engine */}
      <section className="space-y-4">
        <div className="flex items-center gap-3">
          <div>
            <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">Sell Engine</h2>
            <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
              Pipeline de 5 camadas que decide quando e como vender cada posição.
            </p>
          </div>
          <span className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-[#3f1a1a] text-[#ef4444] border border-[#7f1d1d]">
            <Shield className="w-3.5 h-3.5" />
            NUNCA VENDE NO PREJUÍZO — INVIOLÁVEL
          </span>
        </div>

        {/* Global Sell Config */}
        <div className="card">
          <div className="p-5 space-y-5">
            <h3 className="text-[13px] font-semibold text-[var(--text-primary)] uppercase tracking-wider opacity-60">
              Configuração Global de Saída
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
              <Slider
                label="Take Profit"
                value={selling.take_profit_pct}
                min={0.1}
                max={20}
                step={0.1}
                suffix="%"
                onChange={(v) => updateSelling("take_profit_pct", v)}
              />
              <Slider
                label="Lucro Mínimo para Venda"
                value={selling.min_profit_pct}
                min={0}
                max={5}
                step={0.1}
                suffix="%"
                onChange={(v) => updateSelling("min_profit_pct", v)}
              />
              <Slider
                label="Margem de Segurança acima do Preço de Entrada"
                value={selling.safety_margin_above_entry_pct}
                min={0}
                max={5}
                step={0.1}
                suffix="%"
                onChange={(v) => updateSelling("safety_margin_above_entry_pct", v)}
              />
              <Slider
                label="Rate Limit Consulta IA"
                value={selling.ai_rate_limit_seconds}
                min={10}
                max={300}
                step={10}
                suffix="s"
                onChange={(v) => updateSelling("ai_rate_limit_seconds", v)}
              />
            </div>

            {/* AI Consultation Global Toggle */}
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
              <Toggle
                active={selling.enable_ai_consultation}
                onToggle={() => updateSelling("enable_ai_consultation", !selling.enable_ai_consultation)}
              />
            </div>

            {selling.enable_ai_consultation && (
              <div className="space-y-2">
                <label className="text-[12px] text-[var(--text-secondary)] font-medium">Modelo de IA</label>
                <input
                  type="text"
                  className="input w-full text-[13px]"
                  value={selling.ai_model}
                  onChange={(e) => updateSelling("ai_model", e.target.value)}
                  placeholder="google/gemini-2.5-flash"
                />
                <p className="text-[11px] text-[var(--text-tertiary)]">
                  Ex: google/gemini-2.5-flash, openai/gpt-4o, anthropic/claude-3-5-sonnet
                </p>
              </div>
            )}
          </div>
        </div>

        {/* 5 Layer Cards */}
        <div className="space-y-3">
          {SELL_LAYERS.map((layer) => {
            const layerData = sellFlow[layer.key] ?? {};
            const isActive = layer.hasToggle ? !!layerData[layer.toggleKey!] : true;
            const isExpanded = expandedLayer === layer.key;

            return (
              <div
                key={layer.key}
                className={`card transition-all ${isActive ? "border-[var(--accent-primary-border)]" : ""}`}
              >
                <div className="p-5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div
                        className={`p-2 rounded-[var(--radius-sm)] ${
                          isActive
                            ? "bg-[var(--accent-primary-muted)] text-[var(--accent-primary)]"
                            : "bg-[var(--bg-elevated)] text-[var(--text-tertiary)]"
                        }`}
                      >
                        {layer.icon}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
                            Layer {layer.layer}
                          </span>
                          <span
                            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${
                              isActive
                                ? "bg-[var(--accent-primary-muted)] text-[var(--accent-primary)]"
                                : "bg-[var(--bg-elevated)] text-[var(--text-tertiary)]"
                            }`}
                          >
                            {isActive ? "ON" : "OFF"}
                          </span>
                        </div>
                        <h3 className="font-semibold text-[14px] text-[var(--text-primary)]">{layer.label}</h3>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {layer.hasToggle && (
                        <Toggle
                          active={isActive}
                          onToggle={() => updateLayer(layer.key, layer.toggleKey!, !isActive)}
                        />
                      )}
                      <button
                        className="btn-ghost p-1"
                        onClick={() => setExpandedLayer(isExpanded ? null : layer.key)}
                      >
                        {isExpanded ? (
                          <ChevronUp className="w-4 h-4" />
                        ) : (
                          <ChevronDown className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  </div>

                  {!isExpanded && (
                    <p className="text-[12px] text-[var(--text-secondary)] mt-2 ml-11">{layer.description}</p>
                  )}

                  {isExpanded && (
                    <div className="mt-4 pt-4 border-t border-[var(--border-subtle)] space-y-5">
                      <p className="text-[12px] text-[var(--text-secondary)]">{layer.description}</p>

                      {/* Layer 1 — Ranging */}
                      {layer.key === "ranging" && (
                        <div className="space-y-5">
                          <Slider
                            label="ADX — Limiar de Direcionalidade"
                            value={layerData.adx_threshold ?? 18}
                            min={5}
                            max={40}
                            step={0.5}
                            suffix=""
                            onChange={(v) => updateLayer("ranging", "adx_threshold", v)}
                          />
                          <Slider
                            label="BB Width — Limiar de Compressão"
                            value={layerData.bb_width_threshold ?? 0.03}
                            min={0.001}
                            max={0.2}
                            step={0.001}
                            suffix=""
                            onChange={(v) => updateLayer("ranging", "bb_width_threshold", v)}
                          />
                        </div>
                      )}

                      {/* Layer 2 — Exhaustion */}
                      {layer.key === "exhaustion" && (
                        <div className="space-y-5">
                          <Slider
                            label="RSI — Zona de Sobrecompra"
                            value={layerData.rsi_overbought ?? 72}
                            min={50}
                            max={100}
                            step={0.5}
                            suffix=""
                            onChange={(v) => updateLayer("exhaustion", "rsi_overbought", v)}
                          />
                          <Slider
                            label="Queda de Volume para Sinalizar Exaustão"
                            value={layerData.volume_decline_pct ?? 20}
                            min={5}
                            max={80}
                            step={1}
                            suffix="%"
                            onChange={(v) => updateLayer("exhaustion", "volume_decline_pct", v)}
                          />
                        </div>
                      )}

                      {/* Layer 3 — AI Hold */}
                      {layer.key === "ai_consultation" && (
                        <div className="space-y-5">
                          <Slider
                            label="Lucro Mínimo para Acionar Consulta IA"
                            value={layerData.trigger_profit_pct ?? 1.0}
                            min={0}
                            max={10}
                            step={0.1}
                            suffix="%"
                            onChange={(v) => updateLayer("ai_consultation", "trigger_profit_pct", v)}
                          />
                          <div className="p-3 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)]">
                            <p className="text-[11px] text-[var(--text-tertiary)]">
                              Quando habilitada, a IA é consultada antes de qualquer venda com lucro acima do
                              limiar. A resposta determina se o ativo é mantido ou vendido. Requer modelo
                              configurado na seção Global acima.
                            </p>
                          </div>
                        </div>
                      )}

                      {/* Layer 4 — Target */}
                      {layer.key === "target" && (
                        <div className="space-y-4">
                          <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
                            <div>
                              <h4 className="text-[13px] font-medium text-[var(--text-primary)]">
                                Filtro de Volatilidade
                              </h4>
                              <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                                Bloqueia venda em mercados altamente voláteis.
                              </p>
                            </div>
                            <Toggle
                              active={layerData.volatility_filter_enabled ?? true}
                              onToggle={() =>
                                updateLayer(
                                  "target",
                                  "volatility_filter_enabled",
                                  !layerData.volatility_filter_enabled
                                )
                              }
                            />
                          </div>
                          <div className="flex items-center justify-between p-3 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
                            <div>
                              <h4 className="text-[13px] font-medium text-[var(--text-primary)]">
                                Verificação de Liquidez
                              </h4>
                              <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                                Confirma liquidez suficiente antes de executar a venda.
                              </p>
                            </div>
                            <Toggle
                              active={layerData.liquidity_check_enabled ?? true}
                              onToggle={() =>
                                updateLayer(
                                  "target",
                                  "liquidity_check_enabled",
                                  !layerData.liquidity_check_enabled
                                )
                              }
                            />
                          </div>
                          <Slider
                            label="Multiplicador Mínimo de Volume"
                            value={layerData.min_volume_multiplier ?? 0.8}
                            min={0.1}
                            max={5}
                            step={0.1}
                            suffix="x"
                            onChange={(v) => updateLayer("target", "min_volume_multiplier", v)}
                          />
                        </div>
                      )}

                      {/* Layer 5 — Trailing */}
                      {layer.key === "trailing" && (
                        <div className="space-y-5">
                          <Slider
                            label="Trail Distance do HWM"
                            value={layerData.hwm_trail_pct ?? 0.5}
                            min={0.1}
                            max={50}
                            step={0.1}
                            suffix="%"
                            onChange={(v) => updateLayer("trailing", "hwm_trail_pct", v)}
                          />
                          <Slider
                            label="Lucro de Ativação do Trailing"
                            value={layerData.activation_profit_pct ?? 2.0}
                            min={0.1}
                            max={20}
                            step={0.1}
                            suffix="%"
                            onChange={(v) => updateLayer("trailing", "activation_profit_pct", v)}
                          />
                          <div className="p-3 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)]">
                            <p className="text-[11px] text-[var(--text-tertiary)]">
                              O trailing stop só é ativado após o lucro atingir o limiar de ativação. A
                              partir daí, se o preço cair mais do que a distância definida a partir do HWM
                              (High Water Mark), a venda é executada.
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
