"use client";

import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { DollarSign, Trophy, Wallet, Briefcase } from "lucide-react";
import { apiGet } from "@/lib/api";

interface DashboardPosition {
  id: string;
  market_type: "spot" | "futures";
  symbol: string;
  asset: string;
  direction: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  current_value: number;
  profit_loss: number;
  profit_loss_pct: number;
}

interface CapitalPoint {
  time: string;
  value: number;
}

interface DashboardOverview {
  today_pnl: number;
  consolidated_pnl: number;
  realized_total_pnl: number;
  unrealized_pnl: number;
  win_rate: number;
  open_positions_count: number;
  open_positions: DashboardPosition[];
  portfolio_value: number;
  spot_value: number;
  futures_value: number;
  capital_evolution: CapitalPoint[];
  source: string;
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value ?? 0);
}

function formatPercent(value: number) {
  return `${value >= 0 ? "+" : ""}${(value ?? 0).toFixed(2)}%`;
}

function MetricCard({
  label,
  value,
  helper,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  helper?: string;
  icon: React.ElementType;
  tone?: "profit" | "loss" | "neutral";
}) {
  const color =
    tone === "profit"
      ? "var(--color-profit)"
      : tone === "loss"
        ? "var(--color-loss)"
        : "var(--text-primary)";

  return (
    <div className="metric-card">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={14} style={{ color: "var(--accent-primary)" }} />
        <span className="label">{label}</span>
      </div>
      <div className="data-value text-[28px]" style={{ color }}>
        {value}
      </div>
      {helper ? <p className="text-[12px] text-[var(--text-secondary)] mt-2">{helper}</p> : null}
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<DashboardOverview>("/analytics/dashboard?days=30&min_value_usdt=10")
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  const chartData = useMemo(
    () =>
      (data?.capital_evolution ?? []).map((point) => ({
        ...point,
        label: point.time
          ? new Date(point.time).toLocaleDateString("en", { month: "short", day: "numeric" })
          : "",
      })),
    [data],
  );

  const todayTone = (data?.today_pnl ?? 0) >= 0 ? "profit" : "loss";
  const consolidatedTone = (data?.consolidated_pnl ?? 0) >= 0 ? "profit" : "loss";

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Dashboard</h1>
          <p className="text-[13px] text-[var(--text-secondary)] mt-1">
            Gate-backed portfolio snapshot with realized trade analytics.
          </p>
        </div>
        {data ? (
          <span className="caption">
            Source: {data.source === "exchange" ? "Gate.io" : "Local DB fallback"}
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="metric-card">
              <div className="skeleton h-4 w-24 mb-3 rounded" />
              <div className="skeleton h-8 w-36 rounded" />
              <div className="skeleton h-3 w-28 mt-3 rounded" />
            </div>
          ))}
        </div>
      ) : data ? (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            <MetricCard
              label="Today's P&L"
              value={formatCurrency(data.today_pnl)}
              helper={`Realized total: ${formatCurrency(data.realized_total_pnl)}`}
              icon={DollarSign}
              tone={todayTone}
            />
            <MetricCard
              label="Consolidated P&L"
              value={formatCurrency(data.consolidated_pnl)}
              helper={`Unrealized: ${formatCurrency(data.unrealized_pnl)}`}
              icon={Wallet}
              tone={consolidatedTone}
            />
            <MetricCard
              label="Open Positions"
              value={String(data.open_positions_count)}
              helper={`Spot ${formatCurrency(data.spot_value)} • Futures ${formatCurrency(data.futures_value)}`}
              icon={Briefcase}
            />
            <MetricCard
              label="Win Rate"
              value={`${data.win_rate.toFixed(2)}%`}
              helper={`Portfolio value ${formatCurrency(data.portfolio_value)}`}
              icon={Trophy}
              tone={data.win_rate >= 50 ? "profit" : "loss"}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[1.4fr,1fr] gap-6">
            <div className="card">
              <div className="card-header">
                <h3>Capital Evolution</h3>
                <span className="caption">30D</span>
              </div>
              <div className="card-body" style={{ height: 320 }}>
                {chartData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 10, right: 16, left: 8, bottom: 0 }}>
                      <defs>
                        <linearGradient id="capital" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#4F7BF7" stopOpacity={0.25} />
                          <stop offset="100%" stopColor="#4F7BF7" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                      <XAxis dataKey="label" tick={{ fill: "var(--text-tertiary)", fontSize: 11 }} tickLine={false} />
                      <YAxis
                        tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
                        tickFormatter={(value: number) => `$${Math.round(value).toLocaleString()}`}
                        tickLine={false}
                        axisLine={false}
                      />
                      <Tooltip
                        formatter={(value: number) => formatCurrency(value)}
                        labelFormatter={(value) => String(value)}
                        contentStyle={{
                          background: "var(--bg-elevated)",
                          border: "1px solid var(--border-default)",
                          borderRadius: "var(--radius-md)",
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="value"
                        stroke="#4F7BF7"
                        strokeWidth={2}
                        fill="url(#capital)"
                        dot={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="empty-state h-full">
                    <Wallet size={40} />
                    <p>No capital evolution available yet.</p>
                  </div>
                )}
              </div>
            </div>

            <div className="card">
              <div className="card-header">
                <h3>Open Positions</h3>
                <span className="caption">{data.open_positions.length} assets &gt; 10 USDT</span>
              </div>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Asset</th>
                      <th className="numeric">Value</th>
                      <th className="numeric">P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.open_positions.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="text-center py-12 text-[var(--text-tertiary)]">
                          No positions above 10 USDT.
                        </td>
                      </tr>
                    ) : (
                      data.open_positions.map((position) => (
                        <tr key={position.id}>
                          <td>
                            <div className="font-semibold">{position.symbol}</div>
                            <span className="caption">{position.market_type}</span>
                          </td>
                          <td className="numeric">{formatCurrency(position.current_value)}</td>
                          <td className={`numeric ${position.profit_loss >= 0 ? "profit" : "loss"}`}>
                            {formatCurrency(position.profit_loss)}
                            <div className="text-[12px] mt-0.5">{formatPercent(position.profit_loss_pct)}</div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      ) : (
        <div className="empty-state">
          <Wallet size={40} />
          <p>Unable to load dashboard data.</p>
        </div>
      )}
    </div>
  );
}
