"use client";

import { useState } from "react";
import { ArrowLeft, Save, Plus, Trash2, Check, AlertTriangle, Play } from "lucide-react";
import { apiPut } from "@/lib/api";
import { ConditionBuilder, NumericInput } from "./ConditionBuilder";

// ── Shared types (mirrors ProfileBuilder) ─────────────────────────────────────
type RuleConditionType = "threshold" | "comparison" | "boolean";

interface RuleCondition {
  id: string;
  type: RuleConditionType;
  indicator?: string;
  left?: string;
  right?: string;
  operator: string;
  value?: any;
  min?: number;
  max?: number;
  period?: number;
}

interface BlockRule {
  id: string;
  name: string;
  enabled: boolean;
  logic: "AND" | "OR";
  reason?: string;
  timeframe?: string;
  conditions: RuleCondition[];
}

interface EntryTrigger extends RuleCondition {
  required: boolean;
  enabled: boolean;
  timeframe?: string;
}

interface Profile {
  id: string;
  name: string;
  config: any;
}

interface BulkProfileBuilderProps {
  selectedProfiles: Profile[];
  onClose: () => void;
}

type ActiveTab = "filters" | "scoring" | "signals" | "block_rules" | "entry_triggers";

// ── Constants (mirrors ProfileBuilder) ───────────────────────────────────────
const RULE_INDICATORS = [
  { value: "rsi",                    label: "RSI",                               kind: "number" },
  { value: "adx",                    label: "ADX",                               kind: "number" },
  { value: "macd",                   label: "MACD",                              kind: "number" },
  { value: "macd_histogram",         label: "MACD Histogram",                    kind: "number" },
  { value: "bb_width",               label: "BB Width",                          kind: "number" },
  { value: "stoch_k",                label: "Stoch %K",                          kind: "number" },
  { value: "stoch_d",                label: "Stoch %D",                          kind: "number" },
  { value: "zscore",                 label: "Z-Score",                           kind: "number" },
  { value: "volume_spike",           label: "Volume Spike",                      kind: "number" },
  { value: "volume_delta",           label: "Volume Delta",                      kind: "number" },
  { value: "atr_percent",            label: "ATR %",                             kind: "number" },
  { value: "di_plus",                label: "DI+",                               kind: "number" },
  { value: "di_minus",               label: "DI-",                               kind: "number" },
  { value: "taker_ratio",            label: "Taker Ratio (buy/(buy+sell), 0-1)", kind: "number" },
  { value: "orderbook_pressure",     label: "Orderbook Pressure",                kind: "number" },
  { value: "bid_ask_imbalance",      label: "Bid/Ask Imbalance",                 kind: "number" },
  { value: "atr",                    label: "ATR",                               kind: "number" },
  { value: "spread_pct",             label: "Spread %",                          kind: "number" },
  { value: "funding_rate",           label: "Funding Rate",                      kind: "number" },
  { value: "volume_24h",             label: "Volume 24h",                        kind: "number" },
  { value: "market_cap",             label: "Market Cap",                        kind: "number" },
  { value: "change_24h",             label: "Variacao 24h %",                    kind: "number" },
  { value: "orderbook_depth_usdt",   label: "Profundidade Book (USDT)",          kind: "number" },
  { value: "obv",                    label: "OBV",                               kind: "number" },
  { value: "vwap_distance_pct",      label: "VWAP Distance %",                   kind: "number" },
  { value: "ema5",                   label: "EMA5",                              kind: "number" },
  { value: "ema9",                   label: "EMA9",                              kind: "number" },
  { value: "ema21",                  label: "EMA21",                             kind: "number" },
  { value: "ema50",                  label: "EMA50",                             kind: "number" },
  { value: "ema200",                 label: "EMA200",                            kind: "number" },
  { value: "ema_full_alignment",     label: "EMA Full Alignment",                kind: "boolean" },
  { value: "ema9_gt_ema21",          label: "EMA9 > EMA21",                      kind: "boolean" },
  { value: "ema9_gt_ema50",          label: "EMA9 > EMA50",                      kind: "boolean" },
  { value: "ema50_gt_ema200",        label: "EMA50 > EMA200",                    kind: "boolean" },
  { value: "di_trend",               label: "DI+ > DI- (Alta)",                  kind: "boolean" },
];

const NUMERIC_RULE_INDICATORS  = RULE_INDICATORS.filter((i) => i.kind === "number");
const BOOLEAN_RULE_INDICATORS  = RULE_INDICATORS.filter((i) => i.kind === "boolean");
const BOOLEAN_RULE_INDICATOR_VALUES = new Set(BOOLEAN_RULE_INDICATORS.map((i) => i.value));

const PERIOD_DEFAULTS: Record<string, number> = {
  rsi: 14, adx: 14, di_plus: 14, di_minus: 14,
  atr_percent: 14, stoch_k: 14, stoch_d: 14,
  macd: 12, macd_histogram: 12, bb_width: 20,
  zscore: 20, volume_spike: 20, volume_delta: 20,
  vwap_distance_pct: 20,
  ema5: 5, ema9: 9, ema21: 21, ema50: 50, ema200: 200,
};

