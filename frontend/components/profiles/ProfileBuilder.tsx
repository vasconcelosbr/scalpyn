"use client";

import { useState, useEffect } from "react";
import { ArrowLeft, Save, Play, RefreshCw, ShieldOff, Zap, Plus, Trash2 } from "lucide-react";
import { apiPost } from "@/lib/api";
import { ConditionBuilder } from "./ConditionBuilder";
import { WeightSliders } from "./WeightSliders";
import PresetIAButton from "./PresetIAButton";
import ProfileRoleSelector, { ProfileRole } from "./ProfileRoleSelector";

interface ProfileBuilderProps {
  profile?: any;
  onSave: (data: any) => void;
  onCancel: () => void;
}

interface Condition {
  id: string;
  field: string;
  operator: string;
  value: any;
  required?: boolean;
}

interface BlockRule {
  id: string;
  name: string;
  enabled: boolean;
  indicator: string;
  type: "threshold" | "range" | "condition";
  operator?: string;
  value?: number;
  min?: number;
  max?: number;
  reason?: string;
  timeframe?: string;
  period?: number;
}

interface EntryTrigger {
  id: string;
  indicator: string;
  operator: string;
  value: any;
  min?: number;
  max?: number;
  required: boolean;
  enabled: boolean;
  timeframe?: string;
  period?: number;
}

const BLOCK_INDICATORS = [
  "rsi", "adx", "atr_percent", "spread_pct", "volume_24h",
  "ema_full_alignment", "bb_width", "funding_rate", "macd",
  "stoch_k", "stoch_d", "di_plus", "di_minus",
];

const TRIGGER_INDICATORS = [
  "alpha_score", "rsi", "adx", "volume_spike", "macd", "macd_histogram",
  "ema_full_alignment", "stoch_k", "stoch_d", "atr_percent",
  "bb_width", "zscore", "di_plus", "di_minus", "volume_24h",
  "taker_ratio", "ema9_gt_ema21", "ema9_gt_ema50",
  "volume_delta", "orderbook_pressure", "bid_ask_imbalance",
];

