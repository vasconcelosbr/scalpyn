"use client";

import { useState, useEffect, useRef } from "react";
import { AlertCircle, FileJson, Save, RefreshCw, Plus, Trash2, Upload, X } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

const INDICATORS = [
  "price", "market_cap", "change_24h", "volume_24h",
  "ema5", "ema9", "ema21", "ema50", "ema200",
  "alpha_score", "score", "liquidity_score", "momentum_score",
  "rsi", "adx", "macd", "macd_signal", "macd_histogram",
  "ema_trend", "adx_acceleration", "taker_ratio", "buy_pressure",
  "taker_buy_volume", "taker_sell_volume", "volume_spike", "volume_delta",
  "di_plus", "di_minus", "di_trend", "spread_pct", "orderbook_depth_usdt",
  "orderbook_pressure", "bid_ask_imbalance", "bb_width", "stoch_k", "stoch_d",
  "vwap_distance_pct", "obv", "atr", "atr_pct", "atr_percent", "psar_trend",
  "zscore", "funding_rate", "ema9_distance_pct", "ema9_gt_ema21",
  "ema9_gt_ema50", "ema50_gt_ema200", "ema_full_alignment",
];

const OPERATORS = ["<=", ">=", "<", ">", "=", "==", "!=", "between", "is_true", "is_false", "ema9>ema50>ema200", "ema9>ema50", "ema50>ema200", "di+>di-", "di->di+", ">prev+", ">prev"];
type CategoryKey = "liquidity" | "market_structure" | "momentum" | "signal";

const CATEGORY_OPTIONS = [
  { value: "liquidity", label: "liquidity" },
  { value: "market_structure", label: "market structure" },
  { value: "momentum", label: "momentum" },
  { value: "signal", label: "signal" },
];
const DEFAULT_WEIGHTS: Record<CategoryKey, number> = { liquidity: 35, market_structure: 25, momentum: 25, signal: 15 };
const DEFAULT_THRESHOLDS = { strong_buy: 80, buy: 65, neutral: 40 };
const DEFAULT_RULE_CATEGORIES: Record<string, string> = {
  price: "market_structure",
  market_cap: "liquidity",
  change_24h: "momentum",
  volume_spike: "liquidity",
  volume_24h: "liquidity",
  spread_pct: "liquidity",
  orderbook_depth_usdt: "liquidity",
  orderbook_pressure: "liquidity",
  bid_ask_imbalance: "liquidity",
  obv: "liquidity",
  buy_pressure: "liquidity",        // buy/(buy+sell), [0, 1]
  taker_buy_volume: "liquidity",
  taker_sell_volume: "liquidity",
  taker_ratio: "liquidity",         // buy/(buy+sell), [0, 1] — threshold around 0.5 (#82)
  adx: "market_structure",
  ema_trend: "market_structure",
  ema5: "market_structure",
  ema9: "market_structure",
  ema21: "market_structure",
  ema50: "market_structure",
  ema200: "market_structure",
  atr: "market_structure",
  atr_pct: "market_structure",
  atr_percent: "market_structure",
  psar_trend: "market_structure",
  bb_width: "market_structure",
  di_plus: "market_structure",
  di_minus: "market_structure",
  di_trend: "market_structure",
  alpha_score: "signal",
  score: "signal",
  liquidity_score: "liquidity",
  momentum_score: "momentum",
  rsi: "momentum",
  macd: "momentum",
  macd_signal: "momentum",
  macd_histogram: "momentum",
  stoch_k: "momentum",
  stoch_d: "momentum",
  zscore: "momentum",
  vwap_distance_pct: "momentum",
  ema9_distance_pct: "momentum",
  adx_acceleration: "signal",
  volume_delta: "signal",
  funding_rate: "signal",
  ema9_gt_ema21: "signal",
  ema9_gt_ema50: "signal",
  ema50_gt_ema200: "signal",
  ema_full_alignment: "signal",
};

// Indicators where "between" range is the most common use-case
const RANGE_INDICATORS = new Set(["rsi", "stoch_k", "stoch_d", "adx", "vwap_distance_pct", "bb_width", "ema9_distance_pct", "atr_pct", "atr_percent", "zscore", "funding_rate"]);
const BOOLEAN_INDICATORS = new Set(["ema_full_alignment", "ema9_gt_ema21", "ema9_gt_ema50", "ema50_gt_ema200", "di_trend"]);
const VALUELESS_OPERATORS = new Set(["is_true", "is_false", "ema9>ema50>ema200", "ema9>ema50", "ema50>ema200", "di+>di-", "di->di+", ">prev"]);

