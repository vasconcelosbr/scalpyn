"use client";

import { useState, useEffect } from "react";

interface Pool {
  id: string;
  name: string;
  description?: string;
  is_active: boolean;
  mode: string;
  asset_count: number;
  autopilot_enabled: boolean;
}

interface Props {
  value?: string | null;
  onChange: (poolId: string | null) => void;
  placeholder?: string;
  disabled?: boolean;
}

export default function PoolSelector({ value, onChange, placeholder = "Selecionar pool...", disabled }: Props) {
  const [pools, setPools] = useState<Pool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchPools = async () => {
      setLoading(true);
      try {
        const res = await fetch("/api/pools/");
        const data = await res.json();
        setPools(data.pools || []);
      } catch (e: any) {
        setError("Erro ao carregar pools");
      } finally {
        setLoading(false);
      }
    };
    fetchPools();
  }, []);

  if (loading) {
    return (
      <select disabled style={{ width: "100%", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
        <option>Carregando pools...</option>
      </select>
    );
  }

  if (error) {
    return (
      <select disabled style={{ width: "100%", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
        <option>{error}</option>
      </select>
    );
  }

  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      style={{
        width: "100%",
        padding: "8px 12px",
        borderRadius: 8,
        fontSize: 13,
        background: "var(--bg-elevated, #1a1a2e)",
        border: "1px solid rgba(255,255,255,0.1)",
        color: "var(--text-primary, #fff)",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <option value="">{placeholder}</option>
      {pools.map((pool) => (
        <option key={pool.id} value={pool.id}>
          {pool.name}
          {pool.asset_count > 0 ? ` (${pool.asset_count} ativos)` : ""}
          {pool.autopilot_enabled ? " 🤖" : ""}
        </option>
      ))}
    </select>
  );
}