const TIMEFRAME_OPTIONS = [
  { value: "1m",  label: "1m" },
  { value: "3m",  label: "3m" },
  { value: "5m",  label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h",  label: "1h" },
];

/** Period defaults for indicators that support configurable periods */
const PERIOD_DEFAULTS: Record<string, number> = {
  rsi: 14, adx: 14, di_plus: 14, di_minus: 14,
  atr_percent: 14, stoch_k: 14, stoch_d: 14,
  macd: 12, macd_histogram: 12, bb_width: 20,
  zscore: 20, volume_spike: 20, volume_delta: 20,
};

/** Indicators that should NOT show the timeframe selector (metadata / derived / scores) */
const NO_TF_INDICATORS = new Set([
  "alpha_score", "volume_24h", "spread_pct", "taker_ratio",
  "ema_full_alignment", "ema9_gt_ema21", "ema9_gt_ema50",
  "orderbook_pressure", "bid_ask_imbalance",
]);

const DEFAULT_CONFIG = {
  default_timeframe: "5m",
  filters:       { logic: "AND", conditions: [] },
  scoring:       { enabled: true, weights: { liquidity: 25, market_structure: 25, momentum: 25, signal: 25 } },
  signals:       { logic: "AND", conditions: [] },
  block_rules:   { blocks: [] },
  entry_triggers: { logic: "AND", conditions: [] },
};

const ROLE_TO_TYPE: Record<string, "L1" | "L2" | "L3"> = {
  primary_filter:    "L1",
  score_engine:      "L2",
  acquisition_queue: "L3",
  universe_filter:   "L1",
};

type ActiveTab = "filters" | "scoring" | "signals" | "block_rules" | "entry_triggers";

export function ProfileBuilder({ profile, onSave, onCancel }: ProfileBuilderProps) {
  const [name, setName]                     = useState(profile?.name || "");
  const [description, setDescription]       = useState(profile?.description || "");
  const [config, setConfig]                 = useState<any>(() => ({
    ...DEFAULT_CONFIG,
    ...(profile?.config || {}),
    block_rules:   profile?.config?.block_rules   || { blocks: [] },
    entry_triggers: profile?.config?.entry_triggers || { logic: "AND", conditions: [] },
  }));
  const [profileRole, setProfileRole]       = useState<ProfileRole | null>(profile?.profile_role || null);
  const [activeTab, setActiveTab]           = useState<ActiveTab>("filters");
  const [testResult, setTestResult]         = useState<any>(null);
  const [testing, setTesting]               = useState(false);
  const [saving, setSaving]                 = useState(false);
  const [scoringEnabled, setScoringEnabled] = useState(
    profile?.config?.scoring?.enabled !== false
  );

  const handleSave = async () => {
    if (!name.trim()) { alert("Profile name is required"); return; }
    setSaving(true);
    const profileData = {
      name,
      description,
      config,
      is_active: true,
      profile_role: profileRole,
      pipeline_order: profileRole
        ? { universe_filter: 0, primary_filter: 1, score_engine: 2, acquisition_queue: 3 }[profileRole] ?? 99
        : 99,
    };
    onSave(profileData);
    setSaving(false);
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await apiPost("/profiles/test-config", { config });
      setTestResult(result);
    } catch (e: any) {
      alert(`Test failed: ${e.message}`);
    }
    setTesting(false);
  };

  // ── Update helpers ──────────────────────────────────────────────────────────
  const updateFilters  = (conditions: Condition[], logic: string) =>
    setConfig((c: any) => ({ ...c, filters: { logic, conditions } }));

  const updateSignals  = (conditions: Condition[], logic: string) =>
    setConfig((c: any) => ({ ...c, signals: { logic, conditions } }));

  const updateWeights  = (weights: any) =>
    setConfig((c: any) => ({ ...c, scoring: { ...c.scoring, weights } }));

  const toggleScoringEnabled = (enabled: boolean) => {
    setScoringEnabled(enabled);
    setConfig((c: any) => ({ ...c, scoring: { ...c.scoring, enabled } }));
  };

  // ── Block Rules helpers ─────────────────────────────────────────────────────
  const addBlock = () =>
    setConfig((c: any) => ({
      ...c,
      block_rules: {
        ...c.block_rules,
        blocks: [
          ...(c.block_rules?.blocks || []),
          { id: `block_${Date.now()}`, name: "New Block", enabled: true, indicator: "rsi", type: "threshold", operator: ">", value: 80, reason: "" },
        ],
      },
    }));

  const removeBlock = (id: string) =>
    setConfig((c: any) => ({
      ...c,
      block_rules: { ...c.block_rules, blocks: (c.block_rules?.blocks || []).filter((b: BlockRule) => b.id !== id) },
    }));

  const updateBlock = (id: string, field: string, value: any) =>
    setConfig((c: any) => ({
      ...c,
      block_rules: {
        ...c.block_rules,
        blocks: (c.block_rules?.blocks || []).map((b: BlockRule) => b.id === id ? { ...b, [field]: value } : b),
      },
    }));

  // ── Entry Trigger helpers ───────────────────────────────────────────────────
  const addTrigger = () =>
    setConfig((c: any) => ({
      ...c,
      entry_triggers: {
        ...c.entry_triggers,
        conditions: [
          ...(c.entry_triggers?.conditions || []),
          { id: `entry_${Date.now()}`, indicator: "rsi", operator: "<", value: 60, required: false, enabled: true },
        ],
      },
    }));

  const removeTrigger = (id: string) =>
    setConfig((c: any) => ({
      ...c,
      entry_triggers: {
        ...c.entry_triggers,
        conditions: (c.entry_triggers?.conditions || []).filter((t: EntryTrigger) => t.id !== id),
      },
    }));

  const updateTrigger = (id: string, field: string, value: any) =>
    setConfig((c: any) => ({
      ...c,
      entry_triggers: {
        ...c.entry_triggers,
        conditions: (c.entry_triggers?.conditions || []).map((t: EntryTrigger) =>
          t.id === id ? { ...t, [field]: value } : t
        ),
      },
    }));

  const updateEntryLogic = (logic: string) =>
    setConfig((c: any) => ({ ...c, entry_triggers: { ...c.entry_triggers, logic } }));

  // ── Normalização safety-net (frontend) ─────────────────────────────────────
  const normalizePresetConfig = (incoming: any): any => {
    const FIELD_ALIASES: Record<string, string> = {
      "change_24h_pct":   "change_24h",
      "price_change_24h": "change_24h",
      "change_pct_24h":   "change_24h",
      "atr_pct":          "atr_percent",
      "atr_percentage":   "atr_percent",
      "bollinger_width":  "bb_width",
      "stochastic_k":     "stoch_k",
      "stochastic_d":     "stoch_d",
      "z_score":          "zscore",
    };

    const fixConditions = (conditions: any[]): any[] => {
      if (!Array.isArray(conditions)) return conditions;
      return conditions.map((cond) => {
        let field = cond.field || cond.indicator || "";
        let value = cond.value;
        if (FIELD_ALIASES[field]) field = FIELD_ALIASES[field];
        if (typeof value === "string") {
          const parsed = parseFloat(value.replace(",", "."));
          if (!isNaN(parsed)) value = parsed;
        }
        if (field === "volume_24h" && typeof value === "number") {
          const abs = Math.abs(value);
          if (value < 0)       field = "change_24h";
          else if (abs <= 5)   field = "atr_percent";
          else if (abs <= 100) field = "change_24h";
        }
        return { ...cond, field, value };
      });
    };

    const fixBlocks = (blocks: any[]): any[] => {
      if (!Array.isArray(blocks)) return [];
      return blocks.map((b) => {
        const indicator = FIELD_ALIASES[b.indicator || ""] || b.indicator || "rsi";
        return { ...b, indicator };
      });
    };

    return {
      ...incoming,
      filters: incoming.filters ? {
        ...incoming.filters,
        conditions: fixConditions(incoming.filters.conditions ?? []),
      } : incoming.filters,
      signals: incoming.signals ? {
        ...incoming.signals,
        conditions: fixConditions(incoming.signals.conditions ?? []),
      } : incoming.signals,
      block_rules: incoming.block_rules ? {
        ...incoming.block_rules,
        blocks: fixBlocks(incoming.block_rules.blocks ?? []),
      } : { blocks: [] },
      entry_triggers: incoming.entry_triggers ? {
        logic: incoming.entry_triggers.logic || "AND",
        conditions: fixConditions(incoming.entry_triggers.conditions ?? []).map((c: any) => ({
          ...c,
          indicator: c.field || c.indicator || "rsi",
          enabled: c.enabled !== false,
          required: c.required || false,
        })),
      } : { logic: "AND", conditions: [] },
    };
  };

  const handlePresetIASuccess = (result: any) => {
    if (result?.config) {
      const normalized = normalizePresetConfig(result.config);
      setConfig(normalized);
      if (normalized.scoring?.enabled !== undefined) {
        setScoringEnabled(normalized.scoring.enabled !== false);
      }
    }
  };

  const blocks: BlockRule[]           = config.block_rules?.blocks || [];
  const entryConditions: EntryTrigger[] = config.entry_triggers?.conditions || [];
  const entryLogic: string            = config.entry_triggers?.logic || "AND";

  const TABS: { key: ActiveTab; label: string; count?: number; icon?: React.ReactNode }[] = [
    { key: "filters",       label: "Filters",       count: config.filters?.conditions?.length ?? 0 },
    { key: "scoring",       label: "Scoring" },
    { key: "signals",       label: "Signals",       count: config.signals?.conditions?.length ?? 0 },
    { key: "block_rules",   label: "Block Rules",   count: blocks.filter(b => b.enabled).length,   icon: <ShieldOff className="w-3.5 h-3.5" /> },
    { key: "entry_triggers",label: "Entry Triggers",count: entryConditions.filter(t => t.enabled).length, icon: <Zap className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button className="btn btn-secondary p-2" onClick={onCancel} data-testid="back-btn">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            {profile ? "Edit Profile" : "Create Profile"}
          </h1>
          <p className="text-[var(--text-secondary)] text-[13px]">Define your strategy configuration</p>
        </div>
        {profile?.id && (
          <PresetIAButton
            profileId={profile.id}
            profileRole={profileRole ?? profile.profile_role}
            size="sm"
            onSuccess={handlePresetIASuccess}
          />
        )}
        <button className="btn btn-secondary" onClick={handleTest} disabled={testing} data-testid="test-config-btn">
          <Play className="w-4 h-4 mr-2" />
          {testing ? "Testing..." : "Test Config"}
        </button>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving} data-testid="save-profile-btn">
          <Save className="w-4 h-4 mr-2" />
          {saving ? "Saving..." : "Save Profile"}
        </button>
      </div>

      {/* Basic Info + Watchlist */}
      <div className="card">
        <div className="card-body p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="label">Profile Name</label>
              <input
                className="input"
                placeholder="e.g., High Volume Momentum"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="profile-name-input"
              />
            </div>
            <div className="space-y-2">
              <label className="label">Default Timeframe</label>
              <select
                className="input"
                value={config.default_timeframe || "5m"}
                onChange={(e) => setConfig((c: any) => ({ ...c, default_timeframe: e.target.value }))}
                data-testid="default-timeframe-select"
              >
                {TIMEFRAME_OPTIONS.map((tf) => (
                  <option key={tf.value} value={tf.value}>{tf.label}</option>
                ))}
              </select>
              <p className="text-[11px] text-[var(--text-tertiary)]">
                Indicators inherit this timeframe unless overridden
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <label className="label">Description</label>
            <textarea
              className="input min-h-[80px]"
              placeholder="Describe your strategy..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              data-testid="profile-description-input"
            />
          </div>

          <div className="pt-2">
            <ProfileRoleSelector value={profileRole} onChange={(role) => setProfileRole(role)} />
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[var(--border-default)] overflow-x-auto">
        {TABS.map(({ key, label, count, icon }) => (
          <button
            key={key}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
              activeTab === key
                ? "text-[var(--accent-primary)] border-b-2 border-[var(--accent-primary)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
            onClick={() => setActiveTab(key)}
            data-testid={`tab-${key}`}
          >
            {icon}
            {label}
            {count !== undefined && (
              <span className="ml-1 px-1.5 py-0.5 rounded-full bg-[var(--bg-hover)] text-[11px]">
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="card">
        <div className="card-body p-6">

          {/* ── FILTERS ── */}
          {activeTab === "filters" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">Filter Conditions</h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">Assets must pass these conditions to be included</p>
                </div>
                <select
                  className="input w-24"
                  value={config.filters?.logic ?? "AND"}
                  onChange={(e) => updateFilters(config.filters?.conditions ?? [], e.target.value)}
                  data-testid="filter-logic-select"
                >
                  <option value="AND">AND</option>
                  <option value="OR">OR</option>
                </select>
              </div>
              <ConditionBuilder
                conditions={config.filters?.conditions ?? []}
                onChange={(conditions) => updateFilters(conditions, config.filters?.logic ?? "AND")}
                showRequired={false}
                defaultTimeframe={config.default_timeframe || "5m"}
              />
            </div>
          )}

          {/* ── SCORING ── */}
          {activeTab === "scoring" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">Alpha Score Weights</h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">Customize how the Alpha Score is calculated</p>
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <span className="text-[12px] text-[var(--text-secondary)]">
                    {scoringEnabled ? "Enabled" : "Disabled"}
                  </span>
                  <div
                    className={`relative w-10 h-5 rounded-full transition-colors ${
                      scoringEnabled ? "bg-[var(--accent-primary)]" : "bg-[var(--bg-secondary)]"
                    }`}
                    onClick={() => toggleScoringEnabled(!scoringEnabled)}
                  >
                    <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                      scoringEnabled ? "translate-x-5" : "translate-x-0.5"
                    }`} />
                  </div>
                </label>
              </div>
              {scoringEnabled ? (
                <WeightSliders
                  weights={config.scoring?.weights ?? { liquidity: 25, market_structure: 25, momentum: 25, signal: 25 }}
                  onChange={updateWeights}
                />
              ) : (
                <div className="p-8 text-center bg-[var(--bg-secondary)] rounded-lg">
                  <p className="text-[var(--text-tertiary)] text-[13px]">
                    Alpha Score Weights are disabled. Default weights will be used.
                  </p>
                </div>
              )}
            </div>
          )}

          {/* ── SIGNALS ── */}
          {activeTab === "signals" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">Signal Entry Conditions</h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">Define when a trading signal should be triggered</p>
                </div>
                <select
                  className="input w-24"
                  value={config.signals?.logic ?? "AND"}
                  onChange={(e) => updateSignals(config.signals?.conditions ?? [], e.target.value)}
                  data-testid="signal-logic-select"
                >
                  <option value="AND">AND</option>
                  <option value="OR">OR</option>
                </select>
              </div>
              <ConditionBuilder
                conditions={config.signals?.conditions ?? []}
                onChange={(conditions) => updateSignals(conditions, config.signals?.logic ?? "AND")}
                showRequired={true}
                defaultTimeframe={config.default_timeframe || "5m"}
              />
            </div>
          )}

          {/* ── BLOCK RULES ── */}
          {activeTab === "block_rules" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">Block Rules</h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">
                    Hard veto conditions — assets matching any rule are immediately blocked from the watchlist
                  </p>
                </div>
                <button onClick={addBlock} className="btn btn-secondary text-[12px] px-3 py-1.5">
                  <Plus className="w-3.5 h-3.5 mr-1" />
                  Add Block
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {blocks.map((block) => (
                  <div
                    key={block.id}
                    className={`card ${block.enabled ? "border-l-4 border-l-[var(--color-loss)]" : "opacity-60"}`}
                  >
                    <div className="p-4 space-y-3">
                      <div className="flex items-center justify-between gap-2">
                        <input
                          type="text"
                          className="input h-8 text-[13px] font-semibold flex-1"
                          value={block.name}
                          onChange={(e) => updateBlock(block.id, "name", e.target.value)}
                          placeholder="Block name"
                        />
                        <div className="flex items-center gap-2 shrink-0">
                          <div
                            className={`toggle ${block.enabled ? "active" : ""}`}
                            onClick={() => updateBlock(block.id, "enabled", !block.enabled)}
                          >
                            <div className="knob" />
                          </div>
                          <button
                            onClick={() => removeBlock(block.id)}
                            className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)]"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <label className="label text-[11px]">Indicator</label>
                          <select
                            className="input h-8 text-[12px]"
                            value={block.indicator}
                            onChange={(e) => updateBlock(block.id, "indicator", e.target.value)}
                          >
                            {BLOCK_INDICATORS.map((i) => (
                              <option key={i} value={i}>{i}</option>
                            ))}
                          </select>
                        </div>
                        <div className="space-y-1">
                          <label className="label text-[11px]">Type</label>
                          <select
                            className="input h-8 text-[12px]"
                            value={block.type}
                            onChange={(e) => updateBlock(block.id, "type", e.target.value as BlockRule["type"])}
                          >
                            <option value="threshold">Threshold</option>
                            <option value="range">Range</option>
                          </select>
                        </div>
                      </div>

                      {/* Timeframe / Period overrides */}
                      {!NO_TF_INDICATORS.has(block.indicator) && (
                        <div className="grid grid-cols-2 gap-2">
                          <div className="space-y-1">
                            <label className="label text-[11px]">Timeframe</label>
                            <select
                              className="input h-8 text-[12px]"
                              value={block.timeframe || ""}
                              onChange={(e) => updateBlock(block.id, "timeframe", e.target.value || undefined)}
                            >
                              <option value="">{config.default_timeframe || "5m"} (default)</option>
                              {TIMEFRAME_OPTIONS.map((tf) => (
                                <option key={tf.value} value={tf.value}>{tf.label}</option>
                              ))}
                            </select>
                          </div>
                          {PERIOD_DEFAULTS[block.indicator] !== undefined && (
                            <div className="space-y-1">
                              <label className="label text-[11px]">Period</label>
                              <input
                                type="number"
                                min={1}
                                className="input h-8 text-[12px] font-mono"
                                value={block.period ?? ""}
                                onChange={(e) => {
                                  const v = parseInt(e.target.value, 10);
                                  updateBlock(block.id, "period", isNaN(v) ? undefined : v);
                                }}
                                placeholder={`${PERIOD_DEFAULTS[block.indicator]}`}
                              />
                            </div>
                          )}
                        </div>
                      )}

                      {block.type === "threshold" && (
                        <div className="flex items-center gap-2">
                          <select
                            className="input h-8 text-[12px] w-16"
                            value={block.operator || ">"}
                            onChange={(e) => updateBlock(block.id, "operator", e.target.value)}
                          >
                            {[">", "<", ">=", "<="].map((o) => (
                              <option key={o} value={o}>{o}</option>
                            ))}
                          </select>
                          <input
                            type="number"
                            className="input h-8 w-24 text-[12px] font-mono"
                            value={block.value ?? 0}
                            onChange={(e) => updateBlock(block.id, "value", parseFloat(e.target.value))}
                          />
                        </div>
                      )}

                      {block.type === "range" && (
                        <div className="flex items-center gap-2 text-[12px]">
                          <span className="text-[var(--text-secondary)]">Min</span>
                          <input
                            type="number"
                            className="input h-8 w-20 text-[12px] font-mono"
                            value={block.min ?? 0}
                            onChange={(e) => updateBlock(block.id, "min", parseFloat(e.target.value))}
                          />
                          <span className="text-[var(--text-secondary)]">Max</span>
                          <input
                            type="number"
                            className="input h-8 w-20 text-[12px] font-mono"
                            value={block.max ?? 100}
                            onChange={(e) => updateBlock(block.id, "max", parseFloat(e.target.value))}
                          />
                        </div>
                      )}

                      <input
                        type="text"
                        className="input h-8 text-[12px]"
                        value={block.reason || ""}
                        onChange={(e) => updateBlock(block.id, "reason", e.target.value)}
                        placeholder="Reason (e.g. Overbought extreme)"
                      />
                    </div>
                  </div>
                ))}
              </div>

              {blocks.length === 0 && (
                <div className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                  No block rules defined. All assets will pass the block check for this profile.
                </div>
              )}
            </div>
          )}

          {/* ── ENTRY TRIGGERS ── */}
          {activeTab === "entry_triggers" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">Entry Triggers</h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">
                    Conditions that must be met to allow trade execution (L3 only)
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] text-[var(--text-secondary)]">Logic:</span>
                    <select
                      className="input h-8 w-20 text-[12px]"
                      value={entryLogic}
                      onChange={(e) => updateEntryLogic(e.target.value)}
                    >
                      <option value="AND">AND</option>
                      <option value="OR">OR</option>
                    </select>
                  </div>
                  <button onClick={addTrigger} className="btn btn-secondary text-[12px] px-3 py-1.5">
                    <Plus className="w-3.5 h-3.5 mr-1" />
                    Add Trigger
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                {entryConditions.map((trig) => (
                  <div
                    key={trig.id}
                    className={`flex items-center gap-3 p-3 rounded-[var(--radius-md)] border ${
                      trig.enabled
                        ? "border-[var(--border-default)] bg-[var(--bg-surface)]"
                        : "border-[var(--border-subtle)] bg-[var(--bg-base)] opacity-60"
                    }`}
                  >
                    <div
                      className={`toggle ${trig.enabled ? "active" : ""}`}
                      onClick={() => updateTrigger(trig.id, "enabled", !trig.enabled)}
                    >
                      <div className="knob" />
                    </div>
                    <select
                      className="input h-8 text-[12px] w-36"
                      value={trig.indicator}
                      onChange={(e) => updateTrigger(trig.id, "indicator", e.target.value)}
                    >
                      {TRIGGER_INDICATORS.map((i) => (
                        <option key={i} value={i}>{i}</option>
                      ))}
                    </select>
                    <select
                      className="input h-8 text-[12px] w-20"
                      value={trig.operator}
                      onChange={(e) => {
                        const op = e.target.value;
                        if (op === "between") {
                          const minVal = typeof trig.value === "number" ? trig.value : (parseFloat(trig.value) || 0);
                          updateTrigger(trig.id, "operator", op);
                          updateTrigger(trig.id, "min", minVal);
                          updateTrigger(trig.id, "max", 100);
                          updateTrigger(trig.id, "value", undefined);
                        } else if (trig.operator === "between") {
                          updateTrigger(trig.id, "operator", op);
                          updateTrigger(trig.id, "value", trig.min ?? 0);
                        } else {
                          updateTrigger(trig.id, "operator", op);
                        }
                      }}
                    >
                      {[">", "<", ">=", "<=", "=", "!=", "between"].map((o) => (
                        <option key={o} value={o}>{o === "between" ? "entre" : o}</option>
                      ))}
                    </select>
                    {trig.operator === "between" ? (
                      <>
                        <input
                          type="number"
                          className="input h-8 text-[12px] w-20 font-mono"
                          value={trig.min ?? 0}
                          onChange={(e) => {
                            const num = parseFloat(e.target.value);
                            updateTrigger(trig.id, "min", isNaN(num) ? 0 : num);
                          }}
                          placeholder="Min"
                        />
                        <span className="text-[11px] text-[var(--text-secondary)] font-medium">e</span>
                        <input
                          type="number"
                          className="input h-8 text-[12px] w-20 font-mono"
                          value={trig.max ?? 100}
                          onChange={(e) => {
                            const num = parseFloat(e.target.value);
                            updateTrigger(trig.id, "max", isNaN(num) ? (trig.max ?? 100) : num);
                          }}
                          placeholder="Max"
                        />
                      </>
                    ) : (
                    <input
                      type="text"
                      className="input h-8 text-[12px] w-20 font-mono"
                      value={trig.value ?? ""}
                      onChange={(e) => {
                        const num = parseFloat(e.target.value);
                        updateTrigger(trig.id, "value", isNaN(num) ? e.target.value : num);
                      }}
                    />
                    )}
                    {/* Timeframe override */}
                    {!NO_TF_INDICATORS.has(trig.indicator) && (
                      <select
                        className="input h-8 text-[11px] w-[68px]"
                        value={trig.timeframe || ""}
                        onChange={(e) => updateTrigger(trig.id, "timeframe", e.target.value || undefined)}
                        title={`Timeframe (default: ${config.default_timeframe || "5m"})`}
                      >
                        <option value="">{config.default_timeframe || "5m"}</option>
                        {TIMEFRAME_OPTIONS.map((tf) => (
                          <option key={tf.value} value={tf.value}>{tf.label}</option>
                        ))}
                      </select>
                    )}
                    {/* Period override */}
                    {PERIOD_DEFAULTS[trig.indicator] !== undefined && (
                      <input
                        type="number"
                        min={1}
                        className="input h-8 text-[11px] w-14 font-mono"
                        value={trig.period ?? ""}
                        onChange={(e) => {
                          const v = parseInt(e.target.value, 10);
                          updateTrigger(trig.id, "period", isNaN(v) ? undefined : v);
                        }}
                        placeholder={`P:${PERIOD_DEFAULTS[trig.indicator]}`}
                        title={`Period (default: ${PERIOD_DEFAULTS[trig.indicator]})`}
                      />
                    )}
                    <label className="flex items-center gap-1.5 text-[12px] cursor-pointer">
                      <input
                        type="checkbox"
                        checked={trig.required}
                        onChange={(e) => updateTrigger(trig.id, "required", e.target.checked)}
                        className="accent-[var(--accent-primary)]"
                      />
                      <span className={trig.required ? "text-[var(--color-warning)] font-semibold" : "text-[var(--text-secondary)]"}>
                        Required
                      </span>
                    </label>
                    <button
                      onClick={() => removeTrigger(trig.id)}
                      className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)] ml-auto"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>

              {entryConditions.length === 0 && (
                <div className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                  No entry triggers defined. Trades will be allowed by default.
                </div>
              )}

              {entryConditions.length > 0 && (
                <div className="card bg-[var(--bg-base)]">
                  <div className="p-4">
                    <p className="text-[11px] text-[var(--text-tertiary)] mb-2 font-semibold uppercase tracking-wider">Logic Preview</p>
                    <pre className="text-[12px] font-mono text-[var(--text-secondary)] overflow-x-auto">
{`IF (
${entryConditions.filter(t => t.enabled && t.required).map(t => {
  const tf = t.timeframe || config.default_timeframe || "5m";
  const p = t.period ? `, P:${t.period}` : "";
  const cond = t.operator === "between" ? `entre ${t.min} e ${t.max}` : `${t.operator} ${t.value}`;
  return `  [REQUIRED] ${t.indicator} (${tf}${p}) ${cond}`;
}).join("\n  AND\n")}${
  entryConditions.filter(t => t.enabled && t.required).length > 0 &&
  entryConditions.filter(t => t.enabled && !t.required).length > 0
    ? "\n  AND ("
    : ""
}
${entryConditions.filter(t => t.enabled && !t.required).map(t => {
  const tf = t.timeframe || config.default_timeframe || "5m";
  const p = t.period ? `, P:${t.period}` : "";
  const cond = t.operator === "between" ? `entre ${t.min} e ${t.max}` : `${t.operator} ${t.value}`;
  return `    ${t.indicator} (${tf}${p}) ${cond}`;
}).join(`\n    ${entryLogic}\n`)}${
  entryConditions.filter(t => t.enabled && !t.required).length > 0 ? "\n  )" : ""
}
) → ALLOW TRADE ENTRY`}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          )}

        </div>
      </div>

      {/* Test Results */}
      {testResult && (
        <div className="card border-l-4 border-l-[var(--accent-primary)]">
          <div className="card-body p-6">
            <h3 className="font-semibold text-[var(--text-primary)] mb-4">Test Results</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: "Total Assets",  value: testResult.summary?.total_assets || 0,       color: "var(--text-primary)" },
                { label: "After Filter",  value: testResult.summary?.after_filter || 0,        color: "var(--color-profit)" },
                { label: "Filter Rate",   value: testResult.summary?.filter_rate || "0%",      color: "var(--text-primary)" },
                { label: "Signals",       value: testResult.summary?.signals_triggered || 0,   color: "var(--accent-primary)" },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div className="text-[var(--text-tertiary)] text-xs">{label}</div>
                  <div className="text-xl font-bold" style={{ color }}>{value}</div>
                </div>
              ))}
            </div>
            {testResult.sample_assets?.length > 0 && (
              <div className="mt-4">
                <div className="text-[var(--text-tertiary)] text-xs mb-2">Top Matched Assets</div>
                <div className="flex flex-wrap gap-2">
                  {testResult.sample_assets.slice(0, 5).map((asset: any) => (
                    <span key={asset.symbol} className="px-2 py-1 rounded bg-[var(--bg-secondary)] text-[var(--text-primary)] text-xs">
                      {asset.symbol} ({asset.score?.total_score?.toFixed(1) || 0})
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
