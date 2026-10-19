"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPut } from "@/lib/api";

function ConfigEditor({ configType, title, description }: {
  configType: string; title: string; description: string;
}) {
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    apiGet<{ data: Record<string, unknown> }>(`/config/${configType}`)
      .then(config => setDraft(JSON.stringify(config.data, null, 2)))
      .catch(error => setMessage(error instanceof Error ? error.message : "Falha ao carregar"));
  }, [configType]);
  async function save() {
    setBusy(true);
    try {
      await apiPut(`/config/${configType}`, JSON.parse(draft));
      setMessage("Configuração salva com registro de auditoria.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Falha ao salvar");
    } finally { setBusy(false); }
  }
  return <section className="max-w-3xl space-y-5">
    <h2 className="text-2xl font-bold">{title}</h2>
    <p>{description}</p>
    <p className="text-sm">Os limites são obrigatórios; não há ativação automática. Acesso restrito ao responsável autorizado.</p>
    <label className="block" htmlFor={configType}>Configuração JSON</label>
    <textarea id={configType} className="input w-full font-mono min-h-64" value={draft} onChange={event => setDraft(event.target.value)} />
    <button className="btn btn-primary" disabled={busy || !draft} onClick={() => void save()}>Salvar configuração</button>
    {message && <p role="status">{message}</p>}
  </section>;
}

export default function OHLCVSettings() {
  return <div className="space-y-10">
    <h1 className="text-2xl font-bold">Coleta de candles</h1>
    <ConfigEditor configType="ohlcv_comparison" title="Comparação de candles" description="Informe enabled, window_seconds, max_rounds_per_run, max_shadow_rows, statement_timeout_ms, lock_timeout_ms e settlement_delay_seconds. A comparação não altera preços, operações ou modelos." />
    <ConfigEditor configType="ohlcv_recovery" title="Recuperação histórica" description="Informe enabled, max_manifest_candles, page_candles, max_pages_per_run, max_attempts_per_gap, request_spacing_seconds, retry_delay_seconds, http_timeout_seconds, statement_timeout_ms, lock_timeout_ms e dispatch_ttl_seconds. Salvar não inicia a recuperação: cada intervalo exige manifesto revisado e envio explícito. Os preços recuperados mantêm a data real de chegada e não recertificam rótulos." />
  </div>;
}
