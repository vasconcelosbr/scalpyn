"use client";

import { 
  Activity, 
  Target, 
  TrendingUp, 
  Wallet,
  Clock,
  Zap
} from "lucide-react";
import { 
  Area, 
  AreaChart, 
  CartesianGrid, 
  ResponsiveContainer, 
  Tooltip, 
  XAxis, 
  YAxis 
} from "recharts";
import { formatCurrency, formatPercent } from "@/lib/utils";

const chartData = [
  { time: "00:00", value: 10000 },
  { time: "04:00", value: 10250 },
  { time: "08:00", value: 10100 },
  { time: "12:00", value: 10600 },
  { time: "16:00", value: 10450 },
  { time: "20:00", value: 11200 },
  { time: "24:00", value: 11450 },
];

const mockTopAssets = [
  { symbol: "BTCUSDT", price: 64250.0, trend: "Bullish", score: 85, scoreLevel: "excellent" },
  { symbol: "SOLUSDT", price: 142.5, trend: "Bullish", score: 92, scoreLevel: "excellent" },
  { symbol: "ETHUSDT", price: 3450.5, trend: "Range", score: 65, scoreLevel: "good" },
  { symbol: "BNBUSDT", price: 590.2, trend: "Range", score: 55, scoreLevel: "neutral" },
  { symbol: "ADAUSDT", price: 0.45, trend: "Bearish", score: 35, scoreLevel: "low" },
];

export default function Home() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Overview</h1>
      </div>
      
      {/* Metrics Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 metric-cards-grid">
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2">
            <h3 className="label">Total P&L (24h)</h3>
            <TrendingUp className="w-4 h-4 text-[var(--text-tertiary)]" />
          </div>
          <div className="value profit">+$1,245.50</div>
          <p className="change text-[var(--color-profit)]">▲ +3.2%</p>
        </div>
        
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2">
            <h3 className="label">Open Positions</h3>
            <Target className="w-4 h-4 text-[var(--text-tertiary)]" />
          </div>
          <div className="value">4</div>
          <p className="change text-[var(--text-secondary)]">3 spot / 1 futures</p>
        </div>
        
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2">
            <h3 className="label">Win Rate</h3>
            <Activity className="w-4 h-4 text-[var(--text-tertiary)]" />
          </div>
          <div className="value">72.3%</div>
          <p className="change text-[var(--text-secondary)]">34W / 13L (7d)</p>
        </div>
        
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2">
            <h3 className="label">Bot Status</h3>
            <Wallet className="w-4 h-4 text-[var(--text-tertiary)]" />
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="live-dot bg-[var(--color-profit)] w-2 h-2" />
            <span className="font-semibold text-[var(--color-profit)] text-sm tracking-widest uppercase">LIVE</span>
          </div>
          <p className="change text-[var(--text-secondary)] mt-2">Binance OK · 12ms ping</p>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 dashboard-bottom">
        {/* Chart Area */}
        <div className="xl:col-span-2 card">
          <div className="card-header">
            <h3>Capital Evolution</h3>
            <div className="flex gap-2">
              <button className="btn-ghost text-xs">7D</button>
              <button className="btn-ghost text-xs bg-[var(--bg-hover)] text-[var(--text-primary)]">30D</button>
              <button className="btn-ghost text-xs">90D</button>
              <button className="btn-ghost text-xs">YTD</button>
            </div>
          </div>
          <div className="card-body h-[320px] w-full pt-6">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="profitGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#34D399" stopOpacity={0.15} />
                    <stop offset="100%" stopColor="#34D399" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="4 4" stroke="rgba(255, 255, 255, 0.04)" vertical={false} />
                <XAxis 
                  dataKey="time" 
                  axisLine={false} 
                  tickLine={false} 
                  tick={{ fill: '#555B6E', fontSize: 11, fontFamily: 'JetBrains Mono' }} 
                  dy={10}
                />
                <YAxis 
                  axisLine={false} 
                  tickLine={false} 
                  tick={{ fill: '#555B6E', fontSize: 11, fontFamily: 'JetBrains Mono' }} 
                  tickFormatter={(value) => `$${value/1000}k`}
                />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#1A1B25', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontFamily: 'JetBrains Mono', color: '#8B92A5' }}
                  itemStyle={{ color: '#E8ECF4' }}
                />
                <Area 
                  type="monotone" 
                  dataKey="value" 
                  stroke="#34D399" 
                  strokeWidth={2} 
                  fillOpacity={1} 
                  fill="url(#profitGradient)" 
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Assets & Activity Feed (stacked vertically) */}
        <div className="flex flex-col gap-6">
          <div className="card">
            <div className="card-header">
              <h3>Top Ranked Assets</h3>
            </div>
            <div className="p-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th className="w-[120px]">Score</th>
                    <th className="text-right">Price</th>
                  </tr>
                </thead>
                <tbody>
                  {mockTopAssets.map((asset) => (
                    <tr key={asset.symbol}>
                      <td className="font-semibold">{asset.symbol}</td>
                      <td>
                        <div className="score-bar" data-level={asset.scoreLevel}>
                          <div className="bar-track">
                            <div className="bar-fill" style={{ width: `${asset.score}%` }} />
                          </div>
                        </div>
                      </td>
                      <td className="numeric text-[var(--text-secondary)]">{formatCurrency(asset.price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card flex-1">
            <div className="card-header">
              <h3>Recent Activity</h3>
            </div>
            <div className="card-body space-y-4">
              <div className="flex gap-4">
                <div className="mt-1 w-6 h-6 rounded-full bg-[var(--color-profit-muted)] text-[var(--color-profit)] flex items-center justify-center shrink-0">
                  <TrendingUp className="w-3 h-3" />
                </div>
                <div>
                  <div className="font-semibold text-[13px]">BUY BTCUSDT</div>
                  <div className="data-value text-[var(--color-profit)] text-[13px] mt-0.5">Filled @ 64,250</div>
                  <div className="flex items-center gap-1 mt-1 text-[var(--text-tertiary)] text-[11px] uppercase tracking-wider font-semibold">
                    <Clock className="w-3 h-3" /> 2 mins ago
                  </div>
                </div>
              </div>
              <div className="flex gap-4">
                <div className="mt-1 w-6 h-6 rounded-full bg-[var(--color-loss-muted)] text-[var(--color-loss)] flex items-center justify-center shrink-0">
                  <TrendingUp className="w-3 h-3 transform rotate-180" />
                </div>
                <div>
                  <div className="font-semibold text-[13px]">SELL SOLUSDT</div>
                  <div className="data-value text-[var(--color-loss)] text-[13px] mt-0.5">Filled @ 142.50</div>
                  <div className="flex items-center gap-1 mt-1 text-[var(--text-tertiary)] text-[11px] uppercase tracking-wider font-semibold">
                    <Clock className="w-3 h-3" /> 45 mins ago
                  </div>
                </div>
              </div>
              <div className="flex gap-4">
                <div className="mt-1 w-6 h-6 rounded-full bg-[var(--accent-primary-muted)] text-[var(--accent-primary)] flex items-center justify-center shrink-0">
                  <Zap className="w-3 h-3" />
                </div>
                <div>
                  <div className="font-semibold text-[13px]">System Signal</div>
                  <div className="data-value text-[var(--accent-primary)] text-[13px] mt-0.5">ETH Alpha &gt; 80</div>
                  <div className="flex items-center gap-1 mt-1 text-[var(--text-tertiary)] text-[11px] uppercase tracking-wider font-semibold">
                    <Clock className="w-3 h-3" /> 1 hour ago
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
