"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPut } from "@/lib/api";

export default function OHLCVSettings() {
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    apiGet<{ data: Record<string, unknown> }>("/config/ohlcv_comparison")
      .then(config => setDraft(JSON.stringify(config.data, null, 2)))
      .catch(error => setMessage(error instanceof Error ? error.message : "Falha ao carregar"));
  }, []);
  async function save() {
    setBusy(true);
    try {
      await apiPut("/config/ohlcv_comparison", JSON.parse(draft));
      setMessage("Configuração salva com registro de auditoria.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Falha ao salvar");
    } finally { setBusy(false); }
  }
  return <div className="max-w-3xl space-y-5">
    <h1 className="text-2xl font-bold">Comparação de candles</h1>
    <p>Controle operacional restrito ao responsável autorizado. A comparação não altera preços, operações ou modelos.</p>
    <p className="text-sm">Informe enabled, window_seconds, max_rounds_per_run, max_shadow_rows, statement_timeout_ms, lock_timeout_ms e settlement_delay_seconds. Cada rodada processa um intervalo por timeframe. Os limites são obrigatórios; não há ativação automática.</p>
    <label className="block" htmlFor="comparison-config">Configuração JSON</label>
    <textarea id="comparison-config" className="input w-full font-mono min-h-64" value={draft} onChange={event => setDraft(event.target.value)} />
    <button className="btn btn-primary" disabled={busy || !draft} onClick={() => void save()}>Salvar configuração</button>
    {message && <p role="status">{message}</p>}
  </div>;
}
