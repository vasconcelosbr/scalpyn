"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

type History = {
  total: number;
  retained_since: string | null;
  latest_collection: { received_at_display: string; status: string; source_count: number } | null;
  items: { id: string; symbol: string; received_at_display: string; source_updated_at_display: string | null; pool_result: string }[];
};
const outcomes: Record<string, string> = {
  PRESENT: "Presente no PUMP", NOT_INCLUDED: "Não incluído no pool",
  FILTERED: "Filtrado antes da inclusão", PENDING: "Aguardando conferência",
  NOT_VERIFIED: "Sincronização não confirmada",
};
const states: Record<string, string> = {
  SYNCED: "Sincronizada", RECEIVED: "Recebida", UNAVAILABLE: "Feed indisponível",
  SYNC_FAILED: "Falha na sincronização", SKIPPED: "Sincronização não realizada",
};

export default function RadarFeedHistory({ poolId, poolName }: { poolId: string; poolName: string }) {
  const [data, setData] = useState<History | null>(null);
  const [search, setSearch] = useState("");
  const [symbol, setSymbol] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const reload = useCallback(() => setRevision(n => n + 1), []);
  useEffect(() => {
    let active = true;
    setBusy(true);
    setError("");
    apiGet<History>(`/pools/${poolId}/radar-history?limit=50&offset=${offset}&symbol=${encodeURIComponent(symbol)}`)
      .then(result => { if (active) setData(result); })
      .catch(() => { if (active) { setData(null); setError("Não foi possível consultar o histórico."); } })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [poolId, symbol, offset, revision]);

  return <section className="card" aria-label="Histórico da API do Radar">
    <div className="card-header"><h3>Histórico da API · {poolName}</h3>
      <button type="button" className="btn btn-secondary" onClick={reload} disabled={busy}>Atualizar histórico</button>
    </div>
    <div style={{ padding: "16px" }}>
      <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
        Recebimentos dos últimos 7 dias, excluídos automaticamente após esse prazo.
        Cada linha corresponde a um ativo recebido em uma consulta. Presença no pool não significa aprovação para entrada.
      </p>
      <p style={{ color: "var(--text-tertiary)", fontSize: "12px" }}>
        Horário da API: atualização informada pelo provedor, quando disponível.
        Coleta: momento em que o Scalpyn recebeu a lista. Horários em GMT-3.
      </p>
      {data?.retained_since && <p style={{ fontSize: "12px" }}>Registros disponíveis desde {data.retained_since}.</p>}
      {data?.latest_collection && <p role="status" style={{ fontSize: "12px" }}>
        Última consulta: {data.latest_collection.received_at_display} · {states[data.latest_collection.status] || data.latest_collection.status} · {data.latest_collection.status === "UNAVAILABLE" ? "Quantidade indisponível" : `${data.latest_collection.source_count} ativos recebidos`}
      </p>}
      <form onSubmit={e => { e.preventDefault(); setOffset(0); setSymbol(search.trim()); reload(); }} style={{ display: "flex", gap: "8px", margin: "16px 0" }}>
        <input aria-label="Filtrar histórico por ativo" className="input" placeholder="Buscar ativo, ex.: NEAR" value={search} onChange={e => setSearch(e.target.value)} />
        <button className="btn btn-secondary" disabled={busy}>Buscar</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {busy ? <p role="status">Carregando histórico…</p> : data && <>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", textAlign: "left", fontSize: "13px", borderCollapse: "collapse" }}>
            <thead><tr>{["Ativo", "Horário da API", "Coleta no Scalpyn", "Conferência no pool"].map(label => <th key={label} style={{ padding: "10px" }}>{label}</th>)}</tr></thead>
            <tbody>{data.items.map(item => <tr key={item.id} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "10px" }}>{item.symbol}</td>
              <td style={{ padding: "10px", whiteSpace: "nowrap" }}>{item.source_updated_at_display || "Não informado"}</td>
              <td style={{ padding: "10px", whiteSpace: "nowrap" }}>{item.received_at_display}</td>
              <td style={{ padding: "10px" }}>{item.pool_result === "PRESENT" ? `Presente no ${poolName}` : outcomes[item.pool_result] || item.pool_result}</td>
            </tr>)}</tbody>
          </table>
        </div>
        {!data.items.length && <p>Nenhum ativo registrado nesta busca. A captura começa após a ativação deste histórico; consultas vazias não geram linhas de ativos.</p>}
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginTop: "12px" }}>
          <button className="btn btn-secondary" disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 50))}>Anterior</button>
          <span>{data.total} registros</span>
          <button className="btn btn-secondary" disabled={offset + 50 >= data.total} onClick={() => setOffset(n => n + 50)}>Próxima</button>
        </div>
      </>}
    </div>
  </section>;
}
