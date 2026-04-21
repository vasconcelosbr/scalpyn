"use client";

import { useState, useEffect, useMemo } from "react";
import { ArrowLeft, Save, Play, ShieldOff, Zap, Plus, Trash2, Target } from "lucide-react";
import { apiPost } from "@/lib/api";
import { ConditionBuilder, type ScoreRule } from "./ConditionBuilder";
import { WeightSliders } from "./WeightSliders";
import PresetIAButton from "./PresetIAButton";
import ProfileRoleSelector, { ProfileRole } from "./ProfileRoleSelector";
import { useConfig } from "@/hooks/useConfig";

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

type RuleConditionType = "threshold" | "boolean" | "comparison";
type RuleValue = string | number | boolean | null | undefined;

interface RuleIndicatorOption {
  value: string;
  label: string;
  kind: "number" | "boolean";
}

interface RuleCondition {
  id: string;
  type: RuleConditionType;
  indicator?: string;
  left?: string;
  right?: string;
  operator: string;
  value?: RuleValue;
  min?: number;
  max?: number;
}

interface BlockRule {
  id: string;
  name: string;
  enabled: boolean;
  logic: "AND" | "OR";
  conditions: RuleCondition[];
  reason?: string;
  timeframe?: string;
  period?: number;
}

interface EntryTrigger extends RuleCondition {
  id: string;
  required: boolean;
  enabled: boolean;
  timeframe?: string;
  period?: number;
}

const RULE_INDICATORS: RuleIndicatorOption[] = [
  { value: "price", label: "Price", kind: "number" },
  { value: "ema5", label: "EMA 5", kind: "number" },
  { value: "ema9", label: "EMA 9", kind: "number" },
  { value: "ema21", label: "EMA 21", kind: "number" },
  { value: "ema50", label: "EMA 50", kind: "number" },
  { value: "ema200", label: "EMA 200", kind: "number" },
  { value: "alpha_score", label: "Alpha Score", kind: "number" },
  { value: "rsi", label: "RSI", kind: "number" },
  { value: "adx", label: "ADX", kind: "number" },
  { value: "macd", label: "MACD", kind: "number" },
  { value: "macd_histogram", label: "MACD Histogram", kind: "number" },
  { value: "volume_spike", label: "Volume Spike", kind: "number" },
  { value: "taker_ratio", label: "Taker Ratio", kind: "number" },
  { value: "volume_delta", label: "Volume Delta", kind: "number" },
  { value: "orderbook_pressure", label: "Orderbook Pressure", kind: "number" },
  { value: "bid_ask_imbalance", label: "Bid/Ask Imbalance", kind: "number" },
  { value: "atr_percent", label: "ATR %", kind: "number" },
  { value: "bb_width", label: "BB Width", kind: "number" },
  { value: "spread_pct", label: "Spread %", kind: "number" },
  { value: "zscore", label: "Z-Score", kind: "number" },
  { value: "funding_rate", label: "Funding Rate", kind: "number" },
  { value: "volume_24h", label: "Volume 24h", kind: "number" },
  { value: "stoch_k", label: "Stoch %K", kind: "number" },
  { value: "stoch_d", label: "Stoch %D", kind: "number" },
  { value: "di_plus", label: "DI+", kind: "number" },
  { value: "di_minus", label: "DI-", kind: "number" },
  { value: "ema_full_alignment", label: "EMA Full Alignment", kind: "boolean" },
  { value: "ema9_gt_ema21", label: "EMA9 > EMA21", kind: "boolean" },
  { value: "ema9_gt_ema50", label: "EMA9 > EMA50", kind: "boolean" },
  { value: "ema50_gt_ema200", label: "EMA50 > EMA200", kind: "boolean" },
];

const RULE_INDICATOR_MAP = new Map(RULE_INDICATORS.map((indicator) => [indicator.value, indicator]));
const NUMERIC_RULE_INDICATORS = RULE_INDICATORS.filter((indicator) => indicator.kind === "number");
const BOOLEAN_RULE_INDICATORS = RULE_INDICATORS.filter((indicator) => indicator.kind === "boolean");
const BOOLEAN_RULE_INDICATOR_VALUES = new Set(BOOLEAN_RULE_INDICATORS.map((indicator) => indicator.value));

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
  ema5: 5, ema9: 9, ema21: 21, ema50: 50, ema200: 200,
};

