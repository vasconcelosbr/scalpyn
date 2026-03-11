"use client";

import { Settings } from "lucide-react";

export default function GeneralSettings() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">General Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Manage base platform preferences and global toggles.</p>
        </div>
        <button className="btn btn-primary">
          <Settings className="w-4 h-4 mr-2" />
          Save Settings
        </button>
      </div>

      <div className="card">
        <div className="card-body flex flex-col items-center justify-center py-20 text-center">
          <Settings className="w-12 h-12 text-[var(--text-tertiary)] mb-4 opacity-50" />
          <h3 className="text-lg font-bold text-[var(--text-primary)] mb-2">Module Under Construction</h3>
          <p className="text-[var(--text-secondary)] max-w-md">
            The General Settings module is currently being wired to the ConfigService API.
            Check back soon.
          </p>
        </div>
      </div>
    </div>
  );
}
