"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Plus, Trash2, ShieldOff, Zap } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";
import { useSearchParams } from "next/navigation";

type Tab = "blocks" | "entry_triggers";

const BLOCK_INDICATORS = [
  "rsi",
  "adx",
  "atr_pct",
  "spread_pct",
  "volume_24h",
  "funding_rate_abs",
  "ema_trend",
  "bb_width",
];

const TRIGGER_INDICATORS = [
  "alpha_score",
  "rsi",
  "adx",
  "volume_spike",
  "macd_signal",
  "ema_alignment",
  "stoch_k",
  "atr_pct",
  "vwap_distance_pct",
  "bb_width",
];

export default function BlockSettings() {
  const searchParams = useSearchParams();
  const defaultTab = (searchParams.get("tab") as Tab) ?? "blocks";

  const { config, updateConfig, isLoading } = useConfig("block");
  const [activeTab, setActiveTab] = useState<Tab>(defaultTab);
  const [blocks, setBlocks] = useState<any[]>([]);
  const [entryTriggers, setEntryTriggers] = useState<any[]>([]);
  const [entryLogic, setEntryLogic] = useState("AND");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      setBlocks(config.blocks || []);
      setEntryTriggers(config.entry_triggers || []);
      setEntryLogic(config.entry_logic || "AND");
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateConfig({ blocks, entry_triggers: entryTriggers, entry_logic: entryLogic });
    } catch (e) {
      console.error(e);
    }
    setSaving(false);
  };

  // ── Block helpers ──────────────────────────────────────────────────────────
  const addBlock = () =>
    setBlocks([
      ...blocks,
      {
        id: `b${Date.now()}`,
        name: "New Block",
        enabled: true,
        indicator: "rsi",
        type: "threshold",
        operator: ">",
        value: 20,
      },
    ]);
  const removeBlock = (id: string) => setBlocks(blocks.filter((b) => b.id !== id));
  const updateBlock = (id: string, field: string, value: any) =>
    setBlocks(blocks.map((b) => (b.id === id ? { ...b, [field]: value } : b)));

  // ── Entry trigger helpers ──────────────────────────────────────────────────
  const addTrigger = () =>
    setEntryTriggers([
      ...entryTriggers,
      {
        id: `t${Date.now()}`,
        indicator: "alpha_score",
        operator: ">",
        value: 70,
        required: false,
        enabled: true,
      },
    ]);
  const removeTrigger = (id: string) =>
    setEntryTriggers(entryTriggers.filter((t) => t.id !== id));
  const updateTrigger = (id: string, field: string, value: any) =>
    setEntryTriggers(
      entryTriggers.map((t) => (t.id === id ? { ...t, [field]: value } : t))
    );

  if (isLoading)
    return (
      <div className="p-8">
        <div className="skeleton h-96 w-full" />
      </div>
    );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            Block Rules
          </h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Hard vetos that prevent trade execution, plus entry trigger
            conditions (formerly Signal Rules) that must pass to allow entry.
          </p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? (
            <RefreshCw className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[var(--border-subtle)]">
        <button
          onClick={() => setActiveTab("blocks")}
          className={`flex items-center gap-2 px-4 py-2 text-[13px] font-medium transition-colors border-b-2 -mb-px ${
            activeTab === "blocks"
              ? "border-[var(--accent-primary)] text-[var(--accent-primary)]"
              : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          }`}
        >
          <ShieldOff className="w-4 h-4" />
          Block Rules
          <span className="ml-1 px-1.5 py-0.5 rounded-full bg-[var(--bg-hover)] text-[11px]">
            {blocks.filter((b) => b.enabled).length}
          </span>
        </button>
        <button
          onClick={() => setActiveTab("entry_triggers")}
          className={`flex items-center gap-2 px-4 py-2 text-[13px] font-medium transition-colors border-b-2 -mb-px ${
            activeTab === "entry_triggers"
              ? "border-[var(--accent-primary)] text-[var(--accent-primary)]"
              : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          }`}
        >
          <Zap className="w-4 h-4" />
          Entry Triggers
          <span className="ml-1 px-1.5 py-0.5 rounded-full bg-[var(--bg-hover)] text-[11px]">
            {entryTriggers.filter((t) => t.enabled).length}
          </span>
        </button>
      </div>

      {/* ── TAB: BLOCK RULES ─────────────────────────────────────────────────── */}
      {activeTab === "blocks" && (
        <>
          <div className="flex justify-end">
            <button onClick={addBlock} className="btn btn-secondary">
              <Plus className="w-4 h-4 mr-1" />
              Add Block
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {blocks.map((block) => (
              <div
                key={block.id}
                className={`card ${
                  block.enabled ? "border-l-4 border-l-[var(--color-loss)]" : "opacity-60"
                }`}
              >
                <div className="p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <input
                      type="text"
                      className="input h-8 text-[14px] font-semibold flex-1 mr-3"
                      value={block.name}
                      onChange={(e) => updateBlock(block.id, "name", e.target.value)}
                    />
                    <div className="flex items-center gap-2">
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

                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="label">Indicator</label>
                      <select
                        className="input h-8 text-[13px]"
                        value={block.indicator}
                        onChange={(e) => updateBlock(block.id, "indicator", e.target.value)}
                      >
                        {BLOCK_INDICATORS.map((i) => (
                          <option key={i} value={i}>
                            {i}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className="label">Type</label>
                      <select
                        className="input h-8 text-[13px]"
                        value={block.type}
                        onChange={(e) => updateBlock(block.id, "type", e.target.value)}
                      >
                        <option value="threshold">Threshold</option>
                        <option value="range">Range</option>
                        <option value="condition">Condition</option>
                      </select>
                    </div>
                  </div>

                  {block.type === "threshold" && (
                    <div className="flex items-center gap-2">
                      <select
                        className="input h-8 text-[13px] w-16"
                        value={block.operator}
                        onChange={(e) => updateBlock(block.id, "operator", e.target.value)}
                      >
                        {[">", "<", ">=", "<="].map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        className="input numeric h-8 w-24 text-[13px]"
                        value={block.value ?? 0}
                        onChange={(e) =>
                          updateBlock(block.id, "value", parseFloat(e.target.value))
                        }
                      />
                    </div>
                  )}

                  {block.type === "range" && (
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] text-[var(--text-secondary)]">Min</span>
                      <input
                        type="number"
                        className="input numeric h-8 w-20 text-[13px]"
                        value={block.min ?? 0}
                        onChange={(e) =>
                          updateBlock(block.id, "min", parseFloat(e.target.value))
                        }
                      />
                      <span className="text-[12px] text-[var(--text-secondary)]">Max</span>
                      <input
                        type="number"
                        className="input numeric h-8 w-20 text-[13px]"
                        value={block.max ?? 100}
                        onChange={(e) =>
                          updateBlock(block.id, "max", parseFloat(e.target.value))
                        }
                      />
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          {blocks.length === 0 && (
            <div className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
              No block rules defined. All trades will pass the block check.
            </div>
          )}
        </>
      )}

      {/* ── TAB: ENTRY TRIGGERS ──────────────────────────────────────────────── */}
      {activeTab === "entry_triggers" && (
        <>
          {/* Logic mode */}
          <div className="card">
            <div className="card-body flex items-center gap-4">
              <span className="text-[13px] font-semibold text-[var(--text-primary)]">
                Logic Mode:
              </span>
              <select
                className="input w-28 h-9 text-[13px]"
                value={entryLogic}
                onChange={(e) => setEntryLogic(e.target.value)}
              >
                <option value="AND">AND</option>
                <option value="OR">OR</option>
              </select>
              <span className="text-[12px] text-[var(--text-secondary)]">
                {entryLogic === "AND"
                  ? "All required + at least one optional must pass"
                  : "All required + any optional must pass"}
              </span>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h3>Entry Trigger Conditions</h3>
              <button
                onClick={addTrigger}
                className="btn btn-secondary text-[12px] px-3 py-1.5"
              >
                <Plus className="w-3.5 h-3.5 mr-1" />
                Add Trigger
              </button>
            </div>
            <div className="space-y-3 p-4">
              {entryTriggers.map((trig) => (
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
                    className="input h-8 text-[13px] w-36"
                    value={trig.indicator}
                    onChange={(e) =>
                      updateTrigger(trig.id, "indicator", e.target.value)
                    }
                  >
                    {TRIGGER_INDICATORS.map((i) => (
                      <option key={i} value={i}>
                        {i}
                      </option>
                    ))}
                  </select>
                  <select
                    className="input h-8 text-[13px] w-20"
                    value={trig.operator}
                    onChange={(e) =>
                      updateTrigger(trig.id, "operator", e.target.value)
                    }
                  >
                    {[">", "<", ">=", "<=", "=", "!="].map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    className="input h-8 text-[13px] w-20 font-mono"
                    value={trig.value ?? ""}
                    onChange={(e) => {
                      const num = parseFloat(e.target.value);
                      updateTrigger(
                        trig.id,
                        "value",
                        isNaN(num) ? e.target.value : num
                      );
                    }}
                  />
                  <label className="flex items-center gap-1.5 text-[12px] cursor-pointer">
                    <input
                      type="checkbox"
                      checked={trig.required}
                      onChange={(e) =>
                        updateTrigger(trig.id, "required", e.target.checked)
                      }
                      className="accent-[var(--accent-primary)]"
                    />
                    <span
                      className={
                        trig.required
                          ? "text-[var(--color-warning)] font-semibold"
                          : "text-[var(--text-secondary)]"
                      }
                    >
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
              {entryTriggers.length === 0 && (
                <div className="text-center py-8 text-[var(--text-tertiary)] text-[13px]">
                  No entry triggers defined. Trades will be allowed by default.
                </div>
              )}
            </div>
          </div>

          {/* Preview */}
          <div className="card">
            <div className="card-header">
              <h3>Entry Logic Preview</h3>
            </div>
            <div className="card-body">
              <pre className="text-[13px] font-mono text-[var(--text-secondary)] bg-[var(--bg-base)] p-4 rounded-[var(--radius-md)] overflow-x-auto">
                {`IF (\n`}
                {entryTriggers
                  .filter((t) => t.enabled && t.required)
                  .map((t) => `  [REQUIRED] ${t.indicator} ${t.operator} ${t.value}`)
                  .join("\n  AND\n")}
                {entryTriggers.filter((t) => t.enabled && t.required).length > 0 &&
                  entryTriggers.filter((t) => t.enabled && !t.required).length > 0 &&
                  `\n  AND (`}
                {entryTriggers
                  .filter((t) => t.enabled && !t.required)
                  .map((t) => `    ${t.indicator} ${t.operator} ${t.value}`)
                  .join(`\n    ${entryLogic}\n`)}
                {entryTriggers.filter((t) => t.enabled && !t.required).length > 0 &&
                  `\n  )`}
                {`\n) → ALLOW TRADE ENTRY`}
              </pre>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
