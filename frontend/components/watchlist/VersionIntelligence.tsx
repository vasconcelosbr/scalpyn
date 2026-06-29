"use client";

import { useMemo, useState, useEffect } from "react";
import { VersionComparisonChart, type ChartDataPoint } from "./VersionComparisonChart";
import { VersionDuelStats, type VersionStats } from "./VersionDuelStats";
import type { WatchlistPerformanceRow } from "@/lib/watchlistPerformance";

const C = {
  surface: "#10121A", elevated: "#161824", border: "rgba(255,255,255,0.08)", text: "#E6E8EE",
  muted: "#8A91A4", dim: "#5A6075", green: "#22B97A", blue: "#4F7BF7", amber: "#F2A33A",
  red: "#E5484D", purple: "#9D7CF7", neonGreen: "#00FF66", glow: "rgba(0, 255, 102, 0.15)"
} as const;

// Mock Data
const MOCK_CHART_DATA: ChartDataPoint[] = Array.from({ length: 40 }).map((_, i) => {
  const isAfterMutation = i >= 20;
  const isMutationPoint = i === 20;
  
  let evScore = isAfterMutation ? 45 + Math.random() * 15 : 30 + Math.random() * 10;
  let winRate = isAfterMutation ? 0.65 + Math.random() * 0.1 : 0.45 + Math.random() * 0.1;
  
  return {
    index: i,
    evScore,
    winRate,
    version: isAfterMutation ? "v2" : "v1",
    isMutationPoint,
    mutationDetails: isMutationPoint ? {
      added: ["RSI < 24"],
      removed: ["MACD Hist > 0"]
    } : undefined
  };
});

const MOCK_V1: VersionStats = {
  versionName: "1",
  shortName: "v1",
  isChampion: false,
  evScore: 35.2,
  winRate: 0.51,
  pnlPct: 0.15,
  holdingSeconds: 7200, // 2h
  trades: 450,
  confidenceScore: "High"
};

const MOCK_V2: VersionStats = {
  versionName: "2",
  shortName: "v2",
  isChampion: true,
  evScore: 52.8,
  winRate: 0.68,
  pnlPct: 0.42,
  holdingSeconds: 3600, // 1h
  trades: 120,
  confidenceScore: "Medium"
};

export function VersionIntelligence({ availableProfiles = [], rows = [] }: { availableProfiles?: string[], rows?: WatchlistPerformanceRow[] }) {
  const [selectedProfile, setSelectedProfile] = useState(availableProfiles[0] || "");
  
  // Update selected if availableProfiles loads late
  useEffect(() => {
    if (!selectedProfile && availableProfiles.length > 0) {
      setSelectedProfile(availableProfiles[0]);
    }
  }, [availableProfiles, selectedProfile]);
  
  const { activeV1, activeV2 } = useMemo(() => {
    if (!selectedProfile || rows.length === 0) {
      return { 
        activeV1: { ...MOCK_V1, versionName: "Base (v1)" }, 
        activeV2: { ...MOCK_V2, versionName: "Auto-Pilot (v2)" } 
      };
    }
    
    // Find v1 (Base)
    const baseRow = rows.find(r => r.profile_name === selectedProfile);
                    
    // Find v2 (Auto-Pilot)
    const apRows = rows.filter(r => r.profile_name.startsWith(`${selectedProfile} - Auto-Pilot`));
    const apRow = apRows.length > 0 
      ? apRows.reduce((prev, current) => (prev.ev_score > current.ev_score) ? prev : current)
      : null;
      
    const v1: VersionStats = baseRow ? {
      versionName: "Base (v1)",
      shortName: "v1",
      isChampion: false,
      evScore: baseRow.ev_score,
      winRate: baseRow.win_rate || 0,
      pnlPct: baseRow.avg_pnl_pct || 0,
      holdingSeconds: baseRow.avg_holding_win_seconds || 0,
      trades: baseRow.completed_trades,
      confidenceScore: baseRow.stat_confidence as any
    } : { ...MOCK_V1, versionName: "Not Found", shortName: "--" };

    const v2: VersionStats = apRow ? {
      versionName: apRow.profile_name.split(" - ")[1] || "Auto-Pilot",
      shortName: apRow.profile_name.split(" - ")[1] ? apRow.profile_name.split(" - ")[1].substring(0, 2) : "v2",
      isChampion: false,
      evScore: apRow.ev_score,
      winRate: apRow.win_rate || 0,
      pnlPct: apRow.avg_pnl_pct || 0,
      holdingSeconds: apRow.avg_holding_win_seconds || 0,
      trades: apRow.completed_trades,
      confidenceScore: apRow.stat_confidence as any
    } : { ...MOCK_V2, versionName: "Not Found", shortName: "--" };

    if (v1.evScore > v2.evScore) {
      v1.isChampion = true;
    } else if (v2.evScore > v1.evScore) {
      v2.isChampion = true;
    }
    
    return { activeV1: v1, activeV2: v2 };
  }, [selectedProfile, rows]);
  
  return (
    <section className="rounded-2xl p-5 mb-4" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="flex flex-wrap items-center justify-between mb-6 gap-4">
        <div>
          <h2 className="text-[13px] font-semibold flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: C.neonGreen, boxShadow: `0 0 8px ${C.neonGreen}` }} />
            Version Intelligence (A/B Test)
          </h2>
          <p className="mt-0.5 text-[11px]" style={{ color: C.dim }}>Compare mutations injected by the Auto-Pilot.</p>
        </div>
        
        {availableProfiles.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium" style={{ color: C.muted }}>Analyzing:</span>
            <select 
              value={selectedProfile}
              onChange={(e) => setSelectedProfile(e.target.value)}
              className="rounded-lg px-3 py-1.5 text-[11px] outline-none font-bold" 
              style={{ color: C.text, background: C.elevated, border: `1px solid ${C.border}` }}
            >
              {availableProfiles.map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
        )}
      </div>
      
      <div className="grid grid-cols-1 xl:grid-cols-[1fr,400px] gap-6">
        <div className="flex flex-col gap-2">
          <VersionComparisonChart data={MOCK_CHART_DATA} />
          <div className="flex gap-4 mt-2 px-2">
            <span className="text-[10px] uppercase font-bold" style={{ color: C.blue }}>v1 Baseline</span>
            <span className="text-[10px] uppercase font-bold" style={{ color: C.neonGreen }}>v2 Mutated</span>
          </div>
        </div>
        
        <div>
          <VersionDuelStats v1={activeV1} v2={activeV2} />
        </div>
      </div>
    </section>
  );
}
