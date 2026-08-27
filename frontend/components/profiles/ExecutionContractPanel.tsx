"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Database,
  Download,
  Fingerprint,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import { apiGet } from "@/lib/api";

type SectionName = "filters" | "signals" | "entry_triggers" | "block_rules";

interface SectionParity {
  profile_projection_hash: string;
  version_hash: string | null;
  runtime_hash: string;
  match: boolean;
}

interface RequiredCondition {
  section: string;
  indicator?: string | null;
  operator?: string | null;
  value?: unknown;
  min?: unknown;
  max?: unknown;
  period?: unknown;
  timeframe?: unknown;
  fingerprint: string;
}

interface ExecutionContractResponse {
  status: "MATCH" | "MISMATCH";
  profile_id: string;
  profile_name: string;
  contract: {
    profile_version_id: string | null;
    profile_projection_hash: string;
    version_config_hash: string | null;
    runtime_hash: string;
    sections: Record<SectionName, SectionParity>;
    contract_valid: boolean;
    reason_codes: string[];
    missing_required_conditions: RequiredCondition[];
    unexpected_required_conditions: RequiredCondition[];
  };
  latest_runtime?: {
    evaluated_at?: string;
    symbol?: string;
    shadow_decision?: string;
    operational_effect?: boolean;
    payload?: {
      execution_contract?: {
        status?: string;
        contract_valid?: boolean;
        reason_codes?: string[];
        profile_version_id?: string | null;
      };
    };
  } | null;
  round_trip_export: Record<string, unknown>;
}

const SECTION_LABELS: Record<SectionName, string> = {
  filters: "Filters",
  signals: "Signals",
  entry_triggers: "Entry Triggers",
  block_rules: "Block Rules",
};

const shortHash = (value?: string | null) => value ? `${value.slice(0, 12)}…${value.slice(-8)}` : "não disponível";

