"use client";

import { useState, useEffect } from "react";
import { ArrowLeft, Plus, Trash2, Save, Play, HelpCircle, Link } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { ConditionBuilder } from "./ConditionBuilder";
import { WeightSliders } from "./WeightSliders";
import ProfileRoleSelector, { type ProfileRole } from './ProfileRoleSelector'

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

interface CustomWatchlist {
  id: string;
  name: string;
  symbol_count: number;
  symbols: string[];
}

const DEFAULT_CONFIG = {
  filters: {
    logic: "AND",
    conditions: [],
  },
  scoring: {
    enabled: true,
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
  const [customWatchlists, setCustomWatchlists] = useState<CustomWatchlist[]>([]);
  const [selectedWatchlistId, setSelectedWatchlistId] = useState<string>("");
  const [assignedWatchlist, setAssignedWatchlist] = useState<string | null>(null);
  const [scoringEnabled, setScoringEnabled] = useState(profile?.config?.scoring?.enabled !== false);
  const [profileRole, setProfileRole] = useState<ProfileRole | null>(profile?.profile_role || null)

  useEffect(() => {
    // Load custom watchlists from backend
    loadCustomWatchlists();

    // If editing, check if profile has an assigned watchlist
    if (profile?.id) {
      apiGet(`/profiles/watchlist/default/profile`).then((data) => {
        if (data.profile?.id === profile.id) {
          setAssignedWatchlist("default");
        }
      }).catch(() => {});
    }
  }, [profile]);

  const loadCustomWatchlists = async () => {
    try {
      const data = await apiGet("/custom-watchlists");
      setCustomWatchlists(data.watchlists || []);
    } catch (e) {
      console.error("Failed to load watchlists:", e);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) {
      alert("Profile name is required");
      return;
    }
    
    const profileData = {
      name,
      description,
      config,
      is_active: true,
      watchlist_id: selectedWatchlistId || null,
      profile_role: profileRole,
    };
    
    onSave(profileData);
  };

  const handleAssignWatchlist = async () => {
    if (!profile?.id) {
      alert("Save the profile first before assigning a watchlist");
      return;
    }
    
    try {
      const watchlistId = selectedWatchlistId || "default";
      await apiPost(`/profiles/watchlist/${watchlistId}/assign`, {
        profile_id: profile.id
      });
      setAssignedWatchlist(watchlistId);
      alert("Watchlist assigned successfully!");
    } catch (e: any) {
      alert(`Failed to assign: ${e.message}`);
    }
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

  const toggleScoringEnabled = (enabled: boolean) => {
    setScoringEnabled(enabled);
    setConfig({
      ...config,
      scoring: { ...config.scoring, enabled },
    });
  };

  const selectedWatchlist = customWatchlists.find(w => w.id === selectedWatchlistId);

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
              <label className="label">Watchlist</label>
              <div className="flex gap-2">
                <select
                  className="input flex-1"
                  value={selectedWatchlistId}
                  onChange={(e) => setSelectedWatchlistId(e.target.value)}
                  data-testid="watchlist-select"
                >
                  <option value="">-- Select a Watchlist --</option>
                  {customWatchlists.map((wl) => (
                    <option key={wl.id} value={wl.id}>
                      {wl.name} ({wl.symbol_count} assets)
                    </option>
                  ))}
                </select>
                {profile?.id && selectedWatchlistId && (
                  <button
                    className="btn btn-secondary px-3"
                    onClick={handleAssignWatchlist}
                    title="Assign this profile to the selected watchlist"
                  >
                    <Link className="w-4 h-4" />
                  </button>
                )}
              </div>
              {customWatchlists.length === 0 && (
                <p className="text-[11px] text-[var(--text-tertiary)]">
                  No watchlists found. Create one in Market Watchlist page first.
                </p>
              )}
              {assignedWatchlist && (
                <p className="text-[11px] text-[var(--color-profit)]">
                  ✓ Currently assigned to: {assignedWatchlist === "default" ? "Default" : customWatchlists.find(w => w.id === assignedWatchlist)?.name || assignedWatchlist}
                </p>
              )}
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

          {/* Profile Role Selector */}
          <div className="mt-4">
            <ProfileRoleSelector
              value={profileRole}
              onChange={setProfileRole}
            />
          </div>
          
          {/* Watchlist Preview */}
          {selectedWatchlist && selectedWatchlist.symbols.length > 0 && (
            <div className="mt-4 p-3 bg-[var(--bg-secondary)] rounded-lg">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[12px] text-[var(--text-tertiary)]">
                  {selectedWatchlist.name} - {selectedWatchlist.symbols.length} assets
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-[100px] overflow-y-auto">
                {selectedWatchlist.symbols.slice(0, 30).map((symbol) => (
                  <span
                    key={symbol}
                    className="px-2 py-0.5 rounded bg-[var(--bg-card)] text-[var(--text-secondary)] text-[11px]"
                  >
                    {symbol}
                  </span>
                ))}
                {selectedWatchlist.symbols.length > 30 && (
                  <span className="px-2 py-0.5 text-[var(--text-tertiary)] text-[11px]">
                    +{selectedWatchlist.symbols.length - 30} more
                  </span>
                )}
              </div>
            </div>
          )}
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
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-[var(--text-primary)]">
                    Alpha Score Weights
                  </h3>
                  <p className="text-[12px] text-[var(--text-secondary)]">
                    Customize how the Alpha Score is calculated
                  </p>
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
                    <div 
                      className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                        scoringEnabled ? "translate-x-5" : "translate-x-0.5"
                      }`}
                    />
                  </div>
                </label>
              </div>
              {scoringEnabled ? (
                <WeightSliders
                  weights={config.scoring.weights}
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
