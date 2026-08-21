"use client";

import {
  AlertTriangle, Archive, BookOpen, FileText, History, Loader2, Plus,
  RotateCcw, Save, Search, Upload, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import {
  MAX_ANALYSIS_PROMPT_CHARACTERS,
  MAX_ANALYSIS_PROMPT_FILE_BYTES,
  type AnalysisPrompt,
  type AnalysisPromptListResponse,
} from "@/lib/analysis-prompts";

type EditorMode = "create" | "version";

type PromptForm = {
  name: string;
  description: string;
  content_markdown: string;
  source_type: "UPLOAD_MD" | "PASTE";
  source_filename: string | null;
};

const EMPTY_FORM: PromptForm = {
  name: "",
  description: "",
  content_markdown: "",
  source_type: "PASTE",
  source_filename: null,
};

function when(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

export function AnalysisPromptLibrary() {
  const [showArchived, setShowArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AnalysisPrompt | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null);
  const [form, setForm] = useState<PromptForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const listPath = `/ai/modules/analysis-prompts${showArchived ? "?include_archived=true" : ""}`;
  const { data, error: listError, isLoading, mutate } = useSWR<AnalysisPromptListResponse>(
    listPath,
    (path: string) => apiGet<AnalysisPromptListResponse>(path),
    { revalidateOnFocus: false },
  );
  const prompts = useMemo(() => data?.items ?? [], [data?.items]);
  const canManage = Boolean(data?.can_manage);
  const filteredPrompts = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("pt-BR");
    if (!term) return prompts;
    return prompts.filter((prompt) => (
      `${prompt.name} ${prompt.current_version.description ?? ""}`.toLocaleLowerCase("pt-BR").includes(term)
    ));
  }, [prompts, search]);

  useEffect(() => {
    if (!selectedId) return;
    if (!prompts.some((prompt) => prompt.id === selectedId)) {
      setSelectedId(null);
      setDetail(null);
    }
  }, [prompts, selectedId]);

  async function loadDetail(promptId: string) {
    setSelectedId(promptId);
    setDetailLoading(true);
    setError(null);
    try {
      setDetail(await apiGet<AnalysisPrompt>(`/ai/modules/analysis-prompts/${promptId}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao carregar o prompt.");
    } finally {
      setDetailLoading(false);
    }
  }

  function openCreate() {
    setForm(EMPTY_FORM);
    setEditorMode("create");
    setError(null);
  }

  function openVersion() {
    if (!detail?.current_version.content_markdown) return;
    setForm({
      name: detail.current_version.name,
      description: detail.current_version.description ?? "",
      content_markdown: detail.current_version.content_markdown,
      source_type: "PASTE",
      source_filename: null,
    });
    setEditorMode("version");
    setError(null);
  }

  async function handleFile(file: File) {
    setError(null);
    if (!file.name.toLocaleLowerCase("pt-BR").endsWith(".md")) {
      setError("Selecione um arquivo com extensão .md.");
      return;
    }
    if (file.size > MAX_ANALYSIS_PROMPT_FILE_BYTES) {
      setError("O arquivo excede o limite de 256 KiB.");
      return;
    }
    const content = (await file.text()).replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
    if (content.length > MAX_ANALYSIS_PROMPT_CHARACTERS) {
      setError("O conteúdo excede 100.000 caracteres.");
      return;
    }
    setForm((current) => ({
      ...current,
      content_markdown: content,
      source_type: "UPLOAD_MD",
      source_filename: file.name,
      name: current.name || file.name.replace(/\.md$/i, ""),
    }));
  }

  async function save() {
    if (!form.name.trim() || !form.content_markdown.trim()) {
      setError("Informe o nome e o conteúdo Markdown.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        content_markdown: form.content_markdown,
        source_type: form.source_type,
        source_filename: form.source_type === "UPLOAD_MD" ? form.source_filename : null,
      };
      const saved = editorMode === "version" && detail
        ? await apiPost<AnalysisPrompt>(`/ai/modules/analysis-prompts/${detail.id}/versions`, payload)
        : await apiPost<AnalysisPrompt>("/ai/modules/analysis-prompts", payload);
      setEditorMode(null);
      setDetail(saved);
      setSelectedId(saved.id);
      await mutate();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível salvar o prompt.");
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(prompt: AnalysisPrompt) {
    const nextStatus = prompt.status === "ACTIVE" ? "ARCHIVED" : "ACTIVE";
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPatch<AnalysisPrompt>(
        `/ai/modules/analysis-prompts/${prompt.id}/status`,
        { status: nextStatus },
      );
      setDetail(updated);
      await mutate();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível alterar o status.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="prompts" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90 p-5 backdrop-blur">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><BookOpen size={16} className="text-cyan-300" /> Biblioteca de prompts</div>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-[var(--text-muted)]">Prompts Markdown compartilhados e versionados. Uma Intelligence Run sempre congela a versão e o hash selecionados.</p>
        </div>
        {canManage && <button type="button" onClick={openCreate} className="inline-flex items-center gap-2 rounded-lg bg-cyan-300 px-3 py-2 text-xs font-semibold text-cyan-950 hover:bg-cyan-200"><Plus size={14} /> Novo prompt</button>}
      </div>

      {(error || listError) && <div className="flex items-center gap-2 rounded-xl border border-rose-400/25 bg-rose-400/10 px-4 py-3 text-xs text-rose-200"><AlertTriangle size={14} /> {error ?? "Falha ao carregar a biblioteca."}</div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
        <div className="overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90">
          <div className="space-y-3 border-b border-[var(--border-subtle)] p-4">
            <label className="relative block">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar por nome ou descrição" className="w-full rounded-lg border border-[var(--border-subtle)] bg-black/20 py-2.5 pl-9 pr-3 text-xs outline-none focus:border-cyan-400/40" />
            </label>
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-[10px] text-[var(--text-muted)]">{filteredPrompts.length} prompts</span>
              {canManage && <label className="flex items-center gap-2 text-[10px] text-[var(--text-muted)]"><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} /> Mostrar arquivados</label>}
            </div>
          </div>
          <div className="max-h-[68vh] space-y-2 overflow-y-auto p-2">
            {isLoading && <div className="flex items-center gap-2 p-4 text-xs text-[var(--text-muted)]"><Loader2 size={14} className="animate-spin" /> Carregando…</div>}
            {!isLoading && filteredPrompts.length === 0 && <p className="p-4 text-xs text-[var(--text-muted)]">Nenhum prompt encontrado.</p>}
            {filteredPrompts.map((prompt) => (
              <button key={prompt.id} type="button" onClick={() => void loadDetail(prompt.id)} className={`w-full rounded-xl border p-3 text-left transition ${selectedId === prompt.id ? "border-cyan-400/40 bg-cyan-400/[.08]" : "border-transparent hover:border-[var(--border-subtle)] hover:bg-white/[.02]"}`}>
                <div className="flex items-start justify-between gap-3">
                  <div><p className="text-sm font-medium">{prompt.name}</p><p className="mt-1 line-clamp-2 text-[11px] leading-4 text-[var(--text-muted)]">{prompt.current_version.description || "Sem descrição"}</p></div>
                  <span className={`rounded-md border px-2 py-1 font-mono text-[9px] ${prompt.status === "ACTIVE" ? "border-emerald-400/25 text-emerald-300" : "border-slate-500/30 text-slate-400"}`}>{prompt.status}</span>
                </div>
                <div className="mt-3 flex items-center justify-between font-mono text-[9px] text-[var(--text-muted)]"><span>v{prompt.current_version.version_number}</span><span>{when(prompt.updated_at)}</span></div>
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-[420px] overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90">
          {detailLoading && <div className="flex h-80 items-center justify-center gap-2 text-sm text-[var(--text-muted)]"><Loader2 size={16} className="animate-spin" /> Carregando prompt…</div>}
          {!detailLoading && !detail && <div className="flex h-80 flex-col items-center justify-center px-6 text-center text-[var(--text-muted)]"><FileText size={30} className="mb-3 text-cyan-300/50" /><p className="text-sm">Selecione um prompt para ver conteúdo, hash e histórico.</p></div>}
          {!detailLoading && detail && (
            <div>
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--border-subtle)] p-5">
                <div><p className="text-lg font-semibold">{detail.current_version.name}</p><p className="mt-1 text-xs text-[var(--text-muted)]">{detail.current_version.description || "Sem descrição"}</p></div>
                {canManage && <div className="flex gap-2">{detail.status === "ACTIVE" && <button type="button" onClick={openVersion} className="inline-flex items-center gap-2 rounded-lg border border-cyan-400/25 px-3 py-2 text-xs text-cyan-200"><Plus size={13} /> Nova versão</button>}<button type="button" disabled={busy} onClick={() => void changeStatus(detail)} className="inline-flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-xs text-[var(--text-muted)]">{detail.status === "ACTIVE" ? <><Archive size={13} /> Arquivar</> : <><RotateCcw size={13} /> Reativar</>}</button></div>}
              </div>
              <div className="space-y-5 p-5">
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-xl border border-[var(--border-subtle)] p-3"><p className="text-[9px] uppercase text-[var(--text-muted)]">Versão</p><p className="mt-1 font-mono text-xs">v{detail.current_version.version_number}</p></div>
                  <div className="rounded-xl border border-[var(--border-subtle)] p-3"><p className="text-[9px] uppercase text-[var(--text-muted)]">Origem</p><p className="mt-1 truncate font-mono text-xs">{detail.current_version.source_filename || detail.current_version.source_type}</p></div>
                  <div className="rounded-xl border border-[var(--border-subtle)] p-3"><p className="text-[9px] uppercase text-[var(--text-muted)]">Autor</p><p className="mt-1 truncate text-xs">{detail.current_version.created_by_name}</p></div>
                </div>
                <div><p className="mb-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">SHA-256</p><p className="break-all rounded-lg bg-black/20 p-3 font-mono text-[10px] text-cyan-200">{detail.current_version.content_hash}</p></div>
                <div><p className="mb-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Prévia textual segura</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-xl border border-[var(--border-subtle)] bg-black/20 p-4 text-xs leading-5 text-[var(--text-primary)]">{detail.current_version.content_markdown}</pre></div>
                <div><p className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]"><History size={12} /> Histórico imutável</p><div className="space-y-2">{detail.versions?.map((version) => <div key={version.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-[11px]"><span className="font-mono text-cyan-200">v{version.version_number}</span><span>{version.name}</span><span className="text-[var(--text-muted)]">{version.created_by_name} · {when(version.created_at)}</span><span className="max-w-40 truncate font-mono text-[9px] text-[var(--text-muted)]">{version.content_hash}</span></div>)}</div></div>
              </div>
            </div>
          )}
        </div>
      </div>

      {editorMode && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={editorMode === "create" ? "Novo prompt" : "Nova versão do prompt"}>
          <div className="flex max-h-[calc(100vh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-cyan-400/20 bg-[#0A0E16] shadow-2xl">
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4"><div><p className="font-semibold">{editorMode === "create" ? "Novo prompt" : "Nova versão imutável"}</p><p className="mt-1 text-xs text-slate-400">Cole Markdown ou carregue um arquivo .md UTF-8.</p></div><button type="button" onClick={() => setEditorMode(null)} className="p-2 text-slate-500 hover:text-slate-200"><X size={17} /></button></div>
            <div className="space-y-4 overflow-y-auto p-5">
              <label className="block text-xs text-slate-300">Nome<input value={form.name} maxLength={160} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} className="mt-2 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2.5 text-sm outline-none focus:border-cyan-400/40" /></label>
              <label className="block text-xs text-slate-300">Descrição<textarea value={form.description} maxLength={1_000} rows={2} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} className="mt-2 w-full resize-y rounded-lg border border-white/10 bg-black/20 px-3 py-2.5 text-sm outline-none focus:border-cyan-400/40" /></label>
              <div className="flex flex-wrap items-center gap-2"><button type="button" onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-2 rounded-lg border border-cyan-400/25 px-3 py-2 text-xs text-cyan-200"><Upload size={14} /> Carregar .MD</button><input ref={fileRef} type="file" accept=".md,text/markdown,text/plain" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void handleFile(file); event.currentTarget.value = ""; }} /><span className="text-[10px] text-slate-500">{form.source_filename || "ou cole diretamente abaixo"}</span></div>
              <label className="block text-xs text-slate-300">Markdown<textarea value={form.content_markdown} maxLength={MAX_ANALYSIS_PROMPT_CHARACTERS} rows={18} onChange={(event) => setForm((current) => ({ ...current, content_markdown: event.target.value, source_type: "PASTE", source_filename: null }))} className="mt-2 w-full resize-y rounded-lg border border-white/10 bg-black/20 px-3 py-3 font-mono text-xs leading-5 outline-none focus:border-cyan-400/40" /><span className="mt-1 block text-right font-mono text-[9px] text-slate-500">{form.content_markdown.length.toLocaleString("pt-BR")} / {MAX_ANALYSIS_PROMPT_CHARACTERS.toLocaleString("pt-BR")}</span></label>
              {error && <p className="rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-200">{error}</p>}
            </div>
            <div className="flex justify-end gap-2 border-t border-white/10 px-5 py-4"><button type="button" onClick={() => setEditorMode(null)} className="px-3 py-2 text-sm text-slate-400">Cancelar</button><button type="button" disabled={busy} onClick={() => void save()} className="inline-flex items-center gap-2 rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-cyan-950 disabled:opacity-40">{busy ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Salvar</button></div>
          </div>
        </div>
      )}
    </section>
  );
}
