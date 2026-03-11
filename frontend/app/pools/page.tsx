"use client";

import { useState } from "react";
import { formatPercent } from "@/lib/utils";

const MOCK_POOLS = [
  { id: "p1", name: "Main Live Core", mode: "live", active: true, coins: 15, pl: 14.5 },
  { id: "p2", name: "Altcoin Rotation Test", mode: "paper", active: true, coins: 50, pl: -2.1 },
  { id: "p3", name: "Aggressive Breakout", mode: "paper", active: false, coins: 10, pl: 0.0 },
];

export default function PoolsPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Strategy Pools</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Manage isolated subsets of coins and override strategies.</p>
        </div>
        <button className="btn btn-primary">
          + Init Pool Vector
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {MOCK_POOLS.map((pool) => (
          <div key={pool.id} className="card flex flex-col">
            <div className="card-body flex-1 p-6">
              <div className="flex justify-between items-start mb-6">
                <div>
                  <h3 className="text-lg font-bold text-[var(--text-primary)] tracking-tight">{pool.name}</h3>
                  <div className="flex items-center gap-2 mt-2">
                    {pool.mode === "live" ? (
                      <span className="badge bullish">LIVE</span>
                    ) : (
                      <span className="badge range">PAPER</span>
                    )}
                    {pool.active ? (
                      <span className="caption flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-[var(--color-profit)]"></span>Active</span>
                    ) : (
                      <span className="caption flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-[var(--color-neutral)]"></span>Paused</span>
                    )}
                  </div>
                </div>
              </div>
              
              <div className="space-y-4 mb-4 mt-2">
                <div className="flex justify-between items-center text-[13px] border-b border-[var(--border-subtle)] pb-2">
                  <span className="text-[var(--text-secondary)] font-medium">Assets Tracked</span>
                  <span className="data-value text-[var(--text-primary)]">{pool.coins}</span>
                </div>
                <div className="flex justify-between items-center text-[13px]">
                  <span className="text-[var(--text-secondary)] font-medium">30d Alpha Return</span>
                  <span className={`percentage text-[14px] font-bold ${pool.pl >= 0 ? 'profit' : 'loss'}`}>
                    {pool.pl > 0 ? '+' : ''}{formatPercent(pool.pl)}
                  </span>
                </div>
              </div>
            </div>
            
            <div className="grid grid-cols-2 border-t border-[var(--border-default)]">
              <button className="py-3.5 text-[13px] font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] border-r border-[var(--border-default)] transition-colors">
                Configure Nodes
              </button>
              <button className="py-3.5 text-[13px] font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors">
                Override Rules
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
