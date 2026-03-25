"use client";

import { useState, useEffect } from "react";
import { ArrowLeft, Save, Play, Link, RefreshCw } from "lucide-react";
import { apiGet, apiPost, apiPut } from "@/lib/api";
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

interface CustomWatchlist {
  id: string;
  name: string;
  symbol_count: number;
  symbols: string[];
}

const DEFAULT_CONFIG = {
  filters:  { logic: "AND", conditions: [] },
  scoring:  { enabled: true, weights: { liquidity: 25, market_structure: 25, momentum: 25, signal: 25 } },
  signals:  { logic: "AND", conditions: [] },
};

// Mapa de role → profile_type usado na API de WatchlistProfile
const ROLE_TO_TYPE: Record<string, "L1" | "L2" | "L3"> = {
  primary_filter:    "L1",
  score_engine:      "L2",
  acquisition_queue: "L3",
  universe_filter:   "L1", // Pool usa L1 como tipo base
};

export function ProfileBuilder({ profile, onSave, onCancel }: ProfileBuilderProps) {
  const [name, setName]                     = useState(profile?.name || "");
  const [description, setDescription]       = useState(profile?.description || "");
  const [config, setConfig]                 = useState(profile?.config || DEFAULT_CONFIG);
  const [profileRole, setProfileRole]       = useState<ProfileRole | null>(profile?.profile_role || null);
  const [activeTab, setActiveTab]           = useState<"filters" | "scoring" | "signals">("filters");
  const [testResult, setTestResult]         = useState<any>(null);
  const [testing, setTesting]               = useState(false);
  const [saving, setSaving]                 = useState(false);
  const [customWatchlists, setCustomWatchlists] = useState<CustomWatchlist[]>([]);
  const [selectedWatchlistId, setSelectedWatchlistId] = useState<string>("");
  const [assignedWatchlistId, setAssignedWatchlistId] = useState<string>("");
  const [assigning, setAssigning]           = useState(false);
  const [scoringEnabled, setScoringEnabled] = useState(
    profile?.config?.scoring?.enabled !== false
  );

  // ── Carregar watchlists e a association atual do profile ─────────────────
  useEffect(() => {
    loadCustomWatchlists();
  }, []);

  // Quando as watchlists carregam, buscar qual está associada a este profile
  useEffect(() => {
    if (!profile?.id || customWatchlists.length === 0) return;
    loadCurrentWatchlistAssociation();
  }, [profile?.id, customWatchlists]);

  const loadCustomWatchlists = async () => {
    try {
      const data = await apiGet("/custom-watchlists");
      setCustomWatchlists(data.watchlists || []);
    } catch (e) {
      console.error("Failed to load watchlists:", e);
    }
  };

  /**
   * Percorre todas as watchlists e verifica qual tem este profile associado
   * como L1/L2/L3 via GET /custom-watchlists/{id}/profiles
   */
  const loadCurrentWatchlistAssociation = async () => {
    if (!profile?.id) return;
    for (const wl of customWatchlists) {
      try {
        const data = await apiGet(`/custom-watchlists/${wl.id}/profiles`);
        const assigned = [data.L1, data.L2, data.L3].find(
          (p: any) => p?.id === profile.id
        );
        if (assigned) {
          setAssignedWatchlistId(wl.id);
          setSelectedWatchlistId(wl.id);
          return;
        }
      } catch { /* skip */ }
    }
  };

  // ── Salvar ──────────────────────────────────────────────────────────────
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

  // ── Associar watchlist ao profile ─────────────────────────────────────
  const handleAssignWatchlist = async () => {
    if (!profile?.id) { alert("Salve o profile primeiro antes de associar uma watchlist."); return; }
    if (!selectedWatchlistId) { alert("Selecione uma watchlist."); return; }

    const profileType = profileRole ? (ROLE_TO_TYPE[profileRole] ?? "L1") : "L1";

    setAssigning(true);
    try {
      // Se havia outra watchlist associada, desassociar
      if (assignedWatchlistId && assignedWatchlistId !== selectedWatchlistId) {
        await apiPut(`/custom-watchlists/${assignedWatchlistId}/profile/${profileType}`, {
          profile_id: null,
        });
      }

      // Associar à nova watchlist
      await apiPut(`/custom-watchlists/${selectedWatchlistId}/profile/${profileType}`, {
        profile_id: profile.id,
      });

      setAssignedWatchlistId(selectedWatchlistId);
    } catch (e: any) {
      alert(`Falha ao associar watchlist: ${e.message}`);
    }
    setAssigning(false);
  };

  // ── Testar configuração ──────────────────────────────────────────────
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

  // ── Update helpers ────────────────────────────────────────────────────
  const updateFilters  = (conditions: Condition[], logic: string) =>
    setConfig({ ...config, filters: { logic, conditions } });

  const updateSignals  = (conditions: Condition[], logic: string) =>
    setConfig({ ...config, signals: { logic, conditions } });

  const updateWeights  = (weights: any) =>
    setConfig({ ...config, scoring: { ...config.scoring, weights } });

  const toggleScoringEnabled = (enabled: boolean) => {
    setScoringEnabled(enabled);
    setConfig({ ...config, scoring: { ...config.scoring, enabled } });
  };

  const handlePresetIASuccess = (result: any) => {
    if (result?.config) {
      setConfig(result.config);
      if (result.config.scoring?.enabled !== undefined) {
        setScoringEnabled(result.config.scoring.enabled !== false);
      }
    }
  };

  const selectedWatchlist  = customWatchlists.find(w => w.id === selectedWatchlistId);
  const assignedWatchlist  = customWatchlists.find(w => w.id === assignedWatchlistId);
  const isWatchlistChanged = selectedWatchlistId && selectedWatchlistId !== assignedWatchlistId;

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
          disabled={saving}
          data-testid="save-profile-btn"
        >
          <Save className="w-4 h-4 mr-2" />
          {saving ? "Saving..." : "Save Profile"}
        </button>
      </div>

      {/* Basic Info + Watchlist */}
      <div className="card">
        <div className="card-body p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Nome */}
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

            {/* Watchlist */}
            <div className="space-y-2">
              <label className="label">Watchlist</label>
              <div className="flex gap-2">
                <select
                  className="input flex-1"
                  value={selectedWatchlistId}
                  onChange={(e) => setSelectedWatchlistId(e.target.value)}
                  data-testid="watchlist-select"
                >
                  <option value="">-- Selecione uma Watchlist --</option>
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
                    disabled={assigning || !isWatchlistChanged}
                    title="Associar este profile à watchlist selecionada"
                  >
                    {assigning
                      ? <RefreshCw className="w-4 h-4 animate-spin" />
                      : <Link className="w-4 h-4" />}
                  </button>
                )}
              </div>
              {assignedWatchlist && (
                <p className="text-[11px] text-[var(--color-profit)]">
                  ✓ Associado a: <strong>{assignedWatchlist.name}</strong>
                  {profileRole && ` (${ROLE_TO_TYPE[profileRole] ?? "L1"})`}
                </p>
              )}
              {!assignedWatchlistId && profile?.id && (
                <p className="text-[11px] text-[var(--text-tertiary)]">
                  Selecione uma watchlist e clique em 🔗 para associar.
                </p>
              )}
            </div>
          </div>

          {/* Descrição */}
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

          {/* Papel do profile no funil */}
          <div className="pt-2">
            <ProfileRoleSelector
              value={profileRole}
              onChange={(role) => setProfileRole(role)}
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
            {tab === "filters" && ` (${config.filters?.conditions?.length ?? 0})`}
            {tab === "signals" && ` (${config.signals?.conditions?.length ?? 0})`}
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
                  <h3 className="font-semibold text-[var(--text-primary)]">L1 Filter Conditions</h3>
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
              />
            </div>
          )}

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
              />
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
