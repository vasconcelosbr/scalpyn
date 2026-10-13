"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiPut } from "@/lib/api";

type Config = Record<string, number | boolean | string>;
type Run = { run_id: string; started_at: string; status: string; summary: { deleted?: number; mode?: string; error_type?: string } };
type State = { config: Config; schema: { properties: Record<string, { type?: string; minimum?: number; maximum?: number }> }; runs: Run[]; archives: { table_name: string; deleted_rows: number; archive_bytes: number }[] };
const labels: Record<string, string> = {
  cadence_seconds: "Intervalo entre execuções (segundos)", batch_rows: "Registros por lote",
  max_batches_per_run: "Máximo de lotes por execução", statement_timeout_ms: "Tempo máximo de consulta (ms)",
  lock_timeout_ms: "Espera máxima por bloqueio (ms)", max_runtime_seconds: "Duração máxima da execução (segundos)",
  telemetry_retention_days: "Retenção da telemetria (dias)", completed_label_queue_retention_days: "Retenção da fila concluída (dias)",
  fresh_sample_rows: "Amostra de evidências recentes", max_wal_bytes_per_run: "Limite de WAL por execução (bytes)",
};

export default function StorageSettings() {
  const [state, setState] = useState<State | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try { const data = await apiGet<State>("/storage-retention/status"); setState(data); setDraft(data.config); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Falha ao carregar"); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function action(preview: boolean) {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      await apiPut("/config/storage_retention", preview ? { ...draft, mode: "dry_run" } : draft);
      if (preview) await apiPost("/storage-retention/preview");
      await refresh(); setMessage(preview ? "Simulação enviada. Atualize para consultar o resultado." : "Configuração salva. O agendador respeita o intervalo configurado.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Falha ao salvar"); }
    finally { setBusy(false); }
  }
  return <div className="space-y-6 max-w-5xl">
    <div><h1 className="text-2xl font-bold">Retenção de dados</h1><p className="text-sm text-[var(--text-secondary)] mt-2">Expurgo de telemetria normal antiga e tarefas de rotulagem concluídas. Shadow, velas, observações, labels e modelos permanecem preservados.</p></div>
    {message && <p role="status" className="card p-4 text-sm">{message}</p>}
    {draft && state && <>
      <section className="card p-5 space-y-5">
        <label className="flex gap-3 items-center"><input type="checkbox" checked={draft.enabled === true} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /> Agendamento ativo</label>
        <label className="block">Modo<select className="input mt-2" value={String(draft.mode)} onChange={e => setDraft({ ...draft, mode: e.target.value })}><option value="dry_run">Simulação</option><option value="execute">Expurgo com arquivo recuperável</option></select></label>
        <p className="text-xs text-[var(--text-secondary)]">Uma simulação concluída da mesma política é necessária antes do expurgo. Cada lote guarda os registros completos comprimidos, com checksum e restauração verificados. Exclusões não garantem redução imediata do espaço físico.</p>
        <div className="grid gap-4 md:grid-cols-2">{Object.entries(labels).map(([key, label]) => <label className="block text-sm" key={key}>{label}<input className="input mt-2" type="number" step="1" min={state.schema.properties[key]?.minimum} max={state.schema.properties[key]?.maximum} value={Number(draft[key])} onChange={e => setDraft({ ...draft, [key]: Number(e.target.value) })} /></label>)}</div>
        <div className="flex flex-wrap gap-3"><button className="btn btn-primary" disabled={busy} onClick={() => void action(false)}>Salvar</button><button className="btn btn-secondary" disabled={busy || !draft.enabled} onClick={() => void action(true)}>Salvar e simular</button><button className="btn btn-secondary" disabled={busy} onClick={() => void refresh()}>Atualizar</button></div>
      </section>
      <section className="card p-5 overflow-auto"><h2 className="font-semibold mb-3">Arquivos recuperáveis</h2><table className="w-full text-sm text-left"><thead><tr><th>Tabela</th><th>Registros removidos</th><th>Arquivo (bytes)</th></tr></thead><tbody>{state.archives.map(a => <tr key={a.table_name}><td>{a.table_name}</td><td>{a.deleted_rows}</td><td>{a.archive_bytes}</td></tr>)}</tbody></table></section>
      <section className="card p-5 overflow-auto"><h2 className="font-semibold mb-3">Execuções recentes</h2>{!state.runs.length && <p>Ainda não há execuções.</p>}<table className="w-full text-sm text-left"><thead><tr><th>Início</th><th>Modo</th><th>Status</th><th>Removidos</th></tr></thead><tbody>{state.runs.map(r => <tr key={r.run_id}><td>{new Date(r.started_at).toLocaleString("pt-BR")}</td><td>{r.summary.mode ?? "—"}</td><td>{r.status}{r.summary.error_type && `: ${r.summary.error_type}`}</td><td>{r.summary.deleted ?? "—"}</td></tr>)}</tbody></table></section>
    </>}
  </div>;
}
