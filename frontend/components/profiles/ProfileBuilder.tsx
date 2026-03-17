"use client";

import { useState, useEffect } from "react";
import { ArrowLeft, Plus, Trash2, Save, Play, HelpCircle } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { ConditionBuilder } from "./ConditionBuilder";
import { WeightSliders } from "./WeightSliders";

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

const DEFAULT_CONFIG = {
  filters: {
    logic: "AND",
    conditions: [],
  },
  scoring: {
    weights: {
      liquidity: 25,
      market_structure: 25,
      momentum: 25,
      signal: 25,
    },
  },
  signals: {
    logic: "AND",
    conditions: [],
  },
};

export function ProfileBuilder({ profile, onSave, onCancel }: ProfileBuilderProps) {
  const [name, setName] = useState(profile?.name || "");
  const [description, setDescription] = useState(profile?.description || "");
  const [config, setConfig] = useState(profile?.config || DEFAULT_CONFIG);
  const [activeTab, setActiveTab] = useState<"filters" | "scoring" | "signals">("filters");
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [examples, setExamples] = useState<any[]>([]);

  useEffect(() => {
    // Load example profiles
    apiGet("/profiles/examples").then((data) => {
      setExamples(data.examples || []);
    }).catch(() => {});
  }, []);

  const handleSave = () => {
    if (!name.trim()) {
      alert("Profile name is required");
      return;
    }
    onSave({
      name,
      description,
      config,
      is_active: true,
    });
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

  const updateFilters = (conditions: Condition[], logic: string) => {
    setConfig({
      ...config,
      filters: { logic, conditions },
    });
  };

  const updateSignals = (conditions: Condition[], logic: string) => {
    setConfig({
      ...config,
      signals: { logic, conditions },
    });
  };

  const updateWeights = (weights: any) => {
    setConfig({
      ...config,
      scoring: { ...config.scoring, weights },
    });
  };

  const loadExample = (example: any) => {
    setName(example.name);
    setDescription(example.description);
    setConfig(example.config);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          className="btn btn-secondary p-2"
          onClick={onCancel}
          data-testid="back-btn"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            {profile ? "Edit Profile" : "Create Profile"}
          </h1>
          <p className="text-[var(--text-secondary)] text-[13px]">
            Define your strategy configuration
          </p>
        </div>
        <button
          className="btn btn-secondary"
          onClick={handleTest}
          disabled={testing}
          data-testid="test-config-btn"
        >
          <Play className="w-4 h-4 mr-2" />
          {testing ? "Testing..." : "Test Config"}
        </button>
        <button
          className="btn btn-primary"
          onClick={handleSave}
          data-testid="save-profile-btn"
        >
          <Save className="w-4 h-4 mr-2" />
          Save Profile
        </button>
      </div>

      {/* Basic Info */}
      <div className="card">
        <div className="card-body p-6">
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
              <label className="label">Load Example</label>
              <select
                className="input"
                onChange={(e) => {
                  const idx = parseInt(e.target.value);
                  if (idx >= 0 && examples[idx]) {
                    loadExample(examples[idx]);
                  }
                }}
                data-testid="example-select"
              >
                <option value="-1">Select an example...</option>
                {examples.map((ex, i) => (
                  <option key={i} value={i}>
                    {ex.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-2 mt-4">
            <label className="label">Description</label>
            <textarea
              className="input min-h-[80px]"
              placeholder="Describe your strategy..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              data-testid="profile-description-input"
            />
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[var(--border-default)]">
        {(["filters", "scoring", "signals"] as const).map((tab) => (
          <button
            key={tab}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab
                ? "text-[var(--accent-primary)] border-b-2 border-[var(--accent-primary)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
            onClick={() => setActiveTab(tab)}
            data-testid={`tab-${tab}`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
            {tab === "filters" && ` (${config.filters.conditions.length})`}
            {tab === "signals" && ` (${config.signals.conditions.length})`}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="card">
        <div className="card-body p-6">
          {activeTab === "filters" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">
                    L1 Filter Conditions
                  </h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">
                    Assets must pass these conditions to be included
                  </p>
                </div>
                <select
                  className="input w-24"
                  value={config.filters.logic}
                  onChange={(e) =>
                    updateFilters(config.filters.conditions, e.target.value)
                  }
                  data-testid="filter-logic-select"
                >
                  <option value="AND">AND</option>
                  <option value="OR">OR</option>
                </select>
              </div>
              <ConditionBuilder
                conditions={config.filters.conditions}
                onChange={(conditions) =>
                  updateFilters(conditions, config.filters.logic)
                }
                showRequired={false}
              />
            </div>
          )}

          {activeTab === "scoring" && (
            <div className="space-y-4">
              <div>
                <h3 className="font-semibold text-[var(--text-primary)]">
                  Alpha Score Weights
                </h3>
                <p className="text-[12px] text-[var(--text-secondary)]">
                  Customize how the Alpha Score is calculated
                </p>
              </div>
              <WeightSliders
                weights={config.scoring.weights}
                onChange={updateWeights}
              />
            </div>
          )}

          {activeTab === "signals" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">
                    Signal Entry Conditions
                  </h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">
                    Define when a trading signal should be triggered
                  </p>
                </div>
                <select
                  className="input w-24"
                  value={config.signals.logic}
                  onChange={(e) =>
                    updateSignals(config.signals.conditions, e.target.value)
                  }
                  data-testid="signal-logic-select"
                >
                  <option value="AND">AND</option>
                  <option value="OR">OR</option>
                </select>
              </div>
              <ConditionBuilder
                conditions={config.signals.conditions}
                onChange={(conditions) =>
                  updateSignals(conditions, config.signals.logic)
                }
                showRequired={true}
              />
            </div>
          )}
        </div>
      </div>

      {/* Test Results */}
      {testResult && (
        <div className="card border-l-4 border-l-[var(--accent-primary)]">
          <div className="card-body p-6">
            <h3 className="font-semibold text-[var(--text-primary)] mb-4">
              Test Results
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-[var(--text-tertiary)] text-xs">
                  Total Assets
                </div>
                <div className="text-xl font-bold text-[var(--text-primary)]">
                  {testResult.summary?.total_assets || 0}
                </div>
              </div>
              <div>
                <div className="text-[var(--text-tertiary)] text-xs">
                  After Filter
                </div>
                <div className="text-xl font-bold text-[var(--color-profit)]">
                  {testResult.summary?.after_filter || 0}
                </div>
              </div>
              <div>
                <div className="text-[var(--text-tertiary)] text-xs">
                  Filter Rate
                </div>
                <div className="text-xl font-bold text-[var(--text-primary)]">
                  {testResult.summary?.filter_rate || "0%"}
                </div>
              </div>
              <div>
                <div className="text-[var(--text-tertiary)] text-xs">
                  Signals
                </div>
                <div className="text-xl font-bold text-[var(--accent-primary)]">
                  {testResult.summary?.signals_triggered || 0}
                </div>
              </div>
            </div>

            {testResult.sample_assets?.length > 0 && (
              <div className="mt-4">
                <div className="text-[var(--text-tertiary)] text-xs mb-2">
                  Top Matched Assets
                </div>
                <div className="flex flex-wrap gap-2">
                  {testResult.sample_assets.slice(0, 5).map((asset: any) => (
                    <span
                      key={asset.symbol}
                      className="px-2 py-1 rounded bg-[var(--bg-secondary)] text-[var(--text-primary)] text-xs"
                    >
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
