"use client";

import { useState } from "react";
import { ArrowLeft, Save, Plus, Target, Check, AlertTriangle, Play } from "lucide-react";
import { apiPut } from "@/lib/api";
import { ConditionBuilder } from "./ConditionBuilder";
import { WeightSliders } from "./WeightSliders";

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

const DEFAULT_CONFIG = {
  filters: { logic: "AND", conditions: [] },
  scoring: { weights: { liquidity: 25, market_structure: 25, momentum: 25, signal: 25 } },
  signals: { logic: "AND", conditions: [] },
  block_rules: { blocks: [] },
  entry_triggers: { logic: "AND", conditions: [] },
};

function createRuleCondition() {
  return {
    id: `cond_${Date.now()}_${Math.random().toString(36).substring(7)}`,
    type: "threshold",
    indicator: "rsi",
    operator: "<",
    value: 60,
  };
}

export function BulkProfileBuilder({ selectedProfiles, onClose }: BulkProfileBuilderProps) {
  const [activeTab, setActiveTab] = useState<ActiveTab>("filters");
  const [config, setConfig] = useState<any>(DEFAULT_CONFIG);
  const [overwrite, setOverwrite] = useState(false);
  const [previewLog, setPreviewLog] = useState<any[] | null>(null);
  const [saving, setSaving] = useState(false);

  // Helper functions to update state
  const updateFilters = (conditions: any[]) => setConfig((c: any) => ({ ...c, filters: { ...c.filters, conditions } }));
  const updateSignals = (conditions: any[]) => setConfig((c: any) => ({ ...c, signals: { ...c.signals, conditions } }));
  const updateWeights = (weights: any) => setConfig((c: any) => ({ ...c, scoring: { ...c.scoring, weights } }));
  const updateEntryTriggers = (conditions: any[]) => setConfig((c: any) => ({ ...c, entry_triggers: { ...c.entry_triggers, conditions } }));
  const updateBlockRules = (blocks: any[]) => setConfig((c: any) => ({ ...c, block_rules: { ...c.block_rules, blocks } }));

  // Simulate injection
  const generatePreview = () => {
    const logs: any[] = [];

    selectedProfiles.forEach(profile => {
      const profileLogs: string[] = [];
      
      // Check Filters
      if (config.filters.conditions.length > 0) {
        config.filters.conditions.forEach((newCond: any) => {
          const field = newCond.field || newCond.indicator;
          const exists = profile.config?.filters?.conditions?.some((c: any) => (c.field || c.indicator) === field);
          if (exists && !overwrite) {
            profileLogs.push(`Filter '${field}' ignored (already exists)`);
          } else if (exists && overwrite) {
            profileLogs.push(`Filter '${field}' will be overwritten`);
          } else {
            profileLogs.push(`Filter '${field}' will be added`);
          }
        });
      }

      // Check Signals
      if (config.signals.conditions.length > 0) {
        config.signals.conditions.forEach((newCond: any) => {
          const field = newCond.field || newCond.indicator;
          const exists = profile.config?.signals?.conditions?.some((c: any) => (c.field || c.indicator) === field);
          if (exists && !overwrite) {
            profileLogs.push(`Signal '${field}' ignored (already exists)`);
          } else if (exists && overwrite) {
            profileLogs.push(`Signal '${field}' will be overwritten`);
          } else {
            profileLogs.push(`Signal '${field}' will be added`);
          }
        });
      }

      // Check Entry Triggers
      if (config.entry_triggers.conditions.length > 0) {
        config.entry_triggers.conditions.forEach((newCond: any) => {
          const field = newCond.field || newCond.indicator;
          const exists = profile.config?.entry_triggers?.conditions?.some((c: any) => (c.field || c.indicator) === field);
          if (exists && !overwrite) {
            profileLogs.push(`Entry Trigger '${field}' ignored (already exists)`);
          } else if (exists && overwrite) {
            profileLogs.push(`Entry Trigger '${field}' will be overwritten`);
          } else {
            profileLogs.push(`Entry Trigger '${field}' will be added`);
          }
        });
      }

      // Check Block Rules
      if (config.block_rules.blocks.length > 0) {
        config.block_rules.blocks.forEach((newBlock: any) => {
          const name = newBlock.name;
          const exists = profile.config?.block_rules?.blocks?.some((b: any) => b.name === name);
          if (exists && !overwrite) {
            profileLogs.push(`Block Rule '${name}' ignored (already exists)`);
          } else if (exists && overwrite) {
            profileLogs.push(`Block Rule '${name}' will be overwritten`);
          } else {
            profileLogs.push(`Block Rule '${name}' will be added`);
          }
        });
      }

      logs.push({
        profileId: profile.id,
        profileName: profile.name,
        messages: profileLogs.length > 0 ? profileLogs : ["No changes will be made"]
      });
    });

    setPreviewLog(logs);
  };

  const applyChanges = async () => {
    setSaving(true);
    let successCount = 0;
    let failCount = 0;

    for (const profile of selectedProfiles) {
      try {
        const updatedConfig = JSON.parse(JSON.stringify(profile.config || {}));
        
        // Ensure structure exists
        if (!updatedConfig.filters) updatedConfig.filters = { logic: "AND", conditions: [] };
        if (!updatedConfig.signals) updatedConfig.signals = { logic: "AND", conditions: [] };
        if (!updatedConfig.entry_triggers) updatedConfig.entry_triggers = { logic: "AND", conditions: [] };
        if (!updatedConfig.block_rules) updatedConfig.block_rules = { blocks: [] };
        if (!updatedConfig.scoring) updatedConfig.scoring = { weights: { liquidity: 25, market_structure: 25, momentum: 25, signal: 25 } };

        // Process Filters
        config.filters.conditions.forEach((newCond: any) => {
          const field = newCond.field || newCond.indicator;
          const idx = updatedConfig.filters.conditions.findIndex((c: any) => (c.field || c.indicator) === field);
          if (idx !== -1) {
            if (overwrite) updatedConfig.filters.conditions[idx] = newCond;
          } else {
            updatedConfig.filters.conditions.push(newCond);
          }
        });

        // Process Signals
        config.signals.conditions.forEach((newCond: any) => {
          const field = newCond.field || newCond.indicator;
          const idx = updatedConfig.signals.conditions.findIndex((c: any) => (c.field || c.indicator) === field);
          if (idx !== -1) {
            if (overwrite) updatedConfig.signals.conditions[idx] = newCond;
          } else {
            updatedConfig.signals.conditions.push(newCond);
          }
        });

        // Process Entry Triggers
        config.entry_triggers.conditions.forEach((newCond: any) => {
          const field = newCond.field || newCond.indicator;
          const idx = updatedConfig.entry_triggers.conditions.findIndex((c: any) => (c.field || c.indicator) === field);
          if (idx !== -1) {
            if (overwrite) updatedConfig.entry_triggers.conditions[idx] = newCond;
          } else {
            updatedConfig.entry_triggers.conditions.push(newCond);
          }
        });

        // Process Block Rules
        config.block_rules.blocks.forEach((newBlock: any) => {
          const name = newBlock.name;
          const idx = updatedConfig.block_rules.blocks.findIndex((b: any) => b.name === name);
          if (idx !== -1) {
            if (overwrite) updatedConfig.block_rules.blocks[idx] = newBlock;
          } else {
            updatedConfig.block_rules.blocks.push(newBlock);
          }
        });

        await apiPut(`/profiles/${profile.id}`, {
          ...profile,
          config: updatedConfig
        });
        successCount++;
      } catch (err) {
        console.error("Failed to update profile", profile.name, err);
        failCount++;
      }
    }

    setSaving(false);
    alert(`Applied successfully to ${successCount} profiles.${failCount > 0 ? ` Failed on ${failCount} profiles.` : ''}`);
    onClose();
  };

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
              Applying changes to {selectedProfiles.length} selected profiles
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
        {/* Main Content Area */}
        <div className="flex-1 overflow-y-auto bg-[var(--bg-primary)] p-6">
          
          <div className="flex space-x-1 mb-8 border-b border-[var(--border-default)]">
            {[
              { id: "filters", label: "Filters", count: config.filters.conditions.length },
              { id: "scoring", label: "Scoring" },
              { id: "signals", label: "Signals", count: config.signals.conditions.length },
              { id: "block_rules", label: "Block Rules", count: config.block_rules.blocks.length },
              { id: "entry_triggers", label: "Entry Triggers", count: config.entry_triggers.conditions.length },
            ].map((tab) => (
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

          {activeTab === "filters" && (
            <div className="space-y-6">
              <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6">
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Filter Conditions</h3>
                    <p className="text-[13px] text-[var(--text-secondary)]">These will be added to the selected profiles.</p>
                  </div>
                </div>
                <ConditionBuilder
                  conditions={config.filters.conditions}
                  onChange={(conds) => updateFilters(conds)}
                  defaultTimeframe="5m"
                />
              </div>
            </div>
          )}

          {activeTab === "signals" && (
            <div className="space-y-6">
              <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6">
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Signal Conditions</h3>
                    <p className="text-[13px] text-[var(--text-secondary)]">These will be added to the selected profiles.</p>
                  </div>
                </div>
                <ConditionBuilder
                  conditions={config.signals.conditions}
                  onChange={(conds) => updateSignals(conds)}
                  defaultTimeframe="5m"
                />
              </div>
            </div>
          )}

          {activeTab === "entry_triggers" && (
            <div className="space-y-6">
              <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6">
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Entry Triggers</h3>
                    <p className="text-[13px] text-[var(--text-secondary)]">These will be added to the selected profiles.</p>
                  </div>
                </div>
                <ConditionBuilder
                  conditions={config.entry_triggers.conditions}
                  onChange={(conds) => updateEntryTriggers(conds)}
                  defaultTimeframe="5m"
                />
              </div>
            </div>
          )}

          {activeTab === "block_rules" && (
            <div className="space-y-6">
              <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6">
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Block Rules</h3>
                    <p className="text-[13px] text-[var(--text-secondary)]">Define blocks to be added to the selected profiles.</p>
                  </div>
                  <button
                    className="btn btn-secondary text-[12px] h-8"
                    onClick={() => {
                      const newBlock = { id: `block_${Date.now()}`, name: "New Block", enabled: true, logic: "AND", conditions: [createRuleCondition()] };
                      updateBlockRules([...config.block_rules.blocks, newBlock]);
                    }}
                  >
                    <Plus className="w-4 h-4 mr-1" /> Add Block
                  </button>
                </div>
                
                <div className="space-y-4">
                  {config.block_rules.blocks.map((block: any, idx: number) => (
                    <div key={block.id} className="bg-[var(--bg-tertiary)] rounded-lg p-4 border border-[var(--border-default)]">
                      <div className="flex items-center justify-between mb-4">
                        <input
                          type="text"
                          value={block.name}
                          onChange={(e) => {
                            const newBlocks = [...config.block_rules.blocks];
                            newBlocks[idx].name = e.target.value;
                            updateBlockRules(newBlocks);
                          }}
                          className="input flex-1 max-w-[200px]"
                          placeholder="Block Name"
                        />
                        <button
                          className="p-2 text-red-500 hover:bg-red-500/10 rounded-lg transition-colors"
                          onClick={() => {
                            const newBlocks = [...config.block_rules.blocks];
                            newBlocks.splice(idx, 1);
                            updateBlockRules(newBlocks);
                          }}
                        >
                          <Target className="w-4 h-4" />
                        </button>
                      </div>
                      <ConditionBuilder
                        conditions={block.conditions || []}
                        onChange={(conds) => {
                          const newBlocks = [...config.block_rules.blocks];
                          newBlocks[idx].conditions = conds;
                          updateBlockRules(newBlocks);
                        }}
                        defaultTimeframe="5m"
                      />
                    </div>
                  ))}
                  {config.block_rules.blocks.length === 0 && (
                    <div className="text-center py-8 text-[var(--text-tertiary)] text-[13px]">
                      No block rules added yet.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === "scoring" && (
            <div className="space-y-6">
              <div className="bg-[var(--bg-secondary)] rounded-xl border border-[var(--border-subtle)] p-6">
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">Scoring Weights</h3>
                    <p className="text-[13px] text-[var(--text-secondary)]">
                      <AlertTriangle className="w-4 h-4 inline mr-1 text-orange-500" />
                      We do not support bulk updating scoring weights to avoid unintended overwrites. Use individual profile editing for weights.
                    </p>
                  </div>
                </div>
                {/* <WeightSliders weights={config.scoring.weights} onChange={updateWeights} /> */}
              </div>
            </div>
          )}
        </div>

        {/* Preview Panel Slide-in */}
        {previewLog && (
          <div className="w-[400px] border-l border-[var(--border-default)] bg-[var(--bg-secondary)] shadow-xl flex flex-col z-20">
            <div className="p-4 border-b border-[var(--border-default)] flex justify-between items-center bg-[var(--bg-tertiary)]">
              <h3 className="font-bold text-[var(--text-primary)] flex items-center gap-2">
                <Check className="w-4 h-4 text-[var(--accent-primary)]" />
                Validation Log
              </h3>
              <button 
                onClick={() => setPreviewLog(null)}
                className="text-[12px] text-[var(--text-secondary)] hover:text-white"
              >
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
                      <li key={j} className={`text-[12px] ${msg.includes('ignored') ? 'text-[var(--text-tertiary)]' : msg.includes('overwritten') ? 'text-orange-400' : 'text-[var(--color-profit)]'}`}>
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
                    Applying...
                  </span>
                ) : (
                  <span className="flex items-center justify-center">
                    <Save className="w-4 h-4 mr-2" />
                    Confirm & Apply
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