interface ScoreRule {
  id: string;
  indicator: string;
  operator: string;
  value?: number | string | boolean | null;
  min?: number | null;
  max?: number | null;
  points: number;
  category: CategoryKey | string;
}

interface ScoreImportResult {
  rules: ScoreRule[];
  weights?: Record<CategoryKey, number>;
  thresholds?: { strong_buy: number; buy: number; neutral: number };
  topN?: number;
  minScore?: number;
}

const SCORE_JSON_TEMPLATE = `{
  "weights": {
    "liquidity": 35,
    "market_structure": 25,
    "momentum": 25,
    "signal": 15
  },
  "thresholds": {
    "strong_buy": 80,
    "buy": 65,
    "neutral": 40
  },
  "auto_select_top_n": 5,
  "auto_select_min_score": 80,
  "scoring_rules": [
    {
      "id": "rule_ema_trend_ema9_gt_ema50",
      "indicator": "ema_trend",
      "operator": "ema9>ema50",
      "points": 10,
      "category": "market_structure"
    },
    {
      "id": "rule_adx_ge_25",
      "indicator": "adx",
      "operator": ">=",
      "value": 25,
      "points": 12,
      "category": "market_structure"
    },
    {
      "id": "rule_taker_ratio_ge_055",
      "indicator": "taker_ratio",
      "operator": ">=",
      "value": 0.55,
      "points": 8,
      "category": "liquidity"
    }
  ]
}`;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toNumber(value: unknown, fallback: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function toNullableValue(value: unknown): number | string | boolean | null {
  if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  return null;
}

function isNumericString(value: string): boolean {
  return value.trim() !== "" && Number.isFinite(Number(value));
}

function makeRuleId(indicator: string, operator: string, index: number): string {
  const slug = `${indicator}_${operator}_${index + 1}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return `rule_${slug}`;
}

function normalizeImportedRule(raw: unknown, index: number): ScoreRule {
  if (!isRecord(raw)) {
    throw new Error(`scoring_rules[${index}] deve ser um objeto`);
  }

  const indicator = String(raw.indicator || "").trim();
  if (!indicator) {
    throw new Error(`scoring_rules[${index}].indicator é obrigatório`);
  }
  if (!INDICATORS.includes(indicator)) {
    throw new Error(`scoring_rules[${index}].indicator inválido: ${indicator}`);
  }

  const operator = String(raw.operator || (BOOLEAN_INDICATORS.has(indicator) ? "is_true" : RANGE_INDICATORS.has(indicator) ? "between" : ">=")).trim();
  if (!OPERATORS.includes(operator)) {
    throw new Error(`scoring_rules[${index}].operator inválido: ${operator}`);
  }

  const category = String(raw.category || DEFAULT_RULE_CATEGORIES[indicator] || "momentum").trim();
  const rule: ScoreRule = {
    id: String(raw.id || makeRuleId(indicator, operator, index)),
    indicator,
    operator,
    points: toNumber(raw.points, 0),
    category,
  };

  if (operator === "between") {
    rule.min = toNumber(raw.min, 0);
    rule.max = toNumber(raw.max, 100);
    rule.value = null;
  } else if (operator === "is_true") {
    rule.value = true;
  } else if (operator === "is_false") {
    rule.value = false;
  } else if (VALUELESS_OPERATORS.has(operator)) {
    rule.value = null;
  } else {
    rule.value = toNullableValue(raw.value);
  }

  return rule;
}

function normalizeWeights(raw: unknown): Record<CategoryKey, number> | undefined {
  if (!isRecord(raw)) return undefined;
  return {
    liquidity: toNumber(raw.liquidity, 35),
    market_structure: toNumber(raw.market_structure, 25),
    momentum: toNumber(raw.momentum, 25),
    signal: toNumber(raw.signal, 15),
  };
}

function normalizeThresholds(raw: unknown): ScoreImportResult["thresholds"] | undefined {
  if (!isRecord(raw)) return undefined;
  return {
    strong_buy: toNumber(raw.strong_buy, 80),
    buy: toNumber(raw.buy, 65),
    neutral: toNumber(raw.neutral, 40),
  };
}

function parseScoreImport(text: string): ScoreImportResult {
  const parsed: unknown = JSON.parse(text);
  const payload = Array.isArray(parsed) ? { scoring_rules: parsed } : parsed;
  if (!isRecord(payload)) {
    throw new Error('JSON deve ser um objeto com "scoring_rules" ou um array de regras');
  }

  const rawRules = payload.scoring_rules || payload.rules || payload.score_rules;
  if (!Array.isArray(rawRules) || rawRules.length === 0) {
    throw new Error('Informe "scoring_rules": [...] com ao menos uma regra');
  }
  if (rawRules.length > 300) {
    throw new Error(`Máximo 300 scoring_rules por importação. JSON tem ${rawRules.length}.`);
  }

  return {
    rules: rawRules.map(normalizeImportedRule),
    weights: normalizeWeights(payload.weights),
    thresholds: normalizeThresholds(payload.thresholds),
    topN: payload.auto_select_top_n == null ? undefined : toNumber(payload.auto_select_top_n, 5),
    minScore: payload.auto_select_min_score == null ? undefined : toNumber(payload.auto_select_min_score, 80),
  };
}

function getOperatorHint(indicator: string): string {
  if (indicator === "ema_trend") return "ema9>ema50 | ema50>ema200 | ema9>ema50>ema200";
  if (indicator === "di_trend") return "di+>di- | di->di+";
  if (BOOLEAN_INDICATORS.has(indicator)) return "is_true | is_false | == | !=";
  if (indicator === "adx_acceleration") return ">prev+ | >prev";
  if (RANGE_INDICATORS.has(indicator)) return "between | <= | >= | < | > | == | !=";
  return "<= | >= | < | > | == | !=";
}

const SCORE_INDICATOR_REFERENCE = INDICATORS.map((indicator) => ({
  indicator,
  category: DEFAULT_RULE_CATEGORIES[indicator] || "momentum",
  operators: getOperatorHint(indicator),
}));

export default function ScoreEngineSettings() {
  const { config, updateConfig, isLoading } = useConfig("score");
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [rules, setRules] = useState<ScoreRule[]>([]);
  const [thresholds, setThresholds] = useState(DEFAULT_THRESHOLDS);
  const [topN, setTopN] = useState(5);
  const [minScore, setMinScore] = useState(80);
  const [saving, setSaving] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState(SCORE_JSON_TEMPLATE);
  const [importError, setImportError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [importSaving, setImportSaving] = useState(false);
  const importFileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      /* eslint-disable react-hooks/set-state-in-effect */
      setWeights(config.weights || DEFAULT_WEIGHTS);
      setRules((config.scoring_rules || []).map((rule: ScoreRule) => ({
        ...rule,
        category: rule.category || DEFAULT_RULE_CATEGORIES[rule.indicator] || "momentum",
      })));
      setThresholds(config.thresholds || DEFAULT_THRESHOLDS);
      setTopN(config.auto_select_top_n || 5);
      setMinScore(config.auto_select_min_score || 80);
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateConfig({
        weights, scoring_rules: rules, thresholds,
        auto_select_top_n: topN, auto_select_min_score: minScore,
      });
    } catch (e) { console.error(e); }
    setSaving(false);
  };

  const addRule = () => {
    setRules([...rules, {
      id: `rule_${Date.now()}`,
      indicator: "rsi",
      operator: "between",
      min: 30,
      max: 60,
      value: null,
      points: 10,
      category: "momentum",
    }]);
  };

  const removeRule = (id: string) => setRules((currentRules) => currentRules.filter((r) => r.id !== id));

  const updateRule = (id: string, field: keyof ScoreRule, value: ScoreRule[keyof ScoreRule]) => {
    setRules((currentRules) => currentRules.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
  };

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.name.endsWith(".json")) {
      setImportError("Arquivo deve ter extensão .json");
      return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
      setImportText(String(ev.target?.result ?? ""));
      setImportError(null);
    };
    reader.readAsText(file);
  };

  const handleImportJson = async () => {
    let imported: ScoreImportResult;
    try {
      imported = parseScoreImport(importText);
    } catch (err: unknown) {
      setImportError(err instanceof Error ? err.message : String(err));
      return;
    }

    const nextWeights = imported.weights ?? weights;
    const nextThresholds = imported.thresholds ?? thresholds;
    const nextTopN = imported.topN ?? topN;
    const nextMinScore = imported.minScore ?? minScore;

    setImportSaving(true);
    try {
      await updateConfig({
        weights: nextWeights,
        scoring_rules: imported.rules,
        thresholds: nextThresholds,
        auto_select_top_n: nextTopN,
        auto_select_min_score: nextMinScore,
      });
      setRules(imported.rules);
      setWeights(nextWeights);
      setThresholds(nextThresholds);
      setTopN(nextTopN);
      setMinScore(nextMinScore);
      setImportError(null);
      setImportOpen(false);
      setImportNotice(`${imported.rules.length} regras importadas e salvas na matriz global. Os IDs já podem ser associados aos profiles.`);
    } catch (err: unknown) {
      setImportError(`Falha ao salvar a matriz: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setImportSaving(false);
    }
  };

  // Parse numeric input, preserving zero
  const parseNum = (v: string, asInt = false) => {
    if (v === "" || v === "-") return null;
    const n = asInt ? parseInt(v) : parseFloat(v);
    return isNaN(n) ? null : n;
  };

  if (isLoading) {
    return <div className="p-8"><div className="skeleton h-8 w-64 mb-4" /><div className="skeleton h-96 w-full" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Score Engine Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Configure Alpha Score weights, scoring rules, and classification thresholds.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {importNotice && (
        <div className="rounded-lg border border-[var(--accent-primary)]/25 bg-[var(--accent-primary)]/8 px-4 py-3 text-[12px] text-[var(--text-secondary)] flex items-center justify-between gap-3">
          <span>{importNotice}</span>
          <button className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)]" onClick={() => setImportNotice(null)}>
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Scoring Rules */}
      <div className="card">
        <div className="card-header">
          <h3>Scoring Rules</h3>
          <div className="flex items-center gap-2">
            <button onClick={() => setImportOpen(true)} className="btn btn-secondary text-[12px] px-3 py-1.5">
              <FileJson className="w-3.5 h-3.5 mr-1" />
              Import JSON
            </button>
            <button onClick={addRule} className="btn btn-secondary text-[12px] px-3 py-1.5"><Plus className="w-3.5 h-3.5 mr-1" />Add Rule</button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Indicator</th>
                <th>Operator</th>
                <th>Value / Range</th>
                <th>Points</th>
                <th>Category Weights</th>
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  {/* Indicator */}
                  <td>
                    <select
                      className="input h-8 text-[13px] w-36"
                      value={rule.indicator}
                      onChange={(e) => {
                        const ind = e.target.value;
                        const op = BOOLEAN_INDICATORS.has(ind) ? "is_true"
                          : RANGE_INDICATORS.has(ind) && rule.operator === "between" ? "between"
                          : RANGE_INDICATORS.has(ind) && !["<=",">=","<",">","=","==","!=","between"].includes(rule.operator) ? "between"
                          : rule.operator;
                        updateRule(rule.id, "indicator", ind);
                        if (op !== rule.operator) updateRule(rule.id, "operator", op);
                        updateRule(rule.id, "category", DEFAULT_RULE_CATEGORIES[ind] || rule.category || "momentum");
                      }}
                    >
                      {INDICATORS.map((i) => (
                        <option key={i} value={i}>{i}</option>
                      ))}
                    </select>
                  </td>

                  {/* Operator */}
                  <td>
                    <select
                      className="input h-8 text-[13px] w-40"
                      value={rule.operator}
                      onChange={(e) => updateRule(rule.id, "operator", e.target.value)}
                    >
                      {OPERATORS.map((o) => (
                        <option key={o} value={o}>{o}</option>
                      ))}
                    </select>
                  </td>

                  {/* Value / Range */}
                  <td>
                    {rule.operator === "between" ? (
                      <div className="flex items-center gap-1">
                        <input
                          type="number"
                          placeholder="Min"
                          className="input numeric h-8 w-16 text-[13px]"
                          value={rule.min ?? ""}
                          onChange={(e) => updateRule(rule.id, "min", parseNum(e.target.value))}
                          data-testid={`rule-min-${rule.id}`}
                        />
                        <span className="text-[var(--text-secondary)] text-[11px]">–</span>
                        <input
                          type="number"
                          placeholder="Max"
                          className="input numeric h-8 w-16 text-[13px]"
                          value={rule.max ?? ""}
                          onChange={(e) => updateRule(rule.id, "max", parseNum(e.target.value))}
                          data-testid={`rule-max-${rule.id}`}
                        />
                      </div>
                    ) : VALUELESS_OPERATORS.has(rule.operator) ? (
                      <span className="text-[11px] text-[var(--text-tertiary)]">auto</span>
                    ) : (
                      <input
                        type={typeof rule.value === "string" && !isNumericString(rule.value) ? "text" : "number"}
                        className="input numeric h-8 w-20 text-[13px]"
                        value={typeof rule.value === "number" || typeof rule.value === "string" ? rule.value : ""}
                        onChange={(e) => updateRule(rule.id, "value", e.target.type === "text" ? e.target.value : parseNum(e.target.value))}
                        data-testid={`rule-value-${rule.id}`}
                      />
                    )}
                  </td>

                  {/* Points */}
                  <td>
                    <input
                      type="number"
                      className="input numeric h-8 w-16 text-[13px]"
                      value={rule.points ?? ""}
                      onChange={(e) => updateRule(rule.id, "points", parseNum(e.target.value, true) ?? 0)}
                      data-testid={`rule-points-${rule.id}`}
                    />
                  </td>

                  <td>
                    <select
                      className="input h-8 text-[13px] w-40"
                      value={rule.category || DEFAULT_RULE_CATEGORIES[rule.indicator] || "momentum"}
                      onChange={(e) => updateRule(rule.id, "category", e.target.value)}
                      data-testid={`rule-category-${rule.id}`}
                    >
                      {CATEGORY_OPTIONS.map((category) => (
                        <option key={category.value} value={category.value}>{category.label}</option>
                      ))}
                    </select>
                  </td>

                  <td>
                    <button
                      onClick={() => removeRule(rule.id)}
                      className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)]"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
              {rules.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center text-[var(--text-secondary)] text-[13px] py-6">
                    No scoring rules. Use Add Rule to create one.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
          <div className="w-full max-w-5xl max-h-[88vh] overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-base)] shadow-2xl">
            <div className="flex items-center justify-between gap-4 border-b border-[var(--border-subtle)] px-5 py-4">
              <div>
                <h2 className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
                  <FileJson className="w-5 h-5 text-[var(--accent-primary)]" />
                  Importar matriz de Score via JSON
                </h2>
                <p className="text-[12px] text-[var(--text-secondary)] mt-1">
                  Carregue os indicadores globais do Score Engine. Os IDs criados aqui podem ser usados em Strategy Profiles no campo profile_scoring.selected_rule_ids.
                </p>
              </div>
              <button className="btn-icon w-8 h-8" onClick={() => setImportOpen(false)}>
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_360px] max-h-[calc(88vh-132px)] overflow-hidden">
              <div className="p-5 overflow-auto space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <label className="label">JSON da matriz</label>
                  <button
                    className="btn btn-secondary text-[12px] px-3 py-1.5"
                    onClick={() => importFileRef.current?.click()}
                    data-testid="score-import-file-button"
                  >
                    <FileJson className="w-3.5 h-3.5 mr-1.5" />
                    Selecionar arquivo .json
                  </button>
                  <input
                    ref={importFileRef}
                    type="file"
                    accept=".json,application/json"
                    className="hidden"
                    onChange={handleImportFile}
                  />
                </div>
                <textarea
                  className="input min-h-[460px] w-full resize-y font-mono text-[11px] leading-relaxed"
                  value={importText}
                  onChange={(event) => setImportText(event.target.value)}
                  spellCheck={false}
                />
                {importError && (
                  <div className="flex items-start gap-2 rounded-lg border border-red-500/25 bg-red-500/8 px-3 py-2 text-[12px] text-red-400">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>{importError}</span>
                  </div>
                )}
              </div>

              <div className="border-l border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-5 overflow-auto space-y-4">
                <div>
                  <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Estrutura esperada</h3>
                  <p className="text-[11px] text-[var(--text-secondary)] mb-2">
                    O campo <code className="font-mono text-[var(--accent-primary)]">indicator</code> aceita somente os indicadores listados na referência abaixo. Qualquer outro valor é rejeitado antes de aplicar a matriz.
                  </p>
                  <pre className="rounded-lg bg-[var(--bg-base)] border border-[var(--border-subtle)] p-3 text-[10px] leading-relaxed text-[var(--text-secondary)] overflow-auto">{`{
  "weights": {
    "liquidity": 35,
    "market_structure": 25,
    "momentum": 25,
    "signal": 15
  },
  "thresholds": {
    "strong_buy": 80,
    "buy": 65,
    "neutral": 40
  },
  "auto_select_top_n": 5,
  "auto_select_min_score": 80,
  "scoring_rules": [
    {
      "id": "rule_adx_ge_25",
      "indicator": "adx",
      "operator": ">=",
      "value": 25,
      "points": 12,
      "category": "market_structure"
    }
  ]
}`}</pre>
                </div>

                <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-base)] overflow-hidden">
                  <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-3 py-2">
                    <div>
                      <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">Indicadores disponíveis</h3>
                      <p className="text-[10px] text-[var(--text-tertiary)]">{SCORE_INDICATOR_REFERENCE.length} valores aceitos em scoring_rules[].indicator</p>
                    </div>
                    <span className="rounded bg-[var(--accent-primary)]/10 px-2 py-1 text-[10px] font-semibold text-[var(--accent-primary)]">catálogo score</span>
                  </div>
                  <div className="max-h-64 overflow-auto">
                    <table className="w-full text-[11px]">
                      <thead className="sticky top-0 bg-[var(--bg-tertiary)]">
                        <tr className="border-b border-[var(--border-subtle)]">
                          <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">indicator</th>
                          <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">category</th>
                          <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">operadores</th>
                        </tr>
                      </thead>
                      <tbody>
                        {SCORE_INDICATOR_REFERENCE.map((item) => (
                          <tr key={item.indicator} className="border-b border-[var(--border-subtle)]/60 last:border-0">
                            <td className="px-3 py-1.5 font-mono font-semibold text-[var(--text-primary)]">{item.indicator}</td>
                            <td className="px-3 py-1.5 text-[var(--text-secondary)]">{item.category.replace("_", " ")}</td>
                            <td className="px-3 py-1.5 font-mono text-[10px] text-[var(--text-tertiary)]">{item.operators}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-base)] p-3 text-[11px] text-[var(--text-secondary)] space-y-2">
                  <p className="font-semibold text-[var(--text-primary)]">Uso nos profiles</p>
                  <p>Depois de salvar a matriz, use os mesmos IDs no import de Strategy Profiles:</p>
                  <pre className="rounded bg-[var(--bg-tertiary)] p-2 font-mono text-[10px] overflow-auto">{`{
  "profile_scoring": {
    "selected_rule_ids": [
      "rule_adx_ge_25",
      "rule_taker_ratio_ge_055"
    ]
  },
  "profiles": [...]
}`}</pre>
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-[var(--border-subtle)] px-5 py-4">
              <button className="btn btn-secondary" onClick={() => setImportOpen(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleImportJson} disabled={importSaving}>
                {importSaving ? <RefreshCw className="w-4 h-4 mr-2 animate-spin" /> : <Upload className="w-4 h-4 mr-2" />}
                {importSaving ? "Salvando..." : "Aplicar e salvar matriz"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Order flow indicator reference */}
      <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-hover)] px-4 py-3 text-[12px] text-[var(--text-secondary)] space-y-1">
        <span className="font-semibold text-[var(--text-primary)]">Order flow indicator reference:&nbsp;</span>
        <span><code className="font-mono text-[var(--color-profit)]">taker_ratio</code> = buy_vol / (buy+sell) &nbsp;→&nbsp; range 0–1, equilibrium = 0.5 &nbsp;(example threshold: taker_ratio &gt; 0.55)</span>
        <span className="px-2 text-[var(--border-subtle)]">|</span>
        <span><code className="font-mono text-blue-400">buy_pressure</code> = buy_vol / (buy+sell) &nbsp;→&nbsp; range 0–1, equilibrium = 0.5 &nbsp;(same value as taker_ratio)</span>
      </div>

      {/* Thresholds */}
      <div className="card">
        <div className="card-header"><h3>Classification Thresholds & Auto-Select</h3></div>
        <div className="card-body">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="space-y-1">
              <label className="label">Strong Buy ≥</label>
              <input type="number" className="input numeric h-9" value={thresholds.strong_buy} onChange={(e) => setThresholds({ ...thresholds, strong_buy: parseInt(e.target.value) || 0 })} />
            </div>
            <div className="space-y-1">
              <label className="label">Buy ≥</label>
              <input type="number" className="input numeric h-9" value={thresholds.buy} onChange={(e) => setThresholds({ ...thresholds, buy: parseInt(e.target.value) || 0 })} />
            </div>
            <div className="space-y-1">
              <label className="label">Neutral ≥</label>
              <input type="number" className="input numeric h-9" value={thresholds.neutral} onChange={(e) => setThresholds({ ...thresholds, neutral: parseInt(e.target.value) || 0 })} />
            </div>
            <div className="space-y-1">
              <label className="label">Top N Assets</label>
              <input type="number" className="input numeric h-9" value={topN} onChange={(e) => setTopN(parseInt(e.target.value) || 0)} />
            </div>
            <div className="space-y-1">
              <label className="label">Min Score</label>
              <input type="number" className="input numeric h-9" value={minScore} onChange={(e) => setMinScore(parseInt(e.target.value) || 0)} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
