"use client";

import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceDot } from "recharts";

const C = {
  surface: "#10121A", elevated: "#161824", border: "rgba(255,255,255,0.08)", text: "#E6E8EE",
  muted: "#8A91A4", dim: "#5A6075", green: "#22B97A", blue: "#4F7BF7", amber: "#F2A33A",
  red: "#E5484D", purple: "#9D7CF7", neonGreen: "#00FF66",
} as const;

export interface ChartDataPoint {
  index: number;
  evScore: number;
  winRate: number;
  isMutationPoint?: boolean;
  version?: string;
  mutationDetails?: { added: string[]; removed: string[] };
}

interface VersionComparisonChartProps {
  data: ChartDataPoint[];
}

export function VersionComparisonChart({ data }: VersionComparisonChartProps) {
  const mutationPoint = useMemo(() => data.find(d => d.isMutationPoint), [data]);

  return (
    <div className="w-full h-64 rounded-xl p-4 relative" style={{ background: C.elevated, border: `1px solid ${C.border}` }}>
      <div className="absolute top-4 left-4 flex flex-col">
        <h3 className="text-sm font-semibold tracking-wide" style={{ color: C.text }}>Performance Timeline (EV Score)</h3>
        <span className="text-[11px]" style={{ color: C.muted }}>v1 vs v2 Comparison</span>
      </div>
      
      <div className="w-full h-full mt-6">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="colorBefore" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={C.blue} stopOpacity={0.3}/>
                <stop offset="95%" stopColor={C.blue} stopOpacity={0}/>
              </linearGradient>
              <linearGradient id="colorAfter" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={C.neonGreen} stopOpacity={0.3}/>
                <stop offset="95%" stopColor={C.neonGreen} stopOpacity={0}/>
              </linearGradient>
            </defs>
            <XAxis dataKey="index" hide />
            <YAxis stroke={C.dim} fontSize={10} tickLine={false} axisLine={false} />
            <Tooltip 
              content={({ active, payload }) => {
                if (active && payload && payload.length) {
                  const d = payload[0].payload as ChartDataPoint;
                  return (
                    <div className="p-3 rounded-lg backdrop-blur-md" style={{ background: "rgba(16, 18, 26, 0.8)", border: `1px solid ${C.border}` }}>
                      <p className="font-bold text-xs mb-1" style={{ color: C.text }}>{d.version || "Trades"}</p>
                      <p className="text-[11px] font-mono" style={{ color: d.version === 'v2' ? C.neonGreen : C.blue }}>EV: {d.evScore.toFixed(2)}</p>
                      <p className="text-[11px] font-mono" style={{ color: C.muted }}>WR: {(d.winRate * 100).toFixed(1)}%</p>
                      
                      {d.isMutationPoint && d.mutationDetails && (
                        <div className="mt-2 pt-2 border-t" style={{ borderColor: C.border }}>
                          <p className="text-[10px] uppercase font-bold text-white mb-1">Mutation Injected</p>
                          {d.mutationDetails.added.map(a => <p key={a} className="text-[10px]" style={{ color: C.neonGreen }}>+ {a}</p>)}
                          {d.mutationDetails.removed.map(r => <p key={r} className="text-[10px]" style={{ color: C.red }}>- {r}</p>)}
                        </div>
                      )}
                    </div>
                  );
                }
                return null;
              }}
            />
            
            {mutationPoint && (
              <ReferenceLine 
                x={mutationPoint.index} 
                stroke={C.neonGreen} 
                strokeDasharray="3 3" 
                label={{ position: "top", value: "v2 Deployed", fill: C.neonGreen, fontSize: 10, fontWeight: "bold" }} 
              />
            )}
            
            {/* Split the line based on mutation to give distinct colors */}
            <Area 
              type="monotone" 
              dataKey={(d: ChartDataPoint) => d.version === 'v1' ? d.evScore : null} 
              stroke={C.blue} 
              fillOpacity={1} 
              fill="url(#colorBefore)" 
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Area 
              type="monotone" 
              dataKey={(d: ChartDataPoint) => d.version === 'v2' || d.isMutationPoint ? d.evScore : null} 
              stroke={C.neonGreen} 
              fillOpacity={1} 
              fill="url(#colorAfter)" 
              strokeWidth={2}
              isAnimationActive={false}
            />
            
            {mutationPoint && (
              <ReferenceDot 
                x={mutationPoint.index} 
                y={mutationPoint.evScore} 
                r={6} 
                fill={C.surface} 
                stroke={C.neonGreen} 
                strokeWidth={2} 
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
