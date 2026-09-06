"use client";

import { useEffect, useState } from "react";
import { useConfig } from "@/hooks/useConfig";
import { apiGet } from "@/lib/api";

type Values = Record<string, number | string | null>;
type Property = { type?: string; minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number; anyOf?: Property[] };
type Meta = { schema: { properties: Record<string, Property> }; missing_parameters: string[]; approved: boolean; validation_status: string; pre_tp: unknown };
const groups: [string, [string, string][]][] = [
  ["Fluxo e continuação", [
    ["flow_window_seconds", "Janela de fluxo (s)"], ["cvd_window_seconds", "Janela da inclinação do CVD (s)"],
    ["continuation_taker_min", "Taker Ratio mínimo (compra / total)"], ["continuation_delta_min", "Delta normalizado mínimo"],
    ["continuation_cvd_min", "Inclinação normalizada mínima do CVD"], ["continuation_price_min_pct", "Avanço mínimo do preço (%)"]]],
  ["Enfraquecimento e confirmação", [
    ["weakening_taker_max", "Taker Ratio máximo para enfraquecimento"], ["weakening_delta_max", "Delta normalizado máximo"],
    ["weakening_cvd_max", "Inclinação normalizada máxima do CVD"], ["confirmation_seconds", "Persistência necessária (s)"],
    ["alignment_seconds", "Atraso máximo entre candle e evidências (s)"], ["pivot_left", "Candles anteriores ao fundo"], ["pivot_right", "Candles posteriores para confirmar fundo"]]],
  ["Pisos progressivos", [
    ["initial_buffer_pct", "Folga inicial abaixo do TP (p.p.)"], ["step_trigger_pct", "Avanço para cada degrau (p.p.)"],
    ["step_floor_pct", "Elevação do piso por degrau (p.p.)"], ["atr_period", "Período do ATR (candles)"],
    ["atr_multiplier", "Distância normal (× ATR)"], ["tight_atr_multiplier", "Distância apertada (× ATR)"]]],
  ["Qualidade dos dados", [
    ["max_age_seconds", "Idade máxima dos negócios (s)"], ["min_coverage_pct", "Cobertura mínima (%)"],
    ["max_gap_seconds", "Lacuna máxima entre negócios (s)"], ["warmup_seconds", "Coleta necessária antes da decisão (s)"]]],
  ["Operação", [["evaluation_seconds", "Intervalo mínimo entre avaliações (s)"],
    ["ui_refresh_seconds", "Atualização do portfólio (s)"], ["retention_days", "Retenção mínima das evidências (dias)"],
    ["trade_batch_size", "Trades por avaliação"], ["capture_batch_size", "Negócios por lote de captura"], ["max_candles_per_run", "Candles por trade e avaliação"],
    ["observation_horizon_seconds", "Horizonte de coleta após a entrada (s)"], ["replay_lookback_seconds", "Histórico por decisão para replay (s)"]]],
];

