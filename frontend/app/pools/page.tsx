"use client";

import { useEffect, useState } from "react";
import { Plus, Layers, Trash2 } from "lucide-react";
import { apiGet, apiPost, apiDelete } from "@/lib/api";

export default function PoolsPage() {
  const [pools, setPools] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newMode, setNewMode] = useState("paper");

  const fetchPools = async () => {
    setLoading(true);
    try {
      const data = await apiGet("/pools");
      setPools(data.pools || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => { fetchPools(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await apiPost("/pools", { name: newName, description: newDesc, mode: newMode, is_active: true });
      setShowCreate(false);
      setNewName("");
      setNewDesc("");
      fetchPools();
    } catch (e: any) {
      alert(`Failed: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Strategy Pools</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Isolated trading environments with independent config overrides.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <Plus className="w-4 h-4 mr-2" />Create Pool
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className="card border-l-4 border-l-[var(--accent-primary)]">
          <div className="card-body space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="label">Pool Name</label>
                <input className="input" placeholder="e.g. Scalping Core" value={newName} onChange={(e) => setNewName(e.target.value)} />
              </div>
              <div className="space-y-2">
                <label className="label">Mode</label>
                <select className="input" value={newMode} onChange={(e) => setNewMode(e.target.value)}>
                  <option value="paper">Paper Trading</option>
                  <option value="live">Live Trading</option>
                </select>
              </div>
            </div>
            <div className="space-y-2">
              <label className="label">Description</label>
              <input className="input" placeholder="Optional description..." value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
            </div>
            <div className="flex gap-2 justify-end">
              <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleCreate}>Create Pool</button>
            </div>
          </div>
        </div>
      )}

      {/* Pool Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map((i) => <div key={i} className="skeleton h-48 rounded-[var(--radius-lg)]" />)}
        </div>
      ) : pools.length === 0 ? (
        <div className="card border-dashed border-2 border-[var(--border-subtle)] bg-transparent">
          <div className="card-body text-center py-16">
            <Layers className="w-12 h-12 text-[var(--text-tertiary)] opacity-30 mx-auto mb-4" />
            <h3 className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">No Pools Yet</h3>
            <p className="text-[var(--text-secondary)] text-[13px] max-w-sm mx-auto mb-6">
              Pools are isolated trading environments. Each pool can have its own set of coins and config overrides.
            </p>
            <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
              <Plus className="w-4 h-4 mr-2" />Create First Pool
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {pools.map((pool: any) => (
            <div key={pool.id} className="card flex flex-col">
              <div className="card-body flex-1 p-6">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] tracking-tight">{pool.name}</h3>
                    <div className="flex items-center gap-2 mt-2">
                      <span className={`badge ${pool.mode === "live" ? "bullish" : "range"}`}>{pool.mode?.toUpperCase()}</span>
                      <span className="caption flex items-center gap-1.5">
                        <span className={`w-1.5 h-1.5 rounded-full ${pool.is_active ? "bg-[var(--color-profit)]" : "bg-[var(--color-neutral)]"}`}></span>
                        {pool.is_active ? "Active" : "Paused"}
                      </span>
                    </div>
                  </div>
                </div>
                {pool.description && (
                  <p className="text-[13px] text-[var(--text-secondary)] mb-3">{pool.description}</p>
                )}
                <div className="text-[12px] text-[var(--text-tertiary)]">
                  Created {pool.created_at ? new Date(pool.created_at).toLocaleDateString() : "—"}
                </div>
              </div>
              <div className="border-t border-[var(--border-default)] p-3 flex justify-end">
                <button className="btn btn-secondary text-[12px] px-3 py-1.5">Configure</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