/** Indicators that should NOT show the timeframe selector (metadata / derived / scores) */
const NO_TF_INDICATORS = new Set([
  "alpha_score", "price", "volume_24h", "spread_pct", "taker_ratio",
  "ema_full_alignment", "ema9_gt_ema21", "ema9_gt_ema50",
  "ema50_gt_ema200", "orderbook_pressure", "bid_ask_imbalance",
  "funding_rate",
]);

const DEFAULT_CONFIG = {
  default_timeframe: "5m",
  filters:       { logic: "AND", conditions: [] },
  scoring:       { enabled: true, weights: { liquidity: 25, market_structure: 25, momentum: 25, signal: 25 } },
  signals:       { logic: "AND", conditions: [] },
  block_rules:   { blocks: [] },
  entry_triggers: { logic: "AND", conditions: [] },
};

type ActiveTab = "filters" | "scoring" | "signals" | "block_rules" | "entry_triggers";

const RULE_TYPE_OPTIONS: { value: RuleConditionType; label: string }[] = [
  { value: "threshold", label: "Threshold" },
  { value: "boolean", label: "Boolean" },
  { value: "comparison", label: "Comparison" },
];

const COMPARISON_OPERATORS = [">", "<", ">=", "<=", "==", "!="];
const THRESHOLD_OPERATORS = [">", "<", ">=", "<=", "==", "!=", "between"];

function createRuleCondition(type: RuleConditionType = "threshold"): RuleCondition {
  if (type === "comparison") {
    return {
      id: `cond_${Date.now()}`,
      type,
      left: "price",
      operator: ">",
      right: "ema9",
    };
  }

  if (type === "boolean") {
    return {
      id: `cond_${Date.now()}`,
      type,
      indicator: "ema9_gt_ema21",
      operator: "is_true",
      value: true,
    };
  }

  return {
    id: `cond_${Date.now()}`,
    type,
    indicator: "rsi",
    operator: "<",
    value: 60,
  };
}