export function ShadowL3ExitPolicyForm() {
  const { config, updateConfig, isLoading, error } = useConfig("shadow_l3_exit_policy");
  const [form, setForm] = useState<Values>({});
  const [meta, setMeta] = useState<Meta | null>(null);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [sample, setSample] = useState({ entry: "", tp: "", peak: "", atr: "", previous: "" });
  useEffect(() => { if (config.version) setForm(config); }, [config]);
  useEffect(() => {
    apiGet<Meta>("/config/shadow_l3_exit_policy/metadata").then(setMeta).catch(() => setMeta(null));
  }, [config]);
  const missing = groups.flatMap(([, fields]) => fields).filter(([key]) => form[key] == null);
  const save = async () => {
    setSaving(true); setMessage("");
    try { await updateConfig(form); setMessage("Política salva. Novos trades usarão esta versão; os anteriores mantêm seus parâmetros."); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Não foi possível salvar a política."); }
    finally { setSaving(false); }
  };
  const entry = Number(sample.entry), tp = Number(sample.tp), peak = Number(sample.peak), atr = Number(sample.atr);
  const previewKeys = ["initial_buffer_pct", "step_trigger_pct", "step_floor_pct", "atr_multiplier"];
  let preview: number | null = null;
  if (entry > 0 && tp >= entry && peak >= tp && atr > 0 && previewKeys.every(k => typeof form[k] === "number")) {
    const initial = (tp / entry - 1) * 100 - Number(form.initial_buffer_pct);
    const steps = Math.max(0, Math.floor(((peak / entry - 1) * 100 - (tp / entry - 1) * 100) / Number(form.step_trigger_pct)));
    preview = Math.max(Number(sample.previous) || 0, entry * (1 + (initial + steps * Number(form.step_floor_pct)) / 100), peak - atr * Number(form.atr_multiplier));
  }
  return <section className="card p-6 space-y-6" aria-label="Trailing Stop — Shadow L3">
    <div className="flex justify-between gap-4 items-start">
      <div><h2 className="text-lg font-semibold">Trailing Stop — Shadow L3</h2>
        <p className="text-sm text-[var(--text-secondary)] mt-2">Continuação após o TP com pisos progressivos. Exclusivo do shadow L3; não controla ordens reais.</p></div>
      <button type="button" className="btn btn-primary" onClick={save} disabled={saving || isLoading || !meta || !!error}>Salvar política L3</button>
    </div>
    {error && <p role="alert">Não foi possível carregar a configuração. O salvamento permanece bloqueado.</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
    <label className="block text-sm">Modo de operação
      <select className="input mt-2 w-full" value={String(form.mode || "OBSERVE")} onChange={e => setForm({ ...form, mode: e.target.value })}>
        <option value="LEGACY">Legado — preservar comportamento atual</option>
        <option value="OBSERVE">Observação — calcular candidato sem alterar as saídas</option>
        <option value="APPLY" disabled={missing.length > 0}>Aplicar em novos shadows — execução imediata</option>
      </select>
    </label>
    <p className="text-sm text-[var(--text-secondary)]">{missing.length ? "Parâmetros ainda não definidos. A coleta pode prosseguir; a continuação não será autorizada." : "Parâmetros preenchidos. Selecione Aplicar e salve para executar nos novos shadows L3."}</p>
    {meta?.validation_status === "NOT_CALIBRATED" && <p className="text-sm text-[var(--text-secondary)]">Configuração sem calibração empírica concluída. A ativação é registrada no histórico de configurações.</p>}
    <details className="text-sm"><summary className="cursor-pointer">Proteção efetiva antes do TP — somente leitura</summary>
      <pre className="mt-3 overflow-auto text-xs">{meta ? JSON.stringify(meta.pre_tp, null, 2) : "Carregando configuração de origem…"}</pre>
    </details>
    {groups.map(([title, fields]) => <fieldset key={title} className="border-t border-[var(--border-default)] pt-4">
      <legend className="font-semibold px-2">{title}</legend>
      <div className="grid md:grid-cols-2 gap-4 mt-3">{fields.map(([key, label]) => {
        const prop = meta?.schema.properties[key];
        const rule = prop?.anyOf?.find(x => x.type !== "null") || prop;
        return <label key={key} className="block text-sm">{label}
          <input className="input w-full mt-1" type="number" step={rule?.type === "integer" ? "1" : "any"}
            min={rule?.minimum ?? rule?.exclusiveMinimum} max={rule?.maximum ?? rule?.exclusiveMaximum}
            placeholder="A definir após validação" value={form[key] ?? ""}
            onChange={e => setForm({ ...form, [key]: e.target.value === "" ? null : Number(e.target.value) })} />
        </label>;
      })}</div>
    </fieldset>)}
    <label className="block text-sm">Timeframe da estrutura
      <select className="input ml-3" value={String(form.structure_timeframe || "1m")} onChange={e => setForm({ ...form, structure_timeframe: e.target.value })}>
        {["1m", "5m", "15m"].map(tf => <option key={tf}>{tf}</option>)}
      </select>
    </label>
    <details><summary className="cursor-pointer text-sm">Prévia ilustrativa dos pisos</summary>
      <p className="text-xs text-[var(--text-secondary)] mt-2">Informe preços e ATR. O piso real também respeita o mínimo protegido do trade e a proteção anterior. Não representa preço garantido de execução.</p>
      <div className="grid md:grid-cols-5 gap-3 mt-3">{([['entry','Entrada'],['tp','TP'],['peak','Máxima'],['atr','ATR'],['previous','Piso anterior']] as const).map(([key,label]) =>
        <label key={key} className="text-sm">{label}<input type="number" step="any" className="input w-full" value={sample[key]} onChange={e => setSample({ ...sample, [key]: e.target.value })} /></label>)}</div>
      <p className="mt-3 text-sm">{preview === null ? "Preencha os parâmetros e os preços para calcular." : `Piso ilustrativo: ${preview.toLocaleString(undefined, { maximumFractionDigits: 8 })}`}</p>
    </details>
  </section>;
}
