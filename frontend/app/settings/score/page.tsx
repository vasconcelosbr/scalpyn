"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Plus, Trash2, Target } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

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
    setRules([...rules, { id: `rule_${Date.now()}`, indicator: "rsi", operator: "<=", value: 30, points: 20 }]);
  };

  const removeRule = (id: string) => setRules(rules.filter((r) => r.id !== id));

  const updateRule = (id: string, field: string, value: any) => {
    setRules(rules.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
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
          <h3>Scoring Rules</h3>
          <button onClick={addRule} className="btn btn-secondary text-[12px] px-3 py-1.5"><Plus className="w-3.5 h-3.5 mr-1" />Add Rule</button>
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Indicator</th>
                <th>Operator</th>
                <th>Value</th>
                <th>Points</th>
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td>
                    <select className="input h-8 text-[13px] w-36" value={rule.indicator} onChange={(e) => updateRule(rule.id, "indicator", e.target.value)}>
                      {["rsi", "adx", "ema_trend", "taker_ratio", "adx_acceleration", "volume_spike", "macd_signal"].map((i) => (
                        <option key={i} value={i}>{i}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select className="input h-8 text-[13px] w-36" value={rule.operator} onChange={(e) => updateRule(rule.id, "operator", e.target.value)}>
                      {["<=", ">=", "<", ">", "=", "ema9>ema50>ema200", "ema9>ema50", "ema50>ema200", ">prev+", ">prev"].map((o) => (
                        <option key={o} value={o}>{o}</option>
                      ))}
                    </select>
                  </td>
                  <td><input type="number" className="input numeric h-8 w-20 text-[13px]" value={rule.value ?? ""} onChange={(e) => updateRule(rule.id, "value", parseFloat(e.target.value) || null)} /></td>
                  <td><input type="number" className="input numeric h-8 w-16 text-[13px]" value={rule.points} onChange={(e) => updateRule(rule.id, "points", parseInt(e.target.value) || 0)} /></td>
                  <td><button onClick={() => removeRule(rule.id)} className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)]"><Trash2 className="w-3.5 h-3.5" /></button></td>
                </tr>
              ))}
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
              <input type="number" className="input numeric h-9" value={thresholds.strong_buy} onChange={(e) => setThresholds({ ...thresholds, strong_buy: parseInt(e.target.value) })} />
            </div>
            <div className="space-y-1">
              <label className="label">Buy ≥</label>
              <input type="number" className="input numeric h-9" value={thresholds.buy} onChange={(e) => setThresholds({ ...thresholds, buy: parseInt(e.target.value) })} />
            </div>
            <div className="space-y-1">
              <label className="label">Neutral ≥</label>
              <input type="number" className="input numeric h-9" value={thresholds.neutral} onChange={(e) => setThresholds({ ...thresholds, neutral: parseInt(e.target.value) })} />
            </div>
            <div className="space-y-1">
              <label className="label">Top N Assets</label>
              <input type="number" className="input numeric h-9" value={topN} onChange={(e) => setTopN(parseInt(e.target.value))} />
            </div>
            <div className="space-y-1">
              <label className="label">Min Score</label>
              <input type="number" className="input numeric h-9" value={minScore} onChange={(e) => setMinScore(parseInt(e.target.value))} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
