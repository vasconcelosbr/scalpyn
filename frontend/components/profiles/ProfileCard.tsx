"use client";

import { useState } from "react";
import { Settings2, Trash2, Play, Copy } from "lucide-react";
import PresetIAButton from "./PresetIAButton";
import AutoPilotToggle from "./AutoPilotToggle";
import { RoleBadge, type ProfileRole } from "./ProfileRoleSelector";

interface ProfileCardProps {
  profile: {
    id: string;
    name: string;
    description: string;
    is_active: boolean;
    config: any;
    created_at: string;
    updated_at: string;
    // Preset IA + Auto-Pilot fields (optional for backwards compat)
    profile_role?:       ProfileRole | null;
    pipeline_label?:     string | null;
    auto_pilot_enabled?: boolean;
    preset_ia_last_run?: string | null;
  };
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
  onDuplicate: () => void;
  onRefresh?: () => void;
}

export function ProfileCard({
  profile,
  onEdit,
  onDelete,
  onTest,
  onDuplicate,
  onRefresh,
}: ProfileCardProps) {
  const [showPreset, setShowPreset] = useState(false);
  const filterCount = profile.config?.filters?.conditions?.length || 0;
  const signalCount = profile.config?.signals?.conditions?.length || 0;
  const weights = profile.config?.scoring?.weights || {};

  return (
    <div className="card flex flex-col" data-testid={`profile-card-${profile.id}`}>
      <div className="card-body flex-1 p-6">
        <div className="flex justify-between items-start mb-4">
          <div>
            <h3 className="text-lg font-bold text-[var(--text-primary)] tracking-tight">
              {profile.name}
            </h3>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <span
                className={`badge ${profile.is_active ? "bullish" : "range"}`}
              >
                {profile.is_active ? "ACTIVE" : "INACTIVE"}
              </span>
              {profile.profile_role && <RoleBadge role={profile.profile_role} />}
              {profile.pipeline_label && (
                <span style={{ fontSize: 10, color: "var(--text-tertiary)", fontStyle: "italic" }}>
                  {profile.pipeline_label}
                </span>
              )}
            </div>
          </div>
        </div>

        {profile.description && (
          <p className="text-[13px] text-[var(--text-secondary)] mb-4 line-clamp-2">
            {profile.description}
          </p>
        )}

        {/* Config Summary */}
        <div className="space-y-3">
          <div className="flex items-center justify-between text-[12px]">
            <span className="text-[var(--text-tertiary)]">Filters</span>
            <span className="text-[var(--text-primary)] font-medium">
              {filterCount} conditions
            </span>
          </div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="text-[var(--text-tertiary)]">Signals</span>
            <span className="text-[var(--text-primary)] font-medium">
              {signalCount} conditions
            </span>
          </div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="text-[var(--text-tertiary)]">Weights</span>
            <div className="flex gap-1">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
                L:{weights.liquidity || 25}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400">
                M:{weights.market_structure || 25}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">
                Mo:{weights.momentum || 25}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400">
                S:{weights.signal || 25}
              </span>
            </div>
          </div>
        </div>

        <div className="text-[11px] text-[var(--text-tertiary)] mt-4">
          Updated{" "}
          {profile.updated_at
            ? new Date(profile.updated_at).toLocaleDateString()
            : "—"}
        </div>

        {/* Preset IA */}
        <div style={{ marginTop: 12 }}>
          {!showPreset ? (
            <button
              onClick={() => profile.profile_role && setShowPreset(true)}
              style={{
                width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                padding: "7px", fontSize: 11, fontWeight: 700, borderRadius: 7,
                background: profile.profile_role ? "linear-gradient(135deg,rgba(139,92,246,0.12),rgba(79,123,247,0.08))" : "rgba(255,255,255,0.03)",
                border: `1px solid ${profile.profile_role ? "rgba(139,92,246,0.25)" : "var(--border-subtle)"}`,
                cursor: profile.profile_role ? "pointer" : "not-allowed",
                color: profile.profile_role ? "#A78BFA" : "var(--text-tertiary)",
              }}
              title={!profile.profile_role ? "Configure o papel do profile em Configure" : ""}
            >
              ✨ Preset IA
              {!profile.profile_role && <span style={{ fontSize: 10, opacity: 0.7 }}>(configure o role primeiro)</span>}
            </button>
          ) : (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)" }}>Preset IA</span>
                <button onClick={() => setShowPreset(false)} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, color: "var(--text-tertiary)" }}>fechar</button>
              </div>
              <PresetIAButton
                profileId={profile.id}
                profileRole={profile.profile_role}
                size="sm"
                onSuccess={() => { onRefresh?.(); setTimeout(() => setShowPreset(false), 3000); }}
              />
            </div>
          )}
        </div>

        {/* Auto-Pilot */}
        <div style={{ marginTop: 8 }}>
          <AutoPilotToggle
            profileId={profile.id}
            enabled={profile.auto_pilot_enabled ?? false}
            lastRun={profile.preset_ia_last_run}
            onToggle={() => onRefresh?.()}
          />
        </div>
      </div>

      {/* Actions */}
      <div className="border-t border-[var(--border-default)] p-3 flex justify-between">
        <div className="flex gap-1">
          <button
            className="btn btn-secondary text-[12px] px-2 py-1.5 text-red-500 hover:bg-red-500/10"
            onClick={onDelete}
            title="Delete"
            data-testid={`delete-profile-${profile.id}`}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
          <button
            className="btn btn-secondary text-[12px] px-2 py-1.5"
            onClick={onDuplicate}
            title="Duplicate"
          >
            <Copy className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="flex gap-1">
          <button
            className="btn btn-secondary text-[12px] px-2 py-1.5 text-[var(--accent-primary)]"
            onClick={onTest}
            title="Test Profile"
            data-testid={`test-profile-${profile.id}`}
          >
            <Play className="w-3.5 h-3.5" />
          </button>
          <button
            className="btn btn-primary text-[12px] px-3 py-1.5"
            onClick={onEdit}
            data-testid={`edit-profile-${profile.id}`}
          >
            <Settings2 className="w-3.5 h-3.5 mr-1" />
            Configure
          </button>
        </div>
      </div>
    </div>
  );
}