function ConditionList({ title, items, tone }: { title: string; items: RequiredCondition[]; tone: "danger" | "warning" }) {
  if (!items.length) return null;
  const color = tone === "danger" ? "#FB7185" : "#FBBF24";
  return (
    <div style={{ padding: 16, borderRadius: 12, border: `1px solid ${color}33`, background: `${color}0D` }}>
      <div style={{ color, fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{title}</div>
      <div style={{ display: "grid", gap: 8 }}>
        {items.map((item) => (
          <div key={item.fingerprint} style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12 }}>
            <span style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
              {item.section} · {item.indicator || "condição"} {item.operator || ""} {String(item.value ?? item.min ?? "")}
            </span>
            <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}>{shortHash(item.fingerprint)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ExecutionContractPanel({ profileId }: { profileId: string }) {
  const [data, setData] = useState<ExecutionContractResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<ExecutionContractResponse>(`/profiles/${profileId}/execution-contract`));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setLoading(false);
    }
  }, [profileId]);

  useEffect(() => { void load(); }, [load]);

  const runtimeContract = data?.latest_runtime?.payload?.execution_contract;
  const runtimeStatus = runtimeContract?.status || "SEM SNAPSHOT";
  const sectionEntries = useMemo(
    () => data ? (Object.entries(data.contract.sections) as [SectionName, SectionParity][]) : [],
    [data],
  );

  const downloadRoundTrip = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data.round_trip_export, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${data.profile_name}-execution-contract.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return <div className="card"><div className="card-body" style={{ padding: 24, color: "var(--text-secondary)" }}>Carregando integridade do contrato…</div></div>;
  }
  if (error || !data) {
    return (
      <div className="card"><div className="card-body" style={{ padding: 24 }}>
        <div style={{ color: "#FB7185", fontSize: 13 }}>Não foi possível carregar o contrato: {error || "resposta vazia"}</div>
        <button className="btn btn-secondary" onClick={load} style={{ marginTop: 14 }}>Tentar novamente</button>
      </div></div>
    );
  }

  const healthy = data.status === "MATCH";
  const accent = healthy ? "#34D399" : "#FB7185";
  const StatusIcon = healthy ? ShieldCheck : ShieldAlert;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ height: 3, background: accent }} />
        <div className="card-body" style={{ padding: 24 }}>
          <div style={{ display: "flex", gap: 16, justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
              <div style={{ width: 46, height: 46, display: "grid", placeItems: "center", borderRadius: 14, color: accent, background: `${accent}14`, border: `1px solid ${accent}33` }}>
                <StatusIcon size={23} />
              </div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 750, color: "var(--text-primary)" }}>Integridade de execução · {data.status}</div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>Persistido × Runtime, versão imutável e último snapshot</div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-secondary" onClick={load} style={{ display: "flex", alignItems: "center", gap: 7 }}><RefreshCw size={14} /> Atualizar</button>
              <button className="btn btn-secondary" onClick={downloadRoundTrip} style={{ display: "flex", alignItems: "center", gap: 7 }}><Download size={14} /> Exportar contrato</button>
            </div>
          </div>

          {data.contract.reason_codes.length > 0 && (
            <div style={{ marginTop: 18, display: "flex", flexWrap: "wrap", gap: 7 }}>
              {data.contract.reason_codes.map((reason) => <span key={reason} style={{ padding: "5px 9px", borderRadius: 999, background: "rgba(251,113,133,.10)", color: "#FB7185", fontSize: 10, fontWeight: 750, fontFamily: "var(--font-mono)" }}>{reason}</span>)}
            </div>
          )}
        </div>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        {[
          { icon: Fingerprint, label: "Profile ID", value: data.profile_id },
          { icon: Database, label: "Versão imutável", value: data.contract.profile_version_id || "AUSENTE" },
          { icon: Activity, label: "Último runtime", value: `${runtimeStatus}${data.latest_runtime?.symbol ? ` · ${data.latest_runtime.symbol}` : ""}` },
        ].map(({ icon: Icon, label, value }) => (
          <div key={label} className="card"><div className="card-body" style={{ padding: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, color: "var(--text-tertiary)", fontSize: 10, textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 700 }}><Icon size={13} />{label}</div>
            <div title={value} style={{ marginTop: 9, color: "var(--text-primary)", fontFamily: "var(--font-mono)", fontSize: 12, overflowWrap: "anywhere" }}>{value}</div>
          </div></div>
        ))}
      </section>

      <section className="card"><div className="card-body" style={{ padding: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 14 }}>Paridade por seção</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
          {sectionEntries.map(([section, parity]) => (
            <div key={section} style={{ padding: 14, borderRadius: 11, border: `1px solid ${parity.match ? "rgba(52,211,153,.24)" : "rgba(251,113,133,.28)"}`, background: parity.match ? "rgba(52,211,153,.05)" : "rgba(251,113,133,.05)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: "var(--text-primary)", fontSize: 12, fontWeight: 700 }}>{SECTION_LABELS[section]}</span>
                <span style={{ color: parity.match ? "#34D399" : "#FB7185", fontSize: 10, fontWeight: 800 }}>{parity.match ? "MATCH" : "MISMATCH"}</span>
              </div>
              <div style={{ marginTop: 10, display: "grid", gap: 5, color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: 10 }}>
                <div>persistido · {shortHash(parity.profile_projection_hash)}</div>
                <div>versão · {shortHash(parity.version_hash)}</div>
                <div>runtime · {shortHash(parity.runtime_hash)}</div>
              </div>
            </div>
          ))}
        </div>
      </div></section>

      <ConditionList title="Condições obrigatórias ausentes no runtime" items={data.contract.missing_required_conditions} tone="danger" />
      <ConditionList title="Condições obrigatórias inesperadas no runtime" items={data.contract.unexpected_required_conditions} tone="warning" />
    </div>
  );
}
