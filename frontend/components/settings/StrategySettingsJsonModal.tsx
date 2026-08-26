"use client";

import { useRef, useState } from "react";
import { AlertCircle, ArrowLeft, CheckCircle2, FileJson, RefreshCw, Upload, X } from "lucide-react";

import {
  JsonObject,
  StrategySettingsDiff,
  StrategySettingsValidation,
  parseStrategySettingsJson,
} from "@/lib/strategySettings";

interface Props {
  template: string;
  sourceHash: string;
  onClose: () => void;
  onValidate: (payload: JsonObject) => Promise<StrategySettingsValidation>;
  onApply: (validation: StrategySettingsValidation) => Promise<void>;
}

function DiffRow({ change }: { change: StrategySettingsDiff }) {
  const render = (value: unknown) => value === undefined ? "—" : JSON.stringify(value);
  return (
    <div className="grid gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 md:grid-cols-[minmax(180px,.8fr)_1fr_24px_1fr] md:items-center">
      <code className="break-all text-[11px] text-[var(--text-primary)]">{change.path}</code>
      <code className="break-all text-[11px] text-red-400">{render(change.before)}</code>
      <span className="hidden text-center text-[var(--text-tertiary)] md:block">→</span>
      <code className="break-all text-[11px] text-emerald-400">{render(change.after)}</code>
    </div>
  );
}

export function StrategySettingsJsonModal({ template, sourceHash, onClose, onValidate, onApply }: Props) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [text, setText] = useState(template);
  const [validation, setValidation] = useState<StrategySettingsValidation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".json") || file.size > 1024 * 1024) {
      setError("Selecione um arquivo .json de até 1 MB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => { setText(String(reader.result ?? "")); setError(null); };
    reader.onerror = () => setError("Não foi possível ler o arquivo.");
    reader.readAsText(file);
  };

  const validate = async () => {
    setBusy(true); setError(null);
    try {
      const result = await onValidate(parseStrategySettingsJson(text));
      setValidation(result); setStep(2);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };

  const apply = async () => {
    if (!validation) return;
    setBusy(true); setError(null);
    try {
      await onApply(validation); setStep(3);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 sm:p-6" role="dialog" aria-modal="true">
      <div className="flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-[var(--border-default)] bg-[var(--bg-base)] shadow-2xl">
        <header className="flex items-start justify-between border-b border-[var(--border-subtle)] px-5 py-4">
          <div>
            <div className="flex items-center gap-2"><FileJson className="h-5 w-5 text-violet-400" /><h2 className="text-lg font-semibold">Importar configurações</h2></div>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">1. Carregar e editar · 2. Validar e revisar · 3. Confirmar</p>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Fechar"><X className="h-4 w-4" /></button>
        </header>

        <div className="flex gap-2 border-b border-[var(--border-subtle)] px-5 py-3">
          {[1, 2, 3].map((item) => <span key={item} className={`rounded-full px-3 py-1 text-[11px] font-semibold ${step === item ? "bg-violet-500/20 text-violet-300" : "bg-[var(--bg-elevated)] text-[var(--text-tertiary)]"}`}>Etapa {item}</span>)}
        </div>

        <main className="min-h-0 flex-1 overflow-auto">
          {step === 1 && (
            <div className="grid lg:grid-cols-[1fr_420px]">
              <section className="space-y-3 p-5">
                <div className="flex items-center justify-between"><label className="label">JSON completo ou parcial</label><button className="btn btn-secondary text-xs" onClick={() => fileRef.current?.click()}><Upload className="mr-2 h-3.5 w-3.5" />Selecionar arquivo</button></div>
                <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={handleFile} />
                <textarea value={text} onChange={(event) => setText(event.target.value)} className="input min-h-[520px] w-full resize-y font-mono text-[11px] leading-relaxed" spellCheck={false} />
              </section>
              <aside className="border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)] p-5 lg:border-l lg:border-t-0">
                <h3 className="text-sm font-semibold">Estrutura esperada</h3>
                <p className="my-2 text-xs leading-relaxed text-[var(--text-secondary)]">Campos omitidos permanecem inalterados. Campos desconhecidos, tipos inválidos e contratos incompatíveis são rejeitados pelo servidor.</p>
                <pre className="max-h-[540px] overflow-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-base)] p-3 text-[10px] leading-relaxed text-[var(--text-secondary)]">{template}</pre>
              </aside>
            </div>
          )}

          {step === 2 && validation && (
            <section className="space-y-4 p-5">
              <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/8 p-4"><div className="flex items-center gap-2 text-sm font-semibold text-emerald-300"><CheckCircle2 className="h-4 w-4" />JSON válido</div><p className="mt-1 text-xs text-[var(--text-secondary)]">Hash-base {sourceHash.slice(0, 12)}… · {validation.diff.length} alteração(ões)</p></div>
              {validation.diff.length ? validation.diff.map((change) => <DiffRow key={change.path} change={change} />) : <p className="rounded-xl border border-[var(--border-subtle)] p-6 text-center text-sm text-[var(--text-secondary)]">O arquivo é idêntico à configuração salva.</p>}
            </section>
          )}

          {step === 3 && (
            <section className="flex min-h-[360px] flex-col items-center justify-center p-8 text-center"><CheckCircle2 className="h-12 w-12 text-emerald-400" /><h3 className="mt-4 text-lg font-semibold">Configuração aplicada</h3><p className="mt-2 max-w-md text-sm text-[var(--text-secondary)]">A leitura pós-gravação foi confirmada. A mudança vale somente para novos trades.</p></section>
          )}
          {error && <div className="mx-5 mb-5 flex gap-2 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-300"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div>}
        </main>

        <footer className="flex items-center justify-between border-t border-[var(--border-subtle)] px-5 py-4">
          <button className="btn btn-secondary" onClick={step === 2 ? () => setStep(1) : onClose}>{step === 2 && <ArrowLeft className="mr-2 h-4 w-4" />}{step === 2 ? "Voltar" : step === 3 ? "Fechar" : "Cancelar"}</button>
          {step === 1 && <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={validate}>{busy && <RefreshCw className="mr-2 h-4 w-4 animate-spin" />}Validar JSON</button>}
          {step === 2 && <button className="btn btn-primary" disabled={busy || validation?.diff.length === 0} onClick={apply}>{busy && <RefreshCw className="mr-2 h-4 w-4 animate-spin" />}Confirmar aplicação</button>}
        </footer>
      </div>
    </div>
  );
}