const NO_TF_INDICATORS = new Set([
  "alpha_score", "price", "volume_24h", "spread_pct", "taker_ratio",
  "ema_full_alignment", "ema9_gt_ema21", "ema9_gt_ema50",
  "ema50_gt_ema200", "orderbook_pressure", "bid_ask_imbalance", "funding_rate",
]);

const TIMEFRAME_OPTIONS = [
  { value: "1m",  label: "1m" },
  { value: "3m",  label: "3m" },
  { value: "5m",  label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h",  label: "1h" },
];

const RULE_TYPE_OPTIONS = [
  { value: "threshold",  label: "Threshold" },
  { value: "boolean",    label: "Boolean" },
  { value: "comparison", label: "Comparison" },
];

const COMPARISON_OPERATORS = [">", "<", ">=", "<=", "==", "!="];
const THRESHOLD_OPERATORS  = [">", "<", ">=", "<=", "==", "!=", "between"];

// ── Utilities (mirrors ProfileBuilder) ───────────────────────────────────────
function createRuleCondition(type: RuleConditionType = "threshold"): RuleCondition {
  if (type === "comparison") {
    return { id: `cond_${Date.now()}`, type, left: "price", operator: ">", right: "ema9" };
  }
  if (type === "boolean") {
    return { id: `cond_${Date.now()}`, type, indicator: "ema9_gt_ema21", operator: "is_true", value: true };
  }
  return { id: `cond_${Date.now()}`, type, indicator: "rsi", operator: "<", value: 60 };
}

function normalizeRuleCondition(raw: any): RuleCondition {
  if (raw?.type === "comparison" || (raw?.left && raw?.right)) {
    return {
      id: raw?.id || `cond_${Date.now()}`,
      type: "comparison",
      left: raw?.left || "price",
      operator: raw?.operator || ">",
      right: raw?.right || "ema9",
      period: raw?.period,
    };
  }
  const indicator = raw?.indicator || raw?.field || "rsi";
  const inferredType: RuleConditionType =
    raw?.type === "boolean" ||
    BOOLEAN_RULE_INDICATOR_VALUES.has(indicator) ||
    raw?.operator === "is_true" ||
    raw?.operator === "is_false" ||
    typeof raw?.value === "boolean"
      ? "boolean"
      : "threshold";
  return {
    id: raw?.id || `cond_${Date.now()}`,
    type: inferredType,
    indicator,
    operator: raw?.operator || (inferredType === "boolean" ? "is_true" : "<"),
    value:
      inferredType === "boolean"
        ? raw?.operator === "is_false" ? false : raw?.value ?? true
        : raw?.operator === "between" ? undefined : raw?.value ?? 60,
    min: raw?.min,
    max: raw?.max,
    period: raw?.period,
  };
}

function normalizeBlockRule(raw: any): BlockRule {
  const id = raw?.id || `block_${Date.now()}`;
  const base = {
    id,
    name: raw?.name || "New Block",
    enabled: raw?.enabled !== false,
    logic: (raw?.logic || "AND").toUpperCase() === "OR" ? "OR" as const : "AND" as const,
    reason: raw?.reason || "",
    timeframe: raw?.timeframe,
  };
  if (Array.isArray(raw?.conditions) && raw.conditions.length > 0) {
    return { ...base, conditions: raw.conditions.map(normalizeRuleCondition) };
  }
  return { ...base, conditions: [normalizeRuleCondition({ id: `${id}_c0`, ...raw })] };
}

function normalizeEntryTrigger(raw: any): EntryTrigger {
  const normalized = normalizeRuleCondition(raw);
  return {
    ...normalized,
    id: raw?.id || normalized.id,
    required: raw?.required || false,
    enabled: raw?.enabled !== false,
    timeframe: raw?.timeframe,
    period: raw?.period,
  };
}

// ── Key extractor for dedup/overwrite ─────────────────────────────────────────
function condKey(c: any): string {
  return c.field || c.indicator || c.left || "";
}

// ── Default state ─────────────────────────────────────────────────────────────
const DEFAULT_CONFIG = {
  filters:        { logic: "AND", conditions: [] as any[] },
  signals:        { logic: "AND", conditions: [] as any[] },
  block_rules:    { blocks: [] as BlockRule[] },
  entry_triggers: { logic: "AND", conditions: [] as EntryTrigger[] },
};

// ─────────────────────────────────────────────────────────────────────────────
export function BulkProfileBuilder({ selectedProfiles, onClose }: BulkProfileBuilderProps) {
  const [activeTab, setActiveTab]   = useState<ActiveTab>("filters");
  const [config, setConfig]         = useState<typeof DEFAULT_CONFIG>(DEFAULT_CONFIG);
  const [overwrite, setOverwrite]   = useState(false);
  const [previewLog, setPreviewLog] = useState<any[] | null>(null);
  const [saving, setSaving]         = useState(false);

  // ── State helpers ──────────────────────────────────────────────────────────
  const updateFilters      = (conditions: any[]) =>
    setConfig((c) => ({ ...c, filters: { ...c.filters, conditions } }));
  const updateSignals      = (conditions: any[]) =>
    setConfig((c) => ({ ...c, signals: { ...c.signals, conditions } }));
  const updateEntryTriggers = (conditions: EntryTrigger[]) =>
    setConfig((c) => ({ ...c, entry_triggers: { ...c.entry_triggers, conditions } }));
  const updateEntryLogic   = (logic: string) =>
    setConfig((c) => ({ ...c, entry_triggers: { ...c.entry_triggers, logic } }));
  const updateBlockRules   = (blocks: BlockRule[]) =>
    setConfig((c) => ({ ...c, block_rules: { ...c.block_rules, blocks } }));

  // ── Entry Trigger helpers ──────────────────────────────────────────────────
  const addTrigger = () =>
    updateEntryTriggers([
      ...config.entry_triggers.conditions,
      { ...createRuleCondition("threshold"), id: `entry_${Date.now()}`, required: false, enabled: true },
    ]);

  const removeTrigger = (id: string) =>
    updateEntryTriggers(config.entry_triggers.conditions.filter((t) => t.id !== id));

  const updateTrigger = (id: string, field: string, value: any) =>
    updateEntryTriggers(
      config.entry_triggers.conditions.map((t) => (t.id === id ? { ...t, [field]: value } : t))
    );

  // Atomic multi-field update — avoids closure-over-stale-state when changing type
  const replaceTrigger = (id: string, patch: Partial<EntryTrigger>) =>
    updateEntryTriggers(
      config.entry_triggers.conditions.map((t) => (t.id === id ? { ...t, ...patch } : t))
    );

  // ── Block Rule helpers ─────────────────────────────────────────────────────
  const addBlock = () =>
    updateBlockRules([
      ...config.block_rules.blocks,
      { id: `block_${Date.now()}`, name: "New Block", enabled: true, logic: "AND", conditions: [createRuleCondition("threshold")] },
    ]);

  const removeBlock = (id: string) =>
    updateBlockRules(config.block_rules.blocks.filter((b) => b.id !== id));

  const updateBlock = (id: string, field: string, value: any) =>
    updateBlockRules(config.block_rules.blocks.map((b) => (b.id === id ? { ...b, [field]: value } : b)));

  const addBlockCondition = (blockId: string) =>
    updateBlockRules(
      config.block_rules.blocks.map((b) =>
        b.id === blockId
          ? { ...b, conditions: [...b.conditions, createRuleCondition("threshold")] }
          : b
      )
    );

  const updateBlockCondition = (blockId: string, condId: string, updates: Partial<RuleCondition>) =>
    updateBlockRules(
      config.block_rules.blocks.map((b) =>
        b.id === blockId
          ? { ...b, conditions: b.conditions.map((c) => (c.id === condId ? { ...c, ...updates } : c)) }
          : b
      )
    );

  const removeBlockCondition = (blockId: string, condId: string) =>
    updateBlockRules(
      config.block_rules.blocks.map((b) =>
        b.id === blockId ? { ...b, conditions: b.conditions.filter((c) => c.id !== condId) } : b
      )
    );

  // ── Preview ────────────────────────────────────────────────────────────────
  const generatePreview = () => {
    const logs: any[] = [];

    selectedProfiles.forEach((profile) => {
      const msgs: string[] = [];

      // Filters
      config.filters.conditions.forEach((newCond) => {
        const field = condKey(newCond);
        const exists = profile.config?.filters?.conditions?.some((c: any) => condKey(c) === field);
        if (exists && !overwrite) msgs.push(`Filter '${field}' — ignorado (já existe)`);
        else if (exists && overwrite) msgs.push(`Filter '${field}' — será sobrescrito`);
        else msgs.push(`Filter '${field}' — será adicionado`);
      });

      // Signals
      config.signals.conditions.forEach((newCond) => {
        const field = condKey(newCond);
        const exists = profile.config?.signals?.conditions?.some((c: any) => condKey(c) === field);
        if (exists && !overwrite) msgs.push(`Signal '${field}' — ignorado (já existe)`);
        else if (exists && overwrite) msgs.push(`Signal '${field}' — será sobrescrito`);
        else msgs.push(`Signal '${field}' — será adicionado`);
      });

      // Entry Triggers
      config.entry_triggers.conditions.forEach((newCond) => {
        const field = condKey(newCond);
        const exists = profile.config?.entry_triggers?.conditions?.some((c: any) => condKey(c) === field);
        if (exists && !overwrite) msgs.push(`Entry Trigger '${field}' — ignorado (já existe)`);
        else if (exists && overwrite) msgs.push(`Entry Trigger '${field}' — será sobrescrito`);
        else msgs.push(`Entry Trigger '${field}' — será adicionado`);
      });

      // Block Rules — match by name only
      config.block_rules.blocks.forEach((newBlock) => {
        const exists = profile.config?.block_rules?.blocks?.some((b: any) => b.name === newBlock.name);
        if (exists && !overwrite) msgs.push(`Block Rule '${newBlock.name}' — ignorado (já existe)`);
        else if (exists && overwrite) msgs.push(`Block Rule '${newBlock.name}' — será sobrescrito`);
        else msgs.push(`Block Rule '${newBlock.name}' — será adicionado`);
      });

      logs.push({
        profileId: profile.id,
        profileName: profile.name,
        messages: msgs.length > 0 ? msgs : ["Nenhuma alteração"],
      });
    });

    setPreviewLog(logs);
  };

  // ── Apply ──────────────────────────────────────────────────────────────────
  const applyChanges = async () => {
    setSaving(true);
    let ok = 0, fail = 0;

    for (const profile of selectedProfiles) {
      try {
        const cfg = JSON.parse(JSON.stringify(profile.config || {}));

        // Ensure section structure exists
        if (!cfg.filters)        cfg.filters        = { logic: "AND", conditions: [] };
        if (!cfg.signals)        cfg.signals        = { logic: "AND", conditions: [] };
        if (!cfg.entry_triggers) cfg.entry_triggers = { logic: "AND", conditions: [] };
        if (!cfg.block_rules)    cfg.block_rules    = { blocks: [] };

        // ── Filters (only if configured) ──────────────────────────────────
        if (config.filters.conditions.length > 0) {
          config.filters.conditions.forEach((newCond) => {
            const field = condKey(newCond);
            if (overwrite) {
              cfg.filters.conditions = cfg.filters.conditions.filter((c: any) => condKey(c) !== field);
            }
            const exists = cfg.filters.conditions.some((c: any) => condKey(c) === field);
            if (!exists) cfg.filters.conditions.push(newCond);
          });
        }

        // ── Signals (only if configured) ──────────────────────────────────
        if (config.signals.conditions.length > 0) {
          config.signals.conditions.forEach((newCond) => {
            const field = condKey(newCond);
            if (overwrite) {
              cfg.signals.conditions = cfg.signals.conditions.filter((c: any) => condKey(c) !== field);
            }
            const exists = cfg.signals.conditions.some((c: any) => condKey(c) === field);
            if (!exists) cfg.signals.conditions.push(newCond);
          });
        }

        // ── Entry Triggers (only if configured) ───────────────────────────
        // Isolated: NEVER touches filters or block_rules
        if (config.entry_triggers.conditions.length > 0) {
          config.entry_triggers.conditions.forEach((newCond) => {
            const field = condKey(newCond);
            if (overwrite) {
              cfg.entry_triggers.conditions = cfg.entry_triggers.conditions.filter(
                (c: any) => condKey(c) !== field
              );
            }
            const exists = cfg.entry_triggers.conditions.some((c: any) => condKey(c) === field);
            if (!exists) cfg.entry_triggers.conditions.push(newCond);
          });
        }

        // ── Block Rules (only if configured) ──────────────────────────────
        // Isolated: NEVER touches filters or entry_triggers
        // Overwrite: match by block NAME only — not by shared indicator
        if (config.block_rules.blocks.length > 0) {
          config.block_rules.blocks.forEach((newBlock) => {
            if (overwrite) {
              cfg.block_rules.blocks = cfg.block_rules.blocks.filter(
                (b: any) => b.name !== newBlock.name
              );
            }
            const exists = cfg.block_rules.blocks.some((b: any) => b.name === newBlock.name);
            if (!exists) cfg.block_rules.blocks.push(newBlock);
          });
        }

        await apiPut(`/profiles/${profile.id}`, { ...profile, config: cfg });
        ok++;
      } catch (err) {
        console.error("Failed to update profile", profile.name, err);
        fail++;
      }
    }

    setSaving(false);
    alert(`Aplicado em ${ok} profiles.${fail > 0 ? ` Falhou em ${fail}.` : ""}`);
    onClose();
  };

  // ── Derived ───────────────────────────────────────────────────────────────
  const entryConditions = config.entry_triggers.conditions;
  const entryLogic      = (config.entry_triggers as any).logic || "AND";

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-xl overflow-hidden shadow-2xl flex flex-col h-[calc(100vh-8rem)] mt-8">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-[var(--border-default)] bg-[var(--bg-secondary)]/50">
        <div className="flex items-center gap-4">
          <button
            onClick={onClose}
            className="p-2 hover:bg-[var(--bg-tertiary)] rounded-lg transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h2 className="text-xl font-bold text-[var(--text-primary)] tracking-tight">Bulk Edit Indicators</h2>
            <p className="text-[13px] text-[var(--text-secondary)] mt-0.5">
              Aplicando em {selectedProfiles.length} profiles selecionados
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-[13px] text-[var(--text-primary)] font-medium cursor-pointer bg-[var(--bg-tertiary)] px-3 py-1.5 rounded-lg border border-[var(--border-subtle)]">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
              className="w-4 h-4 rounded border-zinc-600 text-[var(--accent-primary)] focus:ring-[var(--accent-primary)] bg-zinc-800"
            />
            Overwrite existing indicators
          </label>
          <button
            onClick={generatePreview}
            disabled={saving}
            className="btn btn-secondary border-[var(--accent-primary)]/30 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/10"
          >
            <Play className="w-4 h-4 mr-2" />
            Preview Changes
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden relative">
        {/* Main Content */}
        <div className="flex-1 overflow-y-auto bg-[var(--bg-primary)] p-6">

          {/* Tabs */}
          <div className="flex space-x-1 mb-8 border-b border-[var(--border-default)]">
            {([
              { id: "filters",        label: "Filters",        count: config.filters.conditions.length },
              { id: "signals",        label: "Signals",        count: config.signals.conditions.length },
              { id: "block_rules",    label: "Block Rules",    count: config.block_rules.blocks.length },
              { id: "entry_triggers", label: "Entry Triggers", count: entryConditions.length },
              { id: "scoring",        label: "Scoring" },
            ] as { id: string; label: string; count?: number }[]).map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as ActiveTab)}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors relative flex items-center gap-2 ${
                  activeTab === tab.id
                    ? "border-[var(--accent-primary)] text-[var(--text-primary)]"
                    : "border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] hover:border-[var(--border-subtle)]"
                }`}
              >
                {tab.label}
                {tab.count !== undefined && (
                  <span className={`px-2 py-0.5 rounded-full text-[10px] ${
                    activeTab === tab.id
                      ? "bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]"
                      : "bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]"
                  }`}>
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* ── FILTERS ── */}
          {activeTab === "filters" && (
            <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6 space-y-4">
              <div>
                <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Filter Conditions</h3>
                <p className="text-[13px] text-[var(--text-secondary)]">
                  Adicionadas aos profiles selecionados. Não afeta Block Rules ou Entry Triggers.
                </p>
              </div>
              <ConditionBuilder
                conditions={config.filters.conditions}
                onChange={updateFilters}
                defaultTimeframe="5m"
              />
            </div>
          )}

          {/* ── SIGNALS ── */}
          {activeTab === "signals" && (
            <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6 space-y-4">
              <div>
                <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Signal Conditions</h3>
                <p className="text-[13px] text-[var(--text-secondary)]">
                  Adicionadas aos profiles selecionados. Não afeta Block Rules ou Entry Triggers.
                </p>
              </div>
              <ConditionBuilder
                conditions={config.signals.conditions}
                onChange={updateSignals}
                defaultTimeframe="5m"
              />
            </div>
          )}

          {/* ── BLOCK RULES ── */}
          {activeTab === "block_rules" && (
            <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6 space-y-4">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Block Rules</h3>
                  <p className="text-[13px] text-[var(--text-secondary)]">
                    Adicionadas/sobrescritas por nome do bloco. Não afeta Filters ou Entry Triggers.
                  </p>
                </div>
                <button className="btn btn-secondary text-[12px] h-8" onClick={addBlock}>
                  <Plus className="w-4 h-4 mr-1" /> Add Block
                </button>
              </div>

              <div className="space-y-4">
                {config.block_rules.blocks.map((block) => (
                  <div key={block.id} className="bg-[var(--bg-tertiary)] rounded-lg p-4 border border-[var(--border-default)] space-y-3">
                    {/* Block header */}
                    <div className="flex items-center gap-3">
                      <div
                        className={`toggle ${block.enabled ? "active" : ""}`}
                        onClick={() => updateBlock(block.id, "enabled", !block.enabled)}
                      >
                        <div className="knob" />
                      </div>
                      <input
                        type="text"
                        value={block.name}
                        onChange={(e) => updateBlock(block.id, "name", e.target.value)}
                        className="input flex-1 max-w-[220px] h-8 text-[13px]"
                        placeholder="Block Name"
                      />
                      <div className="flex items-center gap-2 ml-auto">
                        <span className="text-[11px] text-[var(--text-secondary)]">Logic:</span>
                        <select
                          className="input h-8 w-20 text-[12px]"
                          value={block.logic}
                          onChange={(e) => updateBlock(block.id, "logic", e.target.value)}
                        >
                          <option value="AND">AND</option>
                          <option value="OR">OR</option>
                        </select>
                      </div>
                      {/* Shared timeframe for block */}
                      {block.conditions.some((c) => {
                        const ref = c.type === "comparison" ? c.left : c.indicator;
                        return ref ? !NO_TF_INDICATORS.has(ref) : false;
                      }) && (
                        <select
                          className="input h-8 w-[72px] text-[11px]"
                          value={block.timeframe || ""}
                          onChange={(e) => updateBlock(block.id, "timeframe", e.target.value || undefined)}
                          title="Timeframe (shared for this block)"
                        >
                          <option value="">5m (default)</option>
                          {TIMEFRAME_OPTIONS.map((tf) => (
                            <option key={tf.value} value={tf.value}>{tf.label}</option>
                          ))}
                        </select>
                      )}
                      <button
                        className="p-2 text-red-500 hover:bg-red-500/10 rounded-lg transition-colors ml-2"
                        onClick={() => removeBlock(block.id)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Block conditions */}
                    <div className="space-y-2 pl-2">
                      {block.conditions.map((condition) => (
                        <div
                          key={condition.id}
                          className="flex items-center gap-2 flex-wrap rounded-md border border-[var(--border-subtle)] p-2 bg-[var(--bg-surface)]"
                        >
                          {/* Type selector */}
                          <select
                            className="input h-8 text-[12px] min-w-[120px]"
                            value={condition.type}
                            onChange={(e) => {
                              const next = createRuleCondition(e.target.value as RuleConditionType);
                              updateBlockCondition(block.id, condition.id, {
                                type: next.type, indicator: next.indicator, left: next.left,
                                right: next.right, operator: next.operator, value: next.value,
                                min: next.min, max: next.max,
                              });
                            }}
                          >
                            {RULE_TYPE_OPTIONS.map((o) => (
                              <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                          </select>

                          {condition.type === "comparison" ? (
                            <>
                              <select
                                className="input h-8 text-[12px] min-w-[120px]"
                                value={condition.left || "price"}
                                onChange={(e) => updateBlockCondition(block.id, condition.id, { left: e.target.value })}
                              >
                                {NUMERIC_RULE_INDICATORS.map((i) => (
                                  <option key={i.value} value={i.value}>{i.label}</option>
                                ))}
                              </select>
                              {PERIOD_DEFAULTS[condition.left || ""] !== undefined && (
                                <input
                                  type="number"
                                  className="input h-8 w-20 text-[12px] font-mono text-center"
                                  value={condition.period ?? ""}
                                  onChange={(e) => {
                                    const v = parseInt(e.target.value, 10);
                                    updateBlockCondition(block.id, condition.id, { period: isNaN(v) ? undefined : v });
                                  }}
                                  placeholder={`P:${PERIOD_DEFAULTS[condition.left || ""]}`}
                                  title={`Period (default: ${PERIOD_DEFAULTS[condition.left || ""]})`}
                                />
                              )}
                              <select
                                className="input h-8 text-[12px] w-20"
                                value={condition.operator}
                                onChange={(e) => updateBlockCondition(block.id, condition.id, { operator: e.target.value })}
                              >
                                {COMPARISON_OPERATORS.map((op) => (
                                  <option key={op} value={op}>{op}</option>
                                ))}
                              </select>
                              <select
                                className="input h-8 text-[12px] min-w-[120px]"
                                value={condition.right || "ema9"}
                                onChange={(e) => updateBlockCondition(block.id, condition.id, { right: e.target.value })}
                              >
                                {NUMERIC_RULE_INDICATORS.map((i) => (
                                  <option key={i.value} value={i.value}>{i.label}</option>
                                ))}
                              </select>
                            </>
                          ) : condition.type === "boolean" ? (
                            <>
                              <select
                                className="input h-8 text-[12px] min-w-[140px]"
                                value={condition.indicator || "ema9_gt_ema21"}
                                onChange={(e) => updateBlockCondition(block.id, condition.id, { indicator: e.target.value })}
                              >
                                {BOOLEAN_RULE_INDICATORS.map((i) => (
                                  <option key={i.value} value={i.value}>{i.label}</option>
                                ))}
                              </select>
                              <select
                                className="input h-8 text-[12px] w-24"
                                value={condition.operator === "is_false" ? "false" : "true"}
                                onChange={(e) => {
                                  const bv = e.target.value === "true";
                                  updateBlockCondition(block.id, condition.id, {
                                    operator: bv ? "is_true" : "is_false", value: bv,
                                  });
                                }}
                              >
                                <option value="true">True</option>
                                <option value="false">False</option>
                              </select>
                            </>
                          ) : (
                            /* threshold */
                            <>
                              <select
                                className="input h-8 text-[12px] min-w-[140px]"
                                value={condition.indicator || "rsi"}
                                onChange={(e) => updateBlockCondition(block.id, condition.id, { indicator: e.target.value })}
                              >
                                {NUMERIC_RULE_INDICATORS.map((i) => (
                                  <option key={i.value} value={i.value}>{i.label}</option>
                                ))}
                              </select>
                              {PERIOD_DEFAULTS[condition.indicator || ""] !== undefined && (
                                <input
                                  type="number"
                                  className="input h-8 w-20 text-[12px] font-mono text-center"
                                  value={condition.period ?? ""}
                                  onChange={(e) => {
                                    const v = parseInt(e.target.value, 10);
                                    updateBlockCondition(block.id, condition.id, { period: isNaN(v) ? undefined : v });
                                  }}
                                  placeholder={`P:${PERIOD_DEFAULTS[condition.indicator || ""]}`}
                                  title={`Period (default: ${PERIOD_DEFAULTS[condition.indicator || ""]})`}
                                />
                              )}
                              <select
                                className="input h-8 text-[12px] w-24"
                                value={condition.operator}
                                onChange={(e) => {
                                  const op = e.target.value;
                                  updateBlockCondition(block.id, condition.id, {
                                    operator: op,
                                    value: op === "between" ? undefined : (condition.value ?? 0),
                                    min: op === "between" ? Number(condition.value ?? condition.min ?? 0) : undefined,
                                    max: op === "between" ? Number(condition.max ?? 100) : undefined,
                                  });
                                }}
                              >
                                {THRESHOLD_OPERATORS.map((op) => (
                                  <option key={op} value={op}>{op === "between" ? "between" : op}</option>
                                ))}
                              </select>
                              {condition.operator === "between" ? (
                                <>
                                  <input
                                    type="number"
                                    className="input h-8 w-20 text-[12px] font-mono"
                                    value={condition.min ?? 0}
                                    onChange={(e) => updateBlockCondition(block.id, condition.id, { min: parseFloat(e.target.value) || 0 })}
                                    placeholder="Min"
                                  />
                                  <span className="text-[11px] text-[var(--text-secondary)]">–</span>
                                  <input
                                    type="number"
                                    className="input h-8 w-20 text-[12px] font-mono"
                                    value={condition.max ?? 100}
                                    onChange={(e) => updateBlockCondition(block.id, condition.id, { max: parseFloat(e.target.value) || 0 })}
                                    placeholder="Max"
                                  />
                                </>
                              ) : (
                                <input
                                  type="number"
                                  className="input h-8 w-24 text-[12px] font-mono"
                                  value={typeof condition.value === "number" ? condition.value : Number(condition.value ?? 0)}
                                  onChange={(e) => updateBlockCondition(block.id, condition.id, { value: parseFloat(e.target.value) || 0 })}
                                />
                              )}
                            </>
                          )}

                          <button
                            className="ml-auto p-1.5 text-red-500 hover:bg-red-500/10 rounded-lg transition-colors"
                            onClick={() => removeBlockCondition(block.id, condition.id)}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}

                      <button
                        className="btn btn-secondary text-[11px] h-7 px-3 mt-1"
                        onClick={() => addBlockCondition(block.id)}
                      >
                        <Plus className="w-3 h-3 mr-1" /> Add Condition
                      </button>
                    </div>
                  </div>
                ))}

                {config.block_rules.blocks.length === 0 && (
                  <div className="text-center py-8 text-[var(--text-tertiary)] text-[13px]">
                    Nenhum block rule definido.
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── ENTRY TRIGGERS ── */}
          {activeTab === "entry_triggers" && (
            <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Entry Triggers</h3>
                  <p className="text-[13px] text-[var(--text-secondary)]">
                    Adicionados/sobrescritos por indicador. Não afeta Filters ou Block Rules.
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
                    className={`flex items-center gap-3 p-3 rounded-[var(--radius-md)] border flex-wrap ${
                      trig.enabled
                        ? "border-[var(--border-default)] bg-[var(--bg-surface)]"
                        : "border-[var(--border-subtle)] bg-[var(--bg-base)] opacity-60"
                    }`}
                  >
                    {/* Enabled toggle */}
                    <div
                      className={`toggle ${trig.enabled ? "active" : ""}`}
                      onClick={() => updateTrigger(trig.id, "enabled", !trig.enabled)}
                    >
                      <div className="knob" />
                    </div>

                    {/* Type selector */}
                    <select
                      className="input h-8 text-[12px] w-32"
                      value={trig.type}
                      onChange={(e) => {
                        const next = createRuleCondition(e.target.value as RuleConditionType);
                        replaceTrigger(trig.id, {
                          type: next.type, indicator: next.indicator, left: next.left,
                          right: next.right, operator: next.operator, value: next.value,
                          min: next.min, max: next.max,
                        });
                      }}
                    >
                      {RULE_TYPE_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>

                    {trig.type === "comparison" ? (
                      <>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.left || "price"}
                          onChange={(e) => updateTrigger(trig.id, "left", e.target.value)}
                        >
                          {NUMERIC_RULE_INDICATORS.map((i) => (
                            <option key={i.value} value={i.value}>{i.label}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-20"
                          value={trig.operator}
                          onChange={(e) => updateTrigger(trig.id, "operator", e.target.value)}
                        >
                          {COMPARISON_OPERATORS.map((op) => (
                            <option key={op} value={op}>{op}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.right || "ema9"}
                          onChange={(e) => updateTrigger(trig.id, "right", e.target.value)}
                        >
                          {NUMERIC_RULE_INDICATORS.map((i) => (
                            <option key={i.value} value={i.value}>{i.label}</option>
                          ))}
                        </select>
                      </>
                    ) : trig.type === "boolean" ? (
                      <>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.indicator || "ema9_gt_ema21"}
                          onChange={(e) => updateTrigger(trig.id, "indicator", e.target.value)}
                        >
                          {BOOLEAN_RULE_INDICATORS.map((i) => (
                            <option key={i.value} value={i.value}>{i.label}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-24"
                          value={trig.operator === "is_false" ? "false" : "true"}
                          onChange={(e) => {
                            const bv = e.target.value === "true";
                            updateTrigger(trig.id, "operator", bv ? "is_true" : "is_false");
                            updateTrigger(trig.id, "value", bv);
                          }}
                        >
                          <option value="true">True</option>
                          <option value="false">False</option>
                        </select>
                      </>
                    ) : (
                      /* threshold */
                      <>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.indicator || "rsi"}
                          onChange={(e) => updateTrigger(trig.id, "indicator", e.target.value)}
                        >
                          {NUMERIC_RULE_INDICATORS.map((i) => (
                            <option key={i.value} value={i.value}>{i.label}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-20"
                          value={trig.operator}
                          onChange={(e) => {
                            const op = e.target.value;
                            if (op === "between") {
                              const minVal = typeof trig.value === "number" ? trig.value : (parseFloat(String(trig.value ?? 0)) || 0);
                              updateTrigger(trig.id, "operator", op);
                              updateTrigger(trig.id, "min", minVal);
                              updateTrigger(trig.id, "max", 100);
                              updateTrigger(trig.id, "value", undefined);
                            } else if (trig.operator === "between") {
                              updateTrigger(trig.id, "operator", op);
                              updateTrigger(trig.id, "value", trig.min ?? 0);
                              updateTrigger(trig.id, "min", undefined);
                              updateTrigger(trig.id, "max", undefined);
                            } else {
                              updateTrigger(trig.id, "operator", op);
                            }
                          }}
                        >
                          {THRESHOLD_OPERATORS.map((op) => (
                            <option key={op} value={op}>{op === "between" ? "entre" : op}</option>
                          ))}
                        </select>
                        {trig.operator === "between" ? (
                          <>
                            <NumericInput
                              className="input h-8 text-[12px] w-20 font-mono"
                              value={typeof trig.min === "number" ? trig.min : 0}
                              onChange={(v) => updateTrigger(trig.id, "min", v)}
                              placeholder="Min"
                            />
                            <span className="text-[11px] text-[var(--text-secondary)] font-medium">e</span>
                            <NumericInput
                              className="input h-8 text-[12px] w-20 font-mono"
                              value={typeof trig.max === "number" ? trig.max : 100}
                              onChange={(v) => updateTrigger(trig.id, "max", v)}
                              placeholder="Max"
                            />
                          </>
                        ) : (
                          <NumericInput
                            className="input h-8 text-[12px] w-20 font-mono"
                            value={typeof trig.value === "number" ? trig.value : null}
                            onChange={(v) => updateTrigger(trig.id, "value", v)}
                          />
                        )}
                      </>
                    )}

                    {/* Timeframe override */}
                    {!NO_TF_INDICATORS.has((trig.type === "comparison" ? trig.left : trig.indicator) || "") && (
                      <select
                        className="input h-8 text-[11px] w-[68px]"
                        value={trig.timeframe || ""}
                        onChange={(e) => updateTrigger(trig.id, "timeframe", e.target.value || undefined)}
                        title="Timeframe (default: 5m)"
                      >
                        <option value="">5m</option>
                        {TIMEFRAME_OPTIONS.map((tf) => (
                          <option key={tf.value} value={tf.value}>{tf.label}</option>
                        ))}
                      </select>
                    )}

                    {/* Period override */}
                    {trig.type !== "comparison" && PERIOD_DEFAULTS[trig.indicator || ""] !== undefined && (
                      <input
                        type="number"
                        min={1}
                        className="input h-8 text-[11px] w-14 font-mono"
                        value={trig.period ?? ""}
                        onChange={(e) => {
                          const v = parseInt(e.target.value, 10);
                          updateTrigger(trig.id, "period", isNaN(v) ? undefined : v);
                        }}
                        placeholder={`P:${PERIOD_DEFAULTS[trig.indicator || ""]}`}
                        title={`Period (default: ${PERIOD_DEFAULTS[trig.indicator || ""]})`}
                      />
                    )}

                    {/* Required */}
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
                <div className="text-center py-8 text-[var(--text-tertiary)] text-[13px]">
                  Nenhum entry trigger definido.
                </div>
              )}
            </div>
          )}

          {/* ── SCORING ── */}
          {activeTab === "scoring" && (
            <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6">
              <h3 className="text-lg font-bold text-[var(--text-primary)] mb-2">Scoring Weights</h3>
              <p className="text-[13px] text-[var(--text-secondary)] flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-orange-500 shrink-0" />
                Bulk update de pesos de scoring não é suportado para evitar sobrescritas indesejadas.
                Use o edit individual de cada profile.
              </p>
            </div>
          )}
        </div>

        {/* Preview Panel */}
        {previewLog && (
          <div className="w-[400px] border-l border-[var(--border-default)] bg-[var(--bg-secondary)] shadow-xl flex flex-col z-20">
            <div className="p-4 border-b border-[var(--border-default)] flex justify-between items-center bg-[var(--bg-tertiary)]">
              <h3 className="font-bold text-[var(--text-primary)] flex items-center gap-2">
                <Check className="w-4 h-4 text-[var(--accent-primary)]" />
                Validation Log
              </h3>
              <button onClick={() => setPreviewLog(null)} className="text-[12px] text-[var(--text-secondary)] hover:text-white">
                Close
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {previewLog.map((log, i) => (
                <div key={i} className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-lg p-3">
                  <div className="font-semibold text-[13px] text-[var(--text-primary)] mb-2 border-b border-[var(--border-default)] pb-1">
                    {log.profileName}
                  </div>
                  <ul className="space-y-1">
                    {log.messages.map((msg: string, j: number) => (
                      <li
                        key={j}
                        className={`text-[12px] ${
                          msg.includes("ignorado") ? "text-[var(--text-tertiary)]"
                          : msg.includes("sobrescrito") ? "text-orange-400"
                          : "text-[var(--color-profit)]"
                        }`}
                      >
                        • {msg}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <div className="p-4 border-t border-[var(--border-default)] bg-[var(--bg-tertiary)]">
              <button
                className="btn btn-primary w-full shadow-[0_0_20px_rgba(var(--accent-primary-rgb),0.3)]"
                onClick={applyChanges}
                disabled={saving}
              >
                {saving ? (
                  <span className="flex items-center justify-center">
                    <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin mr-2" />
                    Aplicando...
                  </span>
                ) : (
                  <span className="flex items-center justify-center">
                    <Save className="w-4 h-4 mr-2" />
                    Confirm &amp; Apply
                  </span>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
