"use client";

import { Zap } from "lucide-react";

export default function SignalSettings() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Signal Rules</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Define conditions required to generate buy/sell signals.</p>
        </div>
      </div>

      <div className="card">
        <div className="card-body flex flex-col items-center justify-center py-20 text-center">
          <Zap className="w-12 h-12 text-[var(--text-tertiary)] mb-4 opacity-50" />
          <h3 className="text-lg font-bold text-[var(--text-primary)] mb-2">Module Under Construction</h3>
          <p className="text-[var(--text-secondary)] max-w-md">
            The Signal Rules module is currently being wired to the ConfigService API.
          </p>
        </div>
      </div>
    </div>
  );
}
