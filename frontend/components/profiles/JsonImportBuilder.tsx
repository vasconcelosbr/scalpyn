"use client";

import { useState, useRef, useCallback } from "react";
import {
  ArrowLeft, Upload, FileJson, CheckCircle2, XCircle,
  AlertTriangle, Loader2, Globe, Filter, Target, ShoppingCart,
  ChevronRight, Eye, EyeOff, Pencil, Check, X,
} from "lucide-react";
import { apiPost } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────
type FunnelRole = "universe_filter" | "primary_filter" | "score_engine" | "acquisition_queue";

interface ImportProfile {
  name: string;
  description?: string;
  funnel_role?: FunnelRole;
  pipeline_label?: string;
  default_timeframe?: string;
  filters?:        { logic?: string; conditions?: any[] };
  signals?:        { logic?: string; conditions?: any[] };
  block_rules?:    { blocks?: any[] };
  entry_triggers?: { logic?: string; conditions?: any[] };
  scoring?:        { enabled?: boolean; weights?: any; thresholds?: any };
}

interface ParsedProfile {
  raw: ImportProfile;
  editedName: string;
  valid: boolean;
  validationError?: string;
}

interface ImportResult {
  index: number;
  name: string;
  status: "created" | "error";
  id?: string;
  error?: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────
const ROLE_META: Record<string, { label: string; short: string; color: string; bg: string; border: string; icon: any }> = {
  universe_filter:   { label: "Filtro de Universo", short: "POOL", color: "#8B92A5", bg: "rgba(139,146,165,0.12)", border: "rgba(139,146,165,0.25)", icon: Globe },
  primary_filter:    { label: "Filtro Primário",    short: "L1",   color: "#4F7BF7", bg: "rgba(79,123,247,0.12)",  border: "rgba(79,123,247,0.25)",  icon: Filter },
  score_engine:      { label: "Score Engine",       short: "L2",   color: "#FBBF24", bg: "rgba(251,191,36,0.12)", border: "rgba(251,191,36,0.25)",  icon: Target },
  acquisition_queue: { label: "Fila de Execução",   short: "L3",   color: "#34D399", bg: "rgba(52,211,153,0.12)", border: "rgba(52,211,153,0.25)",  icon: ShoppingCart },
};

const VALID_ROLES = new Set(Object.keys(ROLE_META));
const VALID_TF    = new Set(["1m", "3m", "5m", "15m", "1h"]);

// ── Validation ────────────────────────────────────────────────────────────────
function validateProfile(p: ImportProfile): { valid: boolean; error?: string } {
  if (!p.name?.trim()) return { valid: false, error: "'name' é obrigatório" };
  if (p.funnel_role && !VALID_ROLES.has(p.funnel_role))
    return { valid: false, error: `funnel_role inválido: "${p.funnel_role}"` };
  if (p.default_timeframe && !VALID_TF.has(p.default_timeframe))
    return { valid: false, error: `default_timeframe inválido: "${p.default_timeframe}"` };
  return { valid: true };
}

// ── Count helpers ─────────────────────────────────────────────────────────────
const countConds  = (p: ImportProfile) =>
  (p.filters?.conditions?.length ?? 0) +
  (p.signals?.conditions?.length ?? 0);
const countBlocks = (p: ImportProfile) => p.block_rules?.blocks?.length ?? 0;
const countTrigs  = (p: ImportProfile) => p.entry_triggers?.conditions?.length ?? 0;

// ── Role Badge ────────────────────────────────────────────────────────────────
function RoleBadge({ role }: { role?: string }) {
  if (!role) return <span className="text-[11px] text-[var(--text-tertiary)]">—</span>;
  const meta = ROLE_META[role];
  if (!meta) return <span className="text-[11px] text-[var(--text-tertiary)]">{role}</span>;
  const Icon = meta.icon;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "3px 8px", borderRadius: 20,
      background: meta.bg, border: `1px solid ${meta.border}`,
      fontSize: 10, fontWeight: 700, color: meta.color, fontFamily: "var(--font-mono)",
    }}>
      <Icon size={9} />{meta.short}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
