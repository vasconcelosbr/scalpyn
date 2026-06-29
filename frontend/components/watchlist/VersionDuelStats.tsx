"use client";

import { Trophy, TrendingUp, TrendingDown, Clock, Activity, Settings2 } from "lucide-react";

const C = {
  surface: "#10121A", elevated: "#161824", border: "rgba(255,255,255,0.08)", text: "#E6E8EE",
  muted: "#8A91A4", dim: "#5A6075", green: "#22B97A", blue: "#4F7BF7", amber: "#F2A33A",
  red: "#E5484D", purple: "#9D7CF7", neonGreen: "#00FF66", glow: "rgba(0, 255, 102, 0.15)"
} as const;

export interface VersionStats {
  versionName: string;
  isChampion: boolean;
  evScore: number;
  winRate: number;
  pnlPct: number;
  holdingSeconds: number;
  trades: number;
  confidenceScore: "High" | "Medium" | "Low";
}

interface VersionDuelStatsProps {
  v1: VersionStats;
  v2: VersionStats;
}

export function VersionDuelStats({ v1, v2 }: VersionDuelStatsProps) {
  const renderStatCard = (stat: VersionStats, opponent: VersionStats) => {
    const isWinner = stat.isChampion;
    const wrDiff = stat.winRate - opponent.winRate;
    const pnlDiff = stat.pnlPct - opponent.pnlPct;
    
    return (
      <div 
        className="flex-1 rounded-2xl p-5 relative overflow-hidden transition-all duration-300"
        style={{ 
          background: isWinner ? C.glow : C.elevated, 
          border: `1px solid ${isWinner ? C.neonGreen : C.border}`,
          boxShadow: isWinner ? `0 0 20px ${C.glow}` : "none"
        }}
      >
        {isWinner && (
          <div className="absolute top-0 right-0 bg-opacity-20 px-3 py-1 rounded-bl-xl text-[10px] font-bold tracking-wider flex items-center gap-1" style={{ background: C.neonGreen, color: "#111" }}>
            <Trophy size={12} /> CHAMPION
          </div>
        )}
        
        <div className="flex items-center gap-2 mb-4">
          <div className="w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm" style={{ background: isWinner ? C.neonGreen : C.surface, color: isWinner ? "#111" : C.muted, border: `1px solid ${isWinner ? "transparent" : C.border}` }}>
            {stat.versionName}
          </div>
          <div>
            <h4 className="text-sm font-semibold" style={{ color: isWinner ? C.neonGreen : C.text }}>Version {stat.versionName}</h4>
            <div className="text-[10px] uppercase tracking-wider" style={{ color: C.dim }}>{stat.trades} Trades Analyzed</div>
          </div>
        </div>
        
        <div className="space-y-4">
          {/* EV Score */}
          <div className="flex justify-between items-center pb-2 border-b" style={{ borderColor: C.border }}>
            <span className="text-[11px] flex items-center gap-1" style={{ color: C.muted }}><Activity size={12}/> EV Score</span>
            <span className="font-mono text-sm font-bold" style={{ color: C.text }}>{stat.evScore.toFixed(2)}</span>
          </div>
          
          {/* Win Rate */}
          <div className="flex justify-between items-center pb-2 border-b" style={{ borderColor: C.border }}>
            <span className="text-[11px] flex items-center gap-1" style={{ color: C.muted }}><Target size={12}/> Win Rate</span>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold" style={{ color: C.text }}>{(stat.winRate * 100).toFixed(1)}%</span>
              {wrDiff !== 0 && (
                <span className="text-[10px] flex items-center" style={{ color: wrDiff > 0 ? C.neonGreen : C.red }}>
                  {wrDiff > 0 ? <TrendingUp size={10} className="mr-0.5"/> : <TrendingDown size={10} className="mr-0.5"/>}
                  {Math.abs(wrDiff * 100).toFixed(1)}%
                </span>
              )}
            </div>
          </div>
          
          {/* PnL */}
          <div className="flex justify-between items-center pb-2 border-b" style={{ borderColor: C.border }}>
            <span className="text-[11px] flex items-center gap-1" style={{ color: C.muted }}><BarChart3 size={12}/> P&L Médio</span>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold" style={{ color: C.text }}>{stat.pnlPct > 0 ? "+" : ""}{stat.pnlPct.toFixed(2)}%</span>
              {pnlDiff !== 0 && (
                <span className="text-[10px] flex items-center" style={{ color: pnlDiff > 0 ? C.neonGreen : C.red }}>
                  {pnlDiff > 0 ? <TrendingUp size={10} className="mr-0.5"/> : <TrendingDown size={10} className="mr-0.5"/>}
                  {Math.abs(pnlDiff).toFixed(2)}%
                </span>
              )}
            </div>
          </div>
          
          {/* Confidence */}
          <div className="flex justify-between items-center pt-2">
            <span className="text-[11px] flex items-center gap-1" style={{ color: C.muted }}><Settings2 size={12}/> Statistical Confidence</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase" style={{ 
              background: stat.confidenceScore === "High" ? `${C.neonGreen}22` : stat.confidenceScore === "Medium" ? `${C.amber}22` : `${C.red}22`,
              color: stat.confidenceScore === "High" ? C.neonGreen : stat.confidenceScore === "Medium" ? C.amber : C.red
            }}>
              {stat.confidenceScore}
            </span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col md:flex-row gap-4 w-full">
      {renderStatCard(v1, v2)}
      
      <div className="flex items-center justify-center">
        <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold italic" style={{ background: C.elevated, color: C.dim, border: `1px solid ${C.border}` }}>
          VS
        </div>
      </div>
      
      {renderStatCard(v2, v1)}
    </div>
  );
}

// Dummy icon components since they aren't imported fully above
function Target(props: any) { return <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg> }
function BarChart3(props: any) { return <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg> }
