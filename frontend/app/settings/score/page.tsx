"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Plus, Trash2 } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

const INDICATORS = [
  "rsi", "adx", "ema_trend", "taker_ratio", "adx_acceleration",
  "volume_spike", "macd_signal", "macd_histogram",
  "di_plus", "di_minus", "di_trend", "spread_pct", "orderbook_depth_usdt",
  "bb_width", "stoch_k", "stoch_d", "vwap_distance_pct",
  "volume_24h", "obv", "atr", "atr_pct", "psar_trend", "zscore",
  "volume_delta", "funding_rate",
  "ema9_gt_ema50", "ema50_gt_ema200", "ema_full_alignment",
];

const OPERATORS = ["<=", ">=", "<", ">", "=", "between", "ema9>ema50>ema200", "ema9>ema50", "ema50>ema200", "di+>di-", "di->di+", ">prev+", ">prev"];

// Indicators where "between" range is the most common use-case
const RANGE_INDICATORS = new Set(["rsi", "stoch_k", "stoch_d", "adx", "vwap_distance_pct", "bb_width"]);

export default function ScoreEngineSettings() {
  const { config, updateConfig, isLoading } = useConfig("score");
  const [weights, setWeights] = useState({ liquidity: 25, market_structure: 25, momentum: 25, signal: 25 });
  const [rules, setRules] = useState<any[]>([]);
  const [thresholds, setThresholds] = useState({ strong_buy: 80, buy: 65, neutral: 40 });
  const [topN, setTopN] = useState(5);
  const [minScore, setMinScore] = useState(80);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      setWeights(config.weights || weights);
      setRules(config.scoring_rules || []);
      setThresholds(config.thresholds || thresholds);
      setTopN(config.auto_select_top_n || 5);
      setMinScore(config.auto_select_min_score || 80);
    }
  }, [config]);

  const weightSum = Object.values(weights).reduce((a, b) => a + b, 0);

  // Total points across all scoring rules
  const totalPoints = rules.reduce((sum, r) => sum + (Number(r.points) || 0), 0);

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
    setRules([...rules, { id: `rule_${Date.now()}`, indicator: "rsi", operator: "between", min: 30, max: 60, value: null, points: 10 }]);
  };

  const removeRule = (id: string) => setRules(rules.filter((r) => r.id !== id));

  const updateRule = (id: string, field: string, value: any) => {
    setRules(rules.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
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

      {/* Weights */}
      <div className="card">
        <div className="card-header"><h3>Category Weights</h3></div>
        <div className="card-body space-y-4">
          {(["liquidity", "market_structure", "momentum", "signal"] as const).map((key) => (
            <div key={key} className="flex items-center gap-4">
              <span className="text-[13px] font-medium text-[var(--text-secondary)] w-40 capitalize">{key.replace("_", " ")}</span>
              <input type="range" min={0} max={100} value={weights[key]} onChange={(e) => setWeights({ ...weights, [key]: parseInt(e.target.value) })} className="slider flex-1" />
              <span className="data-value text-[14px] w-12 text-right">{weights[key]}%</span>
            </div>
          ))}
          <div className={`text-[13px] font-semibold ${weightSum === 100 ? "text-[var(--color-profit)]" : "text-[var(--color-loss)]"}`}>
            Total: {weightSum}% {weightSum !== 100 && "(must equal 100%)"}
          </div>
          {/* Preview bar */}
          <div className="flex h-3 rounded-full overflow-hidden bg-[var(--bg-hover)]">
            <div style={{ width: `${weights.liquidity}%` }} className="bg-blue-500" />
            <div style={{ width: `${weights.market_structure}%` }} className="bg-purple-500" />
            <div style={{ width: `${weights.momentum}%` }} className="bg-amber-500" />
            <div style={{ width: `${weights.signal}%` }} className="bg-emerald-500" />
          </div>
        </div>
      </div>

      {/* Scoring Rules */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-3">
            <h3>Scoring Rules</h3>
            <span className={`text-[12px] font-semibold px-2 py-0.5 rounded-full ${totalPoints <= 100 ? "bg-[var(--color-profit)]/15 text-[var(--color-profit)]" : "bg-[var(--color-loss)]/15 text-[var(--color-loss)]"}`}>
              {totalPoints} / 100 pts
            </span>
          </div>
          <button onClick={addRule} className="btn btn-secondary text-[12px] px-3 py-1.5"><Plus className="w-3.5 h-3.5 mr-1" />Add Rule</button>
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Indicator</th>
                <th>Operator</th>
                <th>Value / Range</th>
                <th>Points</th>
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
                        // Auto-switch to "between" for range indicators
                        const op = RANGE_INDICATORS.has(ind) && rule.operator === "between" ? "between"
                          : RANGE_INDICATORS.has(ind) && !["<=",">=","<",">","=","between"].includes(rule.operator) ? "between"
                          : rule.operator;
                        updateRule(rule.id, "indicator", ind);
                        if (op !== rule.operator) updateRule(rule.id, "operator", op);
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
                    ) : (
                      <input
                        type="number"
                        className="input numeric h-8 w-20 text-[13px]"
                        value={rule.value ?? ""}
                        onChange={(e) => updateRule(rule.id, "value", parseNum(e.target.value))}
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
                  <td colSpan={5} className="text-center text-[var(--text-secondary)] text-[13px] py-6">
                    No scoring rules. Click "Add Rule" to create one.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
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