interface Props {
  onClose: () => void;
}

export function JsonImportBuilder({ onClose }: Props) {
  const [stage, setStage]               = useState<"upload" | "preview" | "result">("upload");
  const [dragging, setDragging]         = useState(false);
  const [parseError, setParseError]     = useState<string | null>(null);
  const [parsed, setParsed]             = useState<ParsedProfile[]>([]);
  const [rawJson, setRawJson]           = useState<string>("");
  const [showJson, setShowJson]         = useState(false);
  const [editingIdx, setEditingIdx]     = useState<number | null>(null);
  const [editingVal, setEditingVal]     = useState("");
  const [importing, setImporting]       = useState(false);
  const [results, setResults]           = useState<ImportResult[]>([]);
  const [summary, setSummary]           = useState({ created: 0, failed: 0 });
  const fileRef = useRef<HTMLInputElement>(null);

  // ── Parse file ──────────────────────────────────────────────────────────────
  const processFile = useCallback((file: File) => {
    if (!file.name.endsWith(".json")) {
      setParseError("Arquivo deve ter extensão .json");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      setRawJson(text);
      try {
        const data = JSON.parse(text);
        const profiles: ImportProfile[] = Array.isArray(data)
          ? data
          : Array.isArray(data?.profiles)
          ? data.profiles
          : null as any;

        if (!profiles) {
          setParseError("JSON deve ser um array de profiles ou { \"profiles\": [...] }");
          return;
        }
        if (profiles.length === 0) {
          setParseError("Nenhum profile encontrado no arquivo");
          return;
        }
        if (profiles.length > 200) {
          setParseError(`Máximo 200 profiles por importação. Arquivo tem ${profiles.length}.`);
          return;
        }

        const parsedList: ParsedProfile[] = profiles.map((p) => {
          const v = validateProfile(p);
          return { raw: p, editedName: p.name?.trim() ?? "", valid: v.valid, validationError: v.error };
        });

        setParseError(null);
        setParsed(parsedList);
        setStage("preview");
      } catch (err: any) {
        setParseError(`JSON inválido: ${err.message}`);
      }
    };
    reader.readAsText(file);
  }, []);

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) processFile(file);
    e.target.value = "";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) processFile(file);
  };

  // ── Inline name edit ────────────────────────────────────────────────────────
  const startEdit = (idx: number) => {
    setEditingIdx(idx);
    setEditingVal(parsed[idx].editedName);
  };
  const commitEdit = (idx: number) => {
    const newName = editingVal.trim();
    if (!newName) return;
    setParsed((prev) =>
      prev.map((p, i) => (i === idx ? { ...p, editedName: newName } : p))
    );
    setEditingIdx(null);
  };
  const cancelEdit = () => setEditingIdx(null);

  // ── Import ──────────────────────────────────────────────────────────────────
  const handleImport = async () => {
    setImporting(true);
    try {
      const profilesPayload = parsed.map((p) => ({
        ...p.raw,
        name: p.editedName || p.raw.name,
      }));
      const res = await apiPost("/profiles/bulk-import", { profiles: profilesPayload });
      setResults(res.results ?? []);
      setSummary({ created: res.created ?? 0, failed: res.failed ?? 0 });
      setStage("result");
    } catch (err: any) {
      alert(`Erro na importação: ${err.message}`);
    } finally {
      setImporting(false);
    }
  };

  const validCount   = parsed.filter((p) => p.valid).length;
  const invalidCount = parsed.length - validCount;

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={onClose}
          className="p-2 hover:bg-[var(--bg-tertiary)] rounded-lg transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            Importar Profiles via JSON
          </h1>
          <p className="text-[var(--text-secondary)] mt-0.5 text-[13px]">
            {stage === "upload"  && "Faça upload do arquivo .json com os profiles a criar"}
            {stage === "preview" && `${parsed.length} profiles encontrados — revise antes de importar`}
            {stage === "result"  && `Importação concluída: ${summary.created} criados · ${summary.failed} com erro`}
          </p>
        </div>

        {/* Pipeline breadcrumb */}
        <div className="ml-auto flex items-center gap-2 text-[11px] opacity-50">
          {Object.entries(ROLE_META).map(([key, m], i, arr) => {
            const Icon = m.icon;
            return (
              <div key={key} className="flex items-center gap-2">
                <span className="flex items-center gap-1.5 font-mono font-bold" style={{ color: m.color }}>
                  <Icon size={11} />{m.short}
                </span>
                {i < arr.length - 1 && <ChevronRight className="w-3 h-3 text-[var(--text-tertiary)]" />}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── STAGE: UPLOAD ── */}
      {stage === "upload" && (
        <div className="max-w-2xl mx-auto mt-8 space-y-6">
          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            className={`relative border-2 border-dashed rounded-2xl p-16 flex flex-col items-center justify-center gap-4 cursor-pointer transition-all ${
              dragging
                ? "border-[var(--accent-primary)] bg-[var(--accent-primary)]/5 scale-[1.01]"
                : "border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 hover:bg-[var(--bg-secondary)]"
            }`}
          >
            <div className={`w-16 h-16 rounded-2xl flex items-center justify-center transition-colors ${
              dragging ? "bg-[var(--accent-primary)]/15" : "bg-[var(--bg-tertiary)]"
            }`}>
              <FileJson className={`w-8 h-8 ${dragging ? "text-[var(--accent-primary)]" : "text-[var(--text-tertiary)]"}`} />
            </div>
            <div className="text-center">
              <p className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">
                {dragging ? "Solte o arquivo aqui" : "Arraste o arquivo .json ou clique para selecionar"}
              </p>
              <p className="text-[13px] text-[var(--text-secondary)]">
                Array de profiles ou <code className="font-mono text-[var(--accent-primary)]">{"{ \"profiles\": [...] }"}</code> · máx. 200 profiles
              </p>
            </div>
            <button className="btn btn-secondary pointer-events-none">
              <Upload className="w-4 h-4 mr-2" />
              Selecionar arquivo
            </button>
            <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={handleFileInput} />
          </div>

          {parseError && (
            <div className="flex items-start gap-3 p-4 rounded-xl bg-red-500/8 border border-red-500/20 text-red-400 text-[13px]">
              <XCircle className="w-5 h-5 shrink-0 mt-0.5" />
              <span>{parseError}</span>
            </div>
          )}

          {/* Schema reference card */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl p-5">
            <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-3 flex items-center gap-2">
              <FileJson className="w-4 h-4 text-[var(--text-tertiary)]" />
              Estrutura esperada
            </h3>
            <pre className="text-[11px] text-[var(--text-secondary)] font-mono overflow-x-auto leading-relaxed">{`{
  "profiles": [
    {
      "name": "L3_TREND_FORTE_V1",          // obrigatório
      "funnel_role": "acquisition_queue",    // universe_filter | primary_filter
      "default_timeframe": "5m",             //   score_engine | acquisition_queue
      "filters":        { "logic": "AND", "conditions": [...] },
      "signals":        { "logic": "AND", "conditions": [...] },
      "block_rules":    { "blocks": [...] },
      "entry_triggers": { "logic": "AND", "conditions": [...] },
      "scoring": {
        "weights": { "signal":25, "momentum":25, "liquidity":25, "market_structure":25 },
        "thresholds": { "buy":65, "strong_buy":80, "neutral":40 }
      }
    }
  ]
}`}</pre>
          </div>
        </div>
      )}

      {/* ── STAGE: PREVIEW ── */}
      {stage === "preview" && (
        <div className="space-y-4">
          {/* Summary bar */}
          <div className="flex items-center gap-4 p-4 bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl">
            <div className="flex items-center gap-2 text-[13px]">
              <CheckCircle2 className="w-4 h-4 text-[var(--color-profit)]" />
              <span className="text-[var(--text-primary)] font-semibold">{validCount}</span>
              <span className="text-[var(--text-secondary)]">válidos</span>
            </div>
            {invalidCount > 0 && (
              <div className="flex items-center gap-2 text-[13px]">
                <XCircle className="w-4 h-4 text-[var(--color-loss)]" />
                <span className="text-[var(--text-primary)] font-semibold">{invalidCount}</span>
                <span className="text-[var(--text-secondary)]">com erro (serão ignorados)</span>
              </div>
            )}
            <div className="ml-auto flex items-center gap-3">
              <button
                className="flex items-center gap-1.5 text-[12px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                onClick={() => setShowJson((v) => !v)}
              >
                {showJson ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                {showJson ? "Ocultar JSON" : "Ver JSON"}
              </button>
              <button
                className="btn btn-secondary text-[12px] px-3 py-1.5"
                onClick={() => { setParsed([]); setStage("upload"); setParseError(null); }}
              >
                Trocar arquivo
              </button>
              <button
                className="btn btn-primary px-5"
                onClick={handleImport}
                disabled={importing || validCount === 0}
              >
                {importing
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Importando...</>
                  : <><Upload className="w-4 h-4 mr-2" />Importar {validCount} profile{validCount !== 1 ? "s" : ""}</>
                }
              </button>
            </div>
          </div>

          {/* Raw JSON viewer */}
          {showJson && (
            <div className="bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-xl p-4 max-h-64 overflow-auto">
              <pre className="text-[11px] font-mono text-[var(--text-secondary)] whitespace-pre-wrap">{rawJson}</pre>
            </div>
          )}

          {/* Profiles table */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--border-default)] bg-[var(--bg-tertiary)]">
                  <th className="text-left px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider w-8">#</th>
                  <th className="text-left px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Nome</th>
                  <th className="text-left px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Papel no Funil</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">TF</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Filters+Signals</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Blocks</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Triggers</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Status</th>
                </tr>
              </thead>
              <tbody>
                {parsed.map((p, idx) => (
                  <tr
                    key={idx}
                    className={`border-b border-[var(--border-subtle)] last:border-0 ${
                      p.valid ? "hover:bg-[var(--bg-tertiary)]/50" : "opacity-50 bg-red-500/3"
                    }`}
                  >
                    <td className="px-4 py-3 text-[var(--text-tertiary)] font-mono text-[11px]">{idx + 1}</td>

                    {/* Name — inline editable */}
                    <td className="px-4 py-3 max-w-[260px]">
                      {editingIdx === idx ? (
                        <div className="flex items-center gap-1.5">
                          <input
                            autoFocus
                            className="input h-7 text-[12px] min-w-0 flex-1"
                            value={editingVal}
                            onChange={(e) => setEditingVal(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") commitEdit(idx);
                              if (e.key === "Escape") cancelEdit();
                            }}
                          />
                          <button onClick={() => commitEdit(idx)} className="p-1 text-[var(--color-profit)] hover:bg-[var(--color-profit)]/10 rounded">
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button onClick={cancelEdit} className="p-1 text-[var(--color-loss)] hover:bg-[var(--color-loss)]/10 rounded">
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 group">
                          <span className="font-medium text-[var(--text-primary)] truncate">{p.editedName}</span>
                          {p.valid && (
                            <button
                              onClick={() => startEdit(idx)}
                              className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-[var(--text-tertiary)] hover:text-[var(--text-primary)] rounded"
                            >
                              <Pencil className="w-3 h-3" />
                            </button>
                          )}
                        </div>
                      )}
                      {p.raw.description && (
                        <p className="text-[11px] text-[var(--text-tertiary)] truncate mt-0.5">{p.raw.description}</p>
                      )}
                    </td>

                    <td className="px-4 py-3"><RoleBadge role={p.raw.funnel_role} /></td>

                    <td className="px-4 py-3 text-center font-mono text-[11px] text-[var(--text-secondary)]">
                      {p.raw.default_timeframe ?? "5m"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {countConds(p.raw) > 0
                        ? <span className="text-[12px] font-semibold text-[var(--text-primary)]">{countConds(p.raw)}</span>
                        : <span className="text-[11px] text-[var(--text-tertiary)]">—</span>}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {countBlocks(p.raw) > 0
                        ? <span className="text-[12px] font-semibold text-[var(--text-primary)]">{countBlocks(p.raw)}</span>
                        : <span className="text-[11px] text-[var(--text-tertiary)]">—</span>}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {countTrigs(p.raw) > 0
                        ? <span className="text-[12px] font-semibold text-[var(--text-primary)]">{countTrigs(p.raw)}</span>
                        : <span className="text-[11px] text-[var(--text-tertiary)]">—</span>}
                    </td>

                    <td className="px-4 py-3 text-center">
                      {p.valid ? (
                        <CheckCircle2 className="w-4 h-4 text-[var(--color-profit)] mx-auto" />
                      ) : (
                        <div className="flex flex-col items-center gap-0.5">
                          <XCircle className="w-4 h-4 text-[var(--color-loss)] mx-auto" />
                          <span className="text-[10px] text-[var(--color-loss)] max-w-[120px] text-center leading-tight">
                            {p.validationError}
                          </span>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── STAGE: RESULT ── */}
      {stage === "result" && (
        <div className="max-w-2xl mx-auto mt-4 space-y-6">
          {/* Summary */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[var(--color-profit)]/8 border border-[var(--color-profit)]/20 rounded-xl p-6 text-center">
              <CheckCircle2 className="w-8 h-8 text-[var(--color-profit)] mx-auto mb-2" />
              <div className="text-3xl font-bold text-[var(--color-profit)]">{summary.created}</div>
              <div className="text-[13px] text-[var(--text-secondary)] mt-1">profiles criados</div>
            </div>
            <div className={`${summary.failed > 0 ? "bg-red-500/8 border-red-500/20" : "bg-[var(--bg-secondary)] border-[var(--border-subtle)]"} border rounded-xl p-6 text-center`}>
              {summary.failed > 0
                ? <XCircle className="w-8 h-8 text-red-400 mx-auto mb-2" />
                : <CheckCircle2 className="w-8 h-8 text-[var(--text-tertiary)] mx-auto mb-2" />}
              <div className={`text-3xl font-bold ${summary.failed > 0 ? "text-red-400" : "text-[var(--text-tertiary)]"}`}>{summary.failed}</div>
              <div className="text-[13px] text-[var(--text-secondary)] mt-1">com erro</div>
            </div>
          </div>

          {/* Per-profile results */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
            {results.map((r, i) => (
              <div
                key={i}
                className={`flex items-center gap-3 px-4 py-3 border-b border-[var(--border-subtle)] last:border-0 ${
                  r.status === "error" ? "bg-red-500/4" : ""
                }`}
              >
                {r.status === "created"
                  ? <CheckCircle2 className="w-4 h-4 text-[var(--color-profit)] shrink-0" />
                  : <XCircle className="w-4 h-4 text-[var(--color-loss)] shrink-0" />
                }
                <span className="font-medium text-[var(--text-primary)] text-[13px] flex-1">{r.name}</span>
                {r.status === "error" && (
                  <span className="text-[12px] text-[var(--color-loss)]">{r.error}</span>
                )}
                {r.status === "created" && r.id && (
                  <span className="text-[11px] text-[var(--text-tertiary)] font-mono">{r.id.slice(0, 8)}…</span>
                )}
              </div>
            ))}
          </div>

          <div className="flex gap-3">
            <button className="btn btn-secondary flex-1" onClick={() => { setParsed([]); setRawJson(""); setStage("upload"); }}>
              <Upload className="w-4 h-4 mr-2" />
              Importar outro arquivo
            </button>
            <button className="btn btn-primary flex-1" onClick={onClose}>
              Ver profiles
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