function normalizeRuleCondition(raw: any): RuleCondition {
  if (raw?.type === "comparison" || (raw?.left && raw?.right)) {
    return {
      id: raw?.id || `cond_${Date.now()}`,
      type: "comparison",
      left: raw?.left || "price",
      operator: raw?.operator || ">",
      right: raw?.right || "ema9",
    };
  }

  const indicator = raw?.indicator || raw?.field || "rsi";
  const inferredType: RuleConditionType =
    raw?.type === "boolean" || BOOLEAN_RULE_INDICATOR_VALUES.has(indicator) || raw?.operator === "is_true" || raw?.operator === "is_false" || typeof raw?.value === "boolean"
      ? "boolean"
      : "threshold";

  return {
    id: raw?.id || `cond_${Date.now()}`,
    type: inferredType,
    indicator,
    operator: raw?.operator || (inferredType === "boolean" ? "is_true" : "<"),
    value:
      inferredType === "boolean"
        ? raw?.operator === "is_false"
          ? false
          : raw?.value ?? true
        : raw?.operator === "between"
          ? undefined
          : raw?.value ?? 60,
    min: raw?.min,
    max: raw?.max,
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
    period: raw?.period,
  };

  if (Array.isArray(raw?.conditions) && raw.conditions.length > 0) {
    return {
      ...base,
      conditions: raw.conditions.map(normalizeRuleCondition),
    };
  }

  if (raw?.type === "range") {
    return {
      ...base,
      logic: "OR",
      conditions: [
        {
          id: `${id}_min`,
          type: "threshold",
          indicator: raw?.indicator || "rsi",
          operator: "<",
          value: raw?.min ?? 0,
        },
        {
          id: `${id}_max`,
          type: "threshold",
          indicator: raw?.indicator || "rsi",
          operator: ">",
          value: raw?.max ?? 100,
        },
      ],
    };
  }

  if (raw?.type === "condition" && typeof raw?.condition === "string") {
    const match = raw.condition.match(/^([a-zA-Z0-9_]+)\s*([<>!=]=?|==)\s*([a-zA-Z0-9_]+)$/);
    if (match) {
      return {
        ...base,
        conditions: [
          {
            id: `${id}_cmp`,
            type: "comparison",
            left: match[1],
            operator: match[2] === "=" ? "==" : match[2],
            right: match[3],
          },
        ],
      };
    }
  }

  return {
    ...base,
    conditions: [
      normalizeRuleCondition({
        id: `${id}_legacy`,
        type: raw?.type === "comparison" ? "comparison" : undefined,
        indicator: raw?.indicator,
        operator: raw?.operator,
        value: raw?.value,
        left: raw?.left,
        right: raw?.right,
      }),
    ],
  };
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

function normalizeProfileConfig(rawConfig: any) {
  return {
    ...DEFAULT_CONFIG,
    ...(rawConfig || {}),
    block_rules: {
      blocks: (rawConfig?.block_rules?.blocks || []).map(normalizeBlockRule),
    },
    entry_triggers: {
      logic: rawConfig?.entry_triggers?.logic || "AND",
      logic_preview_text: rawConfig?.entry_triggers?.logic_preview_text,
      conditions: (rawConfig?.entry_triggers?.conditions || []).map(normalizeEntryTrigger),
    },
  };
}

export function ProfileBuilder({ profile, onSave, onCancel }: ProfileBuilderProps) {
  const { config: globalScoreConfig } = useConfig("score");
  const [name, setName]                     = useState(profile?.name || "");
  const [description, setDescription]       = useState(profile?.description || "");
  const [config, setConfig]                 = useState<any>(() => normalizeProfileConfig(profile?.config));
  const [profileRole, setProfileRole]       = useState<ProfileRole | null>(profile?.profile_role || null);
  const [activeTab, setActiveTab]           = useState<ActiveTab>("filters");
  const [testResult, setTestResult]         = useState<any>(null);
  const [testing, setTesting]               = useState(false);
  const [saving, setSaving]                 = useState(false);
  const [scoringEnabled, setScoringEnabled] = useState(
    profile?.config?.scoring?.enabled !== false
  );
  const [entryLogicPreview, setEntryLogicPreview] = useState(
    profile?.config?.entry_triggers?.logic_preview_text || ""
  );

  const scoreRules = useMemo<ScoreRule[]>(
    () => ((globalScoreConfig?.scoring_rules as ScoreRule[] | undefined) || []).map((rule) => ({
      ...rule,
      category: rule.category || "",
      points: Number(rule.points ?? 0),
    })),
    [globalScoreConfig]
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

  const toggleScoringRuleId = (ruleId: string) =>
    setConfig((c: any) => {
      const current: string[] = c.scoring?.selected_rule_ids ?? [];
      const next = current.includes(ruleId)
        ? current.filter((id: string) => id !== ruleId)
        : [...current, ruleId];
      return { ...c, scoring: { ...c.scoring, selected_rule_ids: next } };
    });

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
          {
            id: `block_${Date.now()}`,
            name: "New Block",
            enabled: true,
            logic: "AND",
            conditions: [createRuleCondition("comparison")],
            reason: "",
          },
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

  const addBlockCondition = (blockId: string) =>
    setConfig((c: any) => ({
      ...c,
      block_rules: {
        ...c.block_rules,
        blocks: (c.block_rules?.blocks || []).map((block: BlockRule) => (
          block.id === blockId
            ? { ...block, conditions: [...(block.conditions || []), createRuleCondition("comparison")] }
            : block
        )),
      },
    }));

  const updateBlockCondition = (blockId: string, conditionId: string, updates: Partial<RuleCondition>) =>
    setConfig((c: any) => ({
      ...c,
      block_rules: {
        ...c.block_rules,
        blocks: (c.block_rules?.blocks || []).map((block: BlockRule) => (
          block.id === blockId
            ? {
                ...block,
                conditions: (block.conditions || []).map((condition) => (
                  condition.id === conditionId ? { ...condition, ...updates } : condition
                )),
              }
            : block
        )),
      },
    }));

  const removeBlockCondition = (blockId: string, conditionId: string) =>
    setConfig((c: any) => ({
      ...c,
      block_rules: {
        ...c.block_rules,
        blocks: (c.block_rules?.blocks || []).map((block: BlockRule) => (
          block.id === blockId
            ? {
                ...block,
                conditions: (block.conditions || []).filter((condition) => condition.id !== conditionId),
              }
            : block
        )),
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
          {
            ...createRuleCondition("comparison"),
            id: `entry_${Date.now()}`,
            required: false,
            enabled: true,
          },
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

  const buildEntryLogicPreview = (
    conditions: EntryTrigger[],
    defaultTimeframe: string,
    logic: string,
  ) => {
    const describe = (trigger: EntryTrigger, requiredLabel = false) => {
      const referenceIndicator = trigger.type === "comparison" ? trigger.left : trigger.indicator;
      const tf = referenceIndicator && NO_TF_INDICATORS.has(referenceIndicator)
        ? ""
        : ` (${trigger.timeframe || defaultTimeframe}${trigger.period ? `, P:${trigger.period}` : ""})`;
      let conditionText = "";

      if (trigger.type === "comparison") {
        conditionText = `${trigger.left} ${trigger.operator} ${trigger.right}`;
      } else if (trigger.type === "boolean") {
        const booleanValue =
          trigger.operator === "is_false" || trigger.value === false
            ? "False"
            : trigger.operator === "is_true" || trigger.value === true
              ? "True"
              : String(trigger.value ?? trigger.operator ?? "True");
        conditionText = `${trigger.indicator}${tf} = ${booleanValue}`;
      } else if (trigger.operator === "between") {
        conditionText = `between ${trigger.min} and ${trigger.max}`;
      } else {
        conditionText = `${trigger.operator} ${trigger.value}`;
      }

      if (trigger.type === "comparison") {
        return `  ${requiredLabel ? "[REQUIRED] " : ""}${conditionText}${tf}`.trimEnd();
      }

      return `  ${requiredLabel ? "[REQUIRED] " : ""}${trigger.indicator}${tf} ${conditionText}`.trimEnd();
    };

    const required = conditions.filter((t) => t.enabled && t.required);
    const optional = conditions.filter((t) => t.enabled && !t.required);
    const lines: string[] = ["IF ("];

    if (required.length > 0) {
      lines.push(required.map((trigger) => describe(trigger, true)).join("\n  AND\n"));
    }

    if (optional.length > 0) {
      if (required.length > 0) lines.push("  AND");
      lines.push("  (");
      lines.push(optional.map((trigger) => describe(trigger)).join(`\n    ${logic}\n`));
      lines.push("  )");
    }

    if (required.length === 0 && optional.length === 0) {
      lines.push("  No active entry triggers");
    }

    lines.push(") → ALLOW TRADE ENTRY");
    return lines.join("\n");
  };

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
      return blocks.map((block) => normalizeBlockRule({
        ...block,
        indicator: FIELD_ALIASES[block.indicator || ""] || block.indicator,
      }));
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
        logic_preview_text: incoming.entry_triggers.logic_preview_text,
        conditions: fixConditions(incoming.entry_triggers.conditions ?? []).map((c: any) => normalizeEntryTrigger({
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
      setEntryLogicPreview(normalized.entry_triggers?.logic_preview_text || "");
      if (normalized.scoring?.enabled !== undefined) {
        setScoringEnabled(normalized.scoring.enabled !== false);
      }
    }
  };

  const blocks: BlockRule[]           = config.block_rules?.blocks || [];
  const entryConditions: EntryTrigger[] = config.entry_triggers?.conditions || [];
  const entryLogic: string            = config.entry_triggers?.logic || "AND";
  const autoEntryLogicPreview = useMemo(
    () => buildEntryLogicPreview(entryConditions, config.default_timeframe || "5m", entryLogic),
    [entryConditions, config.default_timeframe, entryLogic]
  );

  useEffect(() => {
    const manualPreview = config.entry_triggers?.logic_preview_text;
    setEntryLogicPreview(manualPreview || autoEntryLogicPreview);
  }, [autoEntryLogicPreview, config.entry_triggers?.logic_preview_text]);

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
                  <p className="text-[12px] text-[var(--text-secondary)]">Assets must pass these binary conditions to be included. Scoring rules are configured in the Scoring tab.</p>
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
                scoreRules={scoreRules}
              />
            </div>
          )}

          {/* ── SCORING ── */}
          {activeTab === "scoring" && (
            <div className="space-y-6">
              {/* Score Engine Configuration — rule selection */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-[var(--text-primary)]">Score Engine Configuration</h3>
                    <p className="text-[12px] text-[var(--text-secondary)]">
                      Select which global scoring rules apply to this profile. Unselected rules will not affect the Alpha Score.
                      {scoreRules.length === 0 && " (Configure scoring rules in Settings → Score Engine)"}
                    </p>
                  </div>
                </div>
                {scoreRules.length > 0 ? (
                  <div className="space-y-1.5" data-testid="scoring-rule-selection">
                    {scoreRules.map((rule) => {
                      const selectedIds: string[] = config.scoring?.selected_rule_ids ?? [];
                      const isExplicitlySelected = selectedIds.includes(rule.id);
                      return (
                        <div
                          key={rule.id}
                          className={`flex items-center gap-3 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                            isExplicitlySelected
                              ? "bg-[var(--accent-primary)]/8 border-[var(--accent-primary)]/30"
                              : selectedIds.length > 0
                              ? "bg-[var(--bg-secondary)] border-[var(--border-subtle)] opacity-50"
                              : "bg-[var(--bg-secondary)] border-[var(--border-subtle)]"
                          }`}
                          onClick={() => toggleScoringRuleId(rule.id)}
                          data-testid={`scoring-rule-toggle-${rule.id}`}
                        >
                          <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 ${
                            isExplicitlySelected
                              ? "bg-[var(--accent-primary)] border-[var(--accent-primary)]"
                              : "border-[var(--border-default)]"
                          }`}>
                            {isExplicitlySelected && (
                              <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                              </svg>
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <span className="text-[13px] font-medium text-[var(--text-primary)]">
                              {rule.indicator}
                            </span>
                            <span className="text-[11px] text-[var(--text-tertiary)] ml-2">
                              {rule.operator} {rule.min != null && rule.max != null ? `${rule.min}–${rule.max}` : rule.value ?? ""}
                            </span>
                          </div>
                          <span className="text-[11px] text-[var(--text-tertiary)] capitalize shrink-0">
                            {(rule.category || "").replace("_", " ")}
                          </span>
                          <span className="text-[12px] font-mono font-semibold text-[var(--accent-primary)] shrink-0">
                            {rule.points} pts
                          </span>
                        </div>
                      );
                    })}
                    {(config.scoring?.selected_rule_ids ?? []).length > 0 && (
                      <button
                        className="text-[11px] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] mt-1"
                        onClick={() => setConfig((c: any) => ({ ...c, scoring: { ...c.scoring, selected_rule_ids: [] } }))}
                        data-testid="scoring-rules-clear-selection"
                      >
                        Clear selection (use all rules)
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="p-4 text-center bg-[var(--bg-secondary)] rounded-lg border border-dashed border-[var(--border-subtle)]">
                    <Target className="w-6 h-6 text-[var(--text-tertiary)] mx-auto mb-2" />
                    <p className="text-[12px] text-[var(--text-tertiary)]">
                      No global scoring rules configured. Go to{" "}
                      <a href="/settings/score" className="text-[var(--accent-primary)] hover:underline">
                        Settings → Score Engine
                      </a>{" "}
                      to add scoring rules.
                    </p>
                  </div>
                )}
              </div>

              {/* Alpha Score Weights */}
              <div className="space-y-3 pt-4 border-t border-[var(--border-subtle)]">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-[var(--text-primary)]">Alpha Score Weights</h3>
                    <p className="text-[12px] text-[var(--text-secondary)]">Customize how the Alpha Score categories are weighted</p>
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

                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-[var(--text-secondary)]">Logic</span>
                          <select
                            className="input h-8 w-20 text-[12px]"
                            value={block.logic || "AND"}
                            onChange={(e) => updateBlock(block.id, "logic", e.target.value)}
                          >
                            <option value="AND">AND</option>
                            <option value="OR">OR</option>
                          </select>
                        </div>
                        <button
                          type="button"
                          className="btn btn-secondary text-[11px] px-2.5 py-1"
                          onClick={() => addBlockCondition(block.id)}
                        >
                          <Plus className="w-3.5 h-3.5 mr-1" />
                          Condition
                        </button>
                      </div>

                      {/* Timeframe / Period overrides */}
                      {block.conditions.some((condition) => {
                        const reference = condition.type === "comparison" ? condition.left : condition.indicator;
                        return reference ? !NO_TF_INDICATORS.has(reference) : false;
                      }) && (
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
                          <div className="text-[11px] text-[var(--text-tertiary)] flex items-end pb-2">
                            Shared across this block rule
                          </div>
                        </div>
                      )}

                      <div className="space-y-2">
                        {block.conditions.map((condition) => (
                          <div key={condition.id} className="flex items-center gap-2 flex-wrap rounded-md border border-[var(--border-subtle)] p-2">
                            <select
                              className="input h-8 text-[12px] min-w-[120px]"
                              value={condition.type}
                              onChange={(e) => {
                                const nextCondition = createRuleCondition(e.target.value as RuleConditionType);
                                updateBlockCondition(block.id, condition.id, {
                                  type: nextCondition.type,
                                  indicator: nextCondition.indicator,
                                  left: nextCondition.left,
                                  right: nextCondition.right,
                                  operator: nextCondition.operator,
                                  value: nextCondition.value,
                                  min: nextCondition.min,
                                  max: nextCondition.max,
                                });
                              }}
                            >
                              {RULE_TYPE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                              ))}
                            </select>

                            {condition.type === "comparison" ? (
                              <>
                                <select
                                  className="input h-8 text-[12px] min-w-[120px]"
                                  value={condition.left || "price"}
                                  onChange={(e) => updateBlockCondition(block.id, condition.id, { left: e.target.value })}
                                >
                                  {NUMERIC_RULE_INDICATORS.map((indicator) => (
                                    <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
                                  ))}
                                </select>
                                <select
                                  className="input h-8 text-[12px] w-20"
                                  value={condition.operator}
                                  onChange={(e) => updateBlockCondition(block.id, condition.id, { operator: e.target.value })}
                                >
                                  {COMPARISON_OPERATORS.map((operator) => (
                                    <option key={operator} value={operator}>{operator}</option>
                                  ))}
                                </select>
                                <select
                                  className="input h-8 text-[12px] min-w-[120px]"
                                  value={condition.right || "ema9"}
                                  onChange={(e) => updateBlockCondition(block.id, condition.id, { right: e.target.value })}
                                >
                                  {NUMERIC_RULE_INDICATORS.map((indicator) => (
                                    <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
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
                                  {BOOLEAN_RULE_INDICATORS.map((indicator) => (
                                    <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
                                  ))}
                                </select>
                                <select
                                  className="input h-8 text-[12px] w-24"
                                  value={condition.operator === "is_false" ? "false" : "true"}
                                  onChange={(e) => {
                                    const booleanValue = e.target.value === "true";
                                    updateBlockCondition(block.id, condition.id, {
                                      operator: booleanValue ? "is_true" : "is_false",
                                      value: booleanValue,
                                    });
                                  }}
                                >
                                  <option value="true">True</option>
                                  <option value="false">False</option>
                                </select>
                              </>
                            ) : (
                              <>
                                <select
                                  className="input h-8 text-[12px] min-w-[140px]"
                                  value={condition.indicator || "rsi"}
                                  onChange={(e) => updateBlockCondition(block.id, condition.id, { indicator: e.target.value })}
                                >
                                  {NUMERIC_RULE_INDICATORS.map((indicator) => (
                                    <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
                                  ))}
                                </select>
                                <select
                                  className="input h-8 text-[12px] w-24"
                                  value={condition.operator}
                                  onChange={(e) => {
                                    const operator = e.target.value;
                                    updateBlockCondition(block.id, condition.id, {
                                      operator,
                                      value: operator === "between" ? undefined : condition.value ?? 0,
                                      min: operator === "between" ? Number(condition.value ?? condition.min ?? 0) : undefined,
                                      max: operator === "between" ? Number(condition.max ?? 100) : undefined,
                                    });
                                  }}
                                >
                                  {THRESHOLD_OPERATORS.map((operator) => (
                                    <option key={operator} value={operator}>{operator === "between" ? "between" : operator}</option>
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
                              type="button"
                              onClick={() => removeBlockCondition(block.id, condition.id)}
                              className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)] ml-auto"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>

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
                      className="input h-8 text-[12px] w-32"
                      value={trig.type}
                      onChange={(e) => {
                        const nextCondition = createRuleCondition(e.target.value as RuleConditionType);
                        updateTrigger(trig.id, "type", nextCondition.type);
                        updateTrigger(trig.id, "indicator", nextCondition.indicator);
                        updateTrigger(trig.id, "left", nextCondition.left);
                        updateTrigger(trig.id, "right", nextCondition.right);
                        updateTrigger(trig.id, "operator", nextCondition.operator);
                        updateTrigger(trig.id, "value", nextCondition.value);
                        updateTrigger(trig.id, "min", nextCondition.min);
                        updateTrigger(trig.id, "max", nextCondition.max);
                      }}
                    >
                      {RULE_TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                    {trig.type === "comparison" ? (
                      <>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.left || "price"}
                          onChange={(e) => updateTrigger(trig.id, "left", e.target.value)}
                        >
                          {NUMERIC_RULE_INDICATORS.map((indicator) => (
                            <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-20"
                          value={trig.operator}
                          onChange={(e) => updateTrigger(trig.id, "operator", e.target.value)}
                        >
                          {COMPARISON_OPERATORS.map((operator) => (
                            <option key={operator} value={operator}>{operator}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.right || "ema9"}
                          onChange={(e) => updateTrigger(trig.id, "right", e.target.value)}
                        >
                          {NUMERIC_RULE_INDICATORS.map((indicator) => (
                            <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
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
                          {BOOLEAN_RULE_INDICATORS.map((indicator) => (
                            <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
                          ))}
                        </select>
                        <select
                          className="input h-8 text-[12px] w-24"
                          value={trig.operator === "is_false" ? "false" : "true"}
                          onChange={(e) => {
                            const booleanValue = e.target.value === "true";
                            updateTrigger(trig.id, "operator", booleanValue ? "is_true" : "is_false");
                            updateTrigger(trig.id, "value", booleanValue);
                          }}
                        >
                          <option value="true">True</option>
                          <option value="false">False</option>
                        </select>
                      </>
                    ) : (
                      <>
                        <select
                          className="input h-8 text-[12px] w-36"
                          value={trig.indicator || "rsi"}
                          onChange={(e) => updateTrigger(trig.id, "indicator", e.target.value)}
                        >
                          {NUMERIC_RULE_INDICATORS.map((indicator) => (
                            <option key={indicator.value} value={indicator.value}>{indicator.label}</option>
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
                          {THRESHOLD_OPERATORS.map((operator) => (
                            <option key={operator} value={operator}>{operator === "between" ? "entre" : operator}</option>
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
                            value={String(trig.value ?? "")}
                            onChange={(e) => {
                              const num = parseFloat(e.target.value);
                              updateTrigger(trig.id, "value", isNaN(num) ? e.target.value : num);
                            }}
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
                        title={`Timeframe (default: ${config.default_timeframe || "5m"})`}
                      >
                        <option value="">{config.default_timeframe || "5m"}</option>
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
                    <div className="flex items-center justify-between gap-3 mb-2">
                      <p className="text-[11px] text-[var(--text-tertiary)] font-semibold uppercase tracking-wider">Logic Preview</p>
                      <button
                        type="button"
                        className="btn btn-secondary text-[11px] px-2.5 py-1"
                        onClick={() => {
                          setEntryLogicPreview(autoEntryLogicPreview);
                          setConfig((c: any) => ({
                            ...c,
                            entry_triggers: {
                              ...c.entry_triggers,
                              logic_preview_text: undefined,
                            },
                          }));
                        }}
                      >
                        Reset Auto
                      </button>
                    </div>
                    <textarea
                      className="input min-h-[220px] text-[12px] font-mono text-[var(--text-secondary)]"
                      value={entryLogicPreview}
                      onChange={(e) => {
                        const value = e.target.value;
                        setEntryLogicPreview(value);
                        setConfig((c: any) => ({
                          ...c,
                          entry_triggers: {
                            ...c.entry_triggers,
                            logic_preview_text: value,
                          },
                        }));
                      }}
                    />
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
