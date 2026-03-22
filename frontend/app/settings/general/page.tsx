"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Settings } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";
import AIProviderSection from "@/components/settings/AIProviderSection";

export default function GeneralSettings() {
  const { config, updateConfig, isLoading } = useConfig("universe");
  const [local, setLocal] = useState({
    min_volume_24h: 5000000,
    min_market_cap: 50000000,
    accepted_pairs: ["USDT"],
    accepted_exchanges: ["gate"],
    max_assets: 100,
    refresh_interval_hours: 24,
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) setLocal({ ...local, ...config });
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig(local); } catch (e) { console.error(e); }
    setSaving(false);
  };

  if (isLoading) return <div className="p-8"><div className="skeleton h-96 w-full" /></div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">General Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Configure the asset universe and global platform settings.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      <div className="card">
        <div className="card-header"><h3>Universe of Assets</h3></div>
        <div className="card-body">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="label">Min 24h Volume (USD)</label>
              <div className="input-group">
                <input type="number" className="input numeric" value={local.min_volume_24h} onChange={(e) => setLocal({ ...local, min_volume_24h: parseInt(e.target.value) || 0 })} />
                <span className="suffix">USD</span>
              </div>
              <p className="caption">Only track assets with at least this much daily volume.</p>
            </div>

            <div className="space-y-2">
              <label className="label">Min Market Cap (USD)</label>
              <div className="input-group">
                <input type="number" className="input numeric" value={local.min_market_cap} onChange={(e) => setLocal({ ...local, min_market_cap: parseInt(e.target.value) || 0 })} />
                <span className="suffix">USD</span>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label">Max Assets to Track</label>
              <div className="slider-container">
                <input type="range" min={10} max={500} value={local.max_assets} onChange={(e) => setLocal({ ...local, max_assets: parseInt(e.target.value) })} className="slider" />
                <span className="slider-value">{local.max_assets}</span>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label">Refresh Interval</label>
              <div className="input-group">
                <input type="number" className="input numeric" value={local.refresh_interval_hours} onChange={(e) => setLocal({ ...local, refresh_interval_hours: parseInt(e.target.value) || 24 })} />
                <span className="suffix">hours</span>
              </div>
            </div>

            <div className="space-y-2">
              <label className="label">Quote Pairs</label>
              <input type="text" className="input text-[13px]" value={local.accepted_pairs.join(", ")} onChange={(e) => setLocal({ ...local, accepted_pairs: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </div>

            <div className="space-y-2">
              <label className="label">Exchanges</label>
              <input type="text" className="input text-[13px]" value={local.accepted_exchanges.join(", ")} onChange={(e) => setLocal({ ...local, accepted_exchanges: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </div>
          </div>
        </div>
      </div>

      {/* AI Provider Keys */}
      <div className="card">
        <div className="card-body">
          <AIProviderSection />
        </div>
      </div>
    </div>
  );
}
