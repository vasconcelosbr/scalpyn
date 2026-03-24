"use client";

import { Settings2, Trash2, Play, Copy } from "lucide-react";

interface ProfileCardProps {
  profile: {
    id: string;
    name: string;
    description: string;
    is_active: boolean;
    config: any;
    created_at: string;
    updated_at: string;
  };
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
  onDuplicate: () => void;
}

export function ProfileCard({
  profile,
  onEdit,
  onDelete,
  onTest,
  onDuplicate,
}: ProfileCardProps) {
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
            <div className="flex items-center gap-2 mt-2">
              <span
                className={`badge ${profile.is_active ? "bullish" : "range"}`}
              >
                {profile.is_active ? "ACTIVE" : "INACTIVE"}
              </span>
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
