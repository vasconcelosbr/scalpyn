"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  ArrowLeft, Upload, FileJson, CheckCircle2, XCircle,
  Loader2, Globe, Filter, Target, ShoppingCart,
  ChevronRight, Eye, EyeOff, Pencil, Check, X, BookOpen, ChevronDown,
  AlertTriangle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { useConfig } from "@/hooks/useConfig";

// ── Types ─────────────────────────────────────────────────────────────────────
type FunnelRole = "universe_filter" | "primary_filter" | "score_engine" | "acquisition_queue";
type JsonObject = Record<string, unknown>;
type RulePayload = JsonObject;

interface ScoringPayload {
  enabled?: boolean;
  weights?: JsonObject;
  thresholds?: JsonObject;
  selected_rule_ids?: string[];
  rules?: RulePayload[];
}

interface ImportProfile {
  name: string;
  description?: string;
  funnel_role?: FunnelRole;
  pipeline_label?: string;
  default_timeframe?: string;
  filters?:        { logic?: string; conditions?: RulePayload[] };
  signals?:        { logic?: string; conditions?: RulePayload[] };
  block_rules?:    { blocks?: RulePayload[] };
  entry_triggers?: { logic?: string; conditions?: RulePayload[] };
  scoring?:        ScoringPayload;
}

interface ScoringAssignment {
  profile_id?: string;
  id?: string;
  profile_name?: string;
  name?: string;
  scoring?: ScoringPayload;
  selected_rule_ids?: string[];
  weights?: JsonObject;
  thresholds?: JsonObject;
  enabled?: boolean;
}

interface ImportFilePayload {
  profiles?: ImportProfile[];
  profile_scoring?: ImportProfile["scoring"];
  scoring?: ImportProfile["scoring"];
  scoring_rule_ids?: string[];
  apply_to_active_profiles?: boolean;
  update_active_profiles?: boolean;
  active_profiles_only?: boolean;
  scoring_assignments?: ScoringAssignment[];
  scoring_rules?: unknown[];
}

interface ExistingProfileRef {
  id: string;
  name: string;
  profile_role?: string | null;
  is_active?: boolean;
  selected_rule_ids: string[];
}

interface ParsedProfile {
  raw: ImportProfile;
  editedName: string;
  valid: boolean;
  validationError?: string;
}

interface ParsedImportPayload {
  profiles: ImportProfile[];
  sharedScoring?: ScoringPayload;
  applyToActiveProfiles: boolean;
  scoringAssignments: ScoringAssignment[];
}

interface ImportResult {
  index: number;
  name: string;
  status: "created" | "updated" | "error";
  id?: string;
  error?: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────
const ROLE_META: Record<string, { label: string; short: string; color: string; bg: string; border: string; icon: LucideIcon }> = {
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
const countScoreRules = (p: ImportProfile) =>
  p.scoring?.selected_rule_ids?.length ?? p.scoring?.rules?.length ?? 0;

function normalizeScoring(scoring?: ImportProfile["scoring"], fallback?: ImportProfile["scoring"]) {
  const selected = scoring?.selected_rule_ids ?? fallback?.selected_rule_ids;
  const rules = scoring?.rules ?? fallback?.rules;
  const merged = {
    ...(fallback || {}),
    ...(scoring || {}),
    ...(selected ? { selected_rule_ids: selected.map(String) } : {}),
    ...(rules ? { rules } : {}),
  };
  return Object.keys(merged).length > 0 ? merged : undefined;
}

function assignmentTarget(a: ScoringAssignment): string {
  return String(a.profile_name || a.name || a.profile_id || a.id || "");
}

function assignmentRuleIds(a: ScoringAssignment, shared?: ScoringPayload): string[] {
  const ids = a.scoring?.selected_rule_ids ?? a.selected_rule_ids ?? shared?.selected_rule_ids;
  return Array.isArray(ids) ? ids.map(String) : [];
}

function parseProfilesPayload(data: ImportFilePayload | ImportProfile[]): ParsedImportPayload {
  const profiles: ImportProfile[] | null = Array.isArray(data)
    ? data
    : Array.isArray(data?.profiles)
    ? data.profiles
    : null;

  const applyToActiveProfiles = !Array.isArray(data) && Boolean(
    data?.apply_to_active_profiles || data?.update_active_profiles || data?.active_profiles_only
  );

  const scoringAssignments: ScoringAssignment[] =
    !Array.isArray(data) && Array.isArray(data?.scoring_assignments)
      ? data.scoring_assignments
      : [];

  if (!profiles && !applyToActiveProfiles && scoringAssignments.length === 0) {
    if (!Array.isArray(data) && Array.isArray(data?.scoring_rules)) {
      throw new Error(
        'Este JSON é uma matriz de Score ("scoring_rules") — importe em Settings → Score Engine → Import JSON. '
        + 'Depois use "scoring_assignments" aqui para associar as regras aos profiles.'
      );
    }
    throw new Error('JSON deve ser um array de profiles, { "profiles": [...] } ou { "scoring_assignments": [...] }');
  }

  const sharedScoring = Array.isArray(data)
    ? undefined
    : normalizeScoring(
        data.profile_scoring || data.scoring,
        data.scoring_rule_ids ? { selected_rule_ids: data.scoring_rule_ids } : undefined
      );

  if (applyToActiveProfiles && !Array.isArray(sharedScoring?.selected_rule_ids)) {
    throw new Error('Para atualizar profiles ativos, informe "profile_scoring.selected_rule_ids": [...]');
  }

  for (const [i, assignment] of scoringAssignments.entries()) {
    if (!isRecordLike(assignment)) {
      throw new Error(`scoring_assignments[${i}] deve ser um objeto`);
    }
    if (!assignment.profile_id && !assignment.id && !assignment.profile_name && !assignment.name) {
      throw new Error(`scoring_assignments[${i}] precisa de "profile_id" ou "profile_name"`);
    }
    if (assignmentRuleIds(assignment, sharedScoring).length === 0) {
      throw new Error(
        `scoring_assignments[${i}] precisa de "selected_rule_ids" (inline ou via "profile_scoring")`
      );
    }
  }

  return {
    profiles: (profiles || []).map((profile) => ({
      ...profile,
      scoring: normalizeScoring(profile.scoring, sharedScoring) || profile.scoring,
    })),
    sharedScoring,
    applyToActiveProfiles,
    scoringAssignments,
  };
}

function isRecordLike(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

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
  const [showRef, setShowRef]           = useState(false);
  const [parseError, setParseError]     = useState<string | null>(null);
  const [parsed, setParsed]             = useState<ParsedProfile[]>([]);
  const [sharedScoring, setSharedScoring] = useState<ScoringPayload | undefined>(undefined);
  const [applyToActiveProfiles, setApplyToActiveProfiles] = useState(false);
  const [scoringAssignments, setScoringAssignments] = useState<ScoringAssignment[]>([]);
  const [existingProfiles, setExistingProfiles] = useState<ExistingProfileRef[]>([]);
  const { config: globalScoreConfig } = useConfig("score");
  const globalRules: { id: string; indicator?: string; operator?: string; points?: number; category?: string }[] =
    Array.isArray(globalScoreConfig?.scoring_rules) ? globalScoreConfig.scoring_rules : [];
  const globalRuleIds = new Set(globalRules.map((r) => String(r.id)));

  useEffect(() => {
    apiGet("/profiles")
      .then((res) => setExistingProfiles(
        ((res.profiles ?? []) as Array<Record<string, unknown>>).map((p) => {
          const config = p.config as Record<string, unknown> | undefined;
          const scoring = config?.scoring as Record<string, unknown> | undefined;
          const rawIds = scoring?.selected_rule_ids;
          return {
            id: String(p.id),
            name: String(p.name ?? ""),
            profile_role: (p.profile_role as string | null) ?? null,
            is_active: p.is_active !== false,
            selected_rule_ids: Array.isArray(rawIds) ? rawIds.map(String) : [],
          };
        })
      ))
      .catch(() => setExistingProfiles([]));
  }, []);

  const buildAssignmentsTemplate = () => JSON.stringify(
    {
      scoring_assignments: existingProfiles.map((p) => ({
        profile_id: p.id,
        profile_name: p.name,
        selected_rule_ids: p.selected_rule_ids,
      })),
    },
    null,
    2
  );
  const [rawJson, setRawJson]           = useState<string>("");
  const [showJson, setShowJson]         = useState(false);
  const [editingIdx, setEditingIdx]     = useState<number | null>(null);
  const [editingVal, setEditingVal]     = useState("");
  const [templateNotice, setTemplateNotice] = useState<string | null>(null);
  const [importing, setImporting]       = useState(false);
  const [results, setResults]           = useState<ImportResult[]>([]);
  const [summary, setSummary]           = useState({ created: 0, updated: 0, failed: 0 });
  const fileRef = useRef<HTMLInputElement>(null);

  const processJsonText = useCallback((text: string) => {
    setRawJson(text);
    try {
      const parsedPayload = parseProfilesPayload(JSON.parse(text));
      const profiles = parsedPayload.profiles;
      if (
        !parsedPayload.applyToActiveProfiles
        && profiles.length === 0
        && parsedPayload.scoringAssignments.length === 0
      ) {
        setParseError("Nenhum profile encontrado no JSON");
        return;
      }
      if (profiles.length > 200) {
        setParseError(`Máximo 200 profiles por importação. JSON tem ${profiles.length}.`);
        return;
      }
      if (parsedPayload.scoringAssignments.length > 200) {
        setParseError(`Máximo 200 scoring_assignments por importação. JSON tem ${parsedPayload.scoringAssignments.length}.`);
        return;
      }

      const parsedList: ParsedProfile[] = profiles.map((p) => {
        const v = validateProfile(p);
        return { raw: p, editedName: p.name?.trim() ?? "", valid: v.valid, validationError: v.error };
      });

      setParseError(null);
      setParsed(parsedList);
      setSharedScoring(parsedPayload.sharedScoring);
      setApplyToActiveProfiles(parsedPayload.applyToActiveProfiles);
      setScoringAssignments(parsedPayload.scoringAssignments);
      setStage("preview");
    } catch (err: unknown) {
      setParseError(`JSON inválido: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  // ── Parse file ──────────────────────────────────────────────────────────────
  const processFile = useCallback((file: File) => {
    if (!file.name.endsWith(".json")) {
      setParseError("Arquivo deve ter extensão .json");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      processJsonText(text);
    };
    reader.readAsText(file);
  }, [processJsonText]);

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
      const res = await apiPost("/profiles/bulk-import", applyToActiveProfiles
        ? {
            apply_to_active_profiles: true,
            profile_scoring: sharedScoring,
          }
        : {
            ...(profilesPayload.length > 0 ? { profiles: profilesPayload } : {}),
            ...(sharedScoring ? { profile_scoring: sharedScoring } : {}),
            ...(scoringAssignments.length > 0 ? { scoring_assignments: scoringAssignments } : {}),
          });
      setResults(res.results ?? []);
      setSummary({ created: res.created ?? 0, updated: res.updated ?? 0, failed: res.failed ?? 0 });
      setStage("result");
    } catch (err: unknown) {
      alert(`Erro na importação: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setImporting(false);
    }
  };

  const validCount   = applyToActiveProfiles ? 1 : parsed.filter((p) => p.valid).length;
  const invalidCount = applyToActiveProfiles ? 0 : parsed.length - validCount;
  const selectedScoringCount = sharedScoring?.selected_rule_ids?.length ?? 0;
  const canImport = applyToActiveProfiles
    ? Array.isArray(sharedScoring?.selected_rule_ids)
    : validCount > 0 || scoringAssignments.length > 0;

  const existingProfileById = new Map(existingProfiles.map((p) => [p.id, p]));
  const existingProfileByName = new Map(existingProfiles.map((p) => [p.name.toLowerCase(), p]));
  const resolveAssignmentProfile = (a: ScoringAssignment): ExistingProfileRef | undefined => {
    const pid = a.profile_id || a.id;
    if (pid) return existingProfileById.get(String(pid));
    const pname = a.profile_name || a.name;
    return pname ? existingProfileByName.get(String(pname).toLowerCase()) : undefined;
  };

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
            {stage === "preview" && (applyToActiveProfiles
              ? `${selectedScoringCount} regras de Scoring para aplicar aos profiles ativos`
              : [
                  parsed.length > 0 ? `${parsed.length} profiles encontrados` : null,
                  scoringAssignments.length > 0 ? `${scoringAssignments.length} associações de scoring` : null,
                ].filter(Boolean).join(" · ") + " — revise antes de importar")}
            {stage === "result"  && (applyToActiveProfiles
              ? `Atualizacao concluida: ${summary.updated} atualizados · ${summary.failed} com erro`
              : `Importação concluída: ${summary.created} criados · ${summary.updated} atualizados · ${summary.failed} com erro`)}
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

          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-[13px] font-semibold text-[var(--text-primary)] flex items-center gap-2">
                  <FileJson className="w-4 h-4 text-[var(--text-tertiary)]" />
                  Colar JSON
                </h3>
                <p className="text-[12px] text-[var(--text-secondary)] mt-1">
                  Use este campo para importar em massa sem arquivo. Com <code className="font-mono text-[var(--accent-primary)]">apply_to_active_profiles</code>, o bloco <code className="font-mono text-[var(--accent-primary)]">profile_scoring</code> substitui o Scoring de todos os profiles ativos.
                </p>
              </div>
              <button
                className="btn btn-secondary text-[12px] px-3 py-1.5 shrink-0"
                onClick={() => processJsonText(rawJson)}
                disabled={!rawJson.trim()}
              >
                <Upload className="w-3.5 h-3.5 mr-1.5" />
                Revisar JSON
              </button>
            </div>
            <textarea
              className="input min-h-[180px] w-full font-mono text-[11px] leading-relaxed resize-y"
              value={rawJson}
              onChange={(e) => setRawJson(e.target.value)}
              placeholder='{"apply_to_active_profiles":true,"profile_scoring":{"selected_rule_ids":["rule_ema_trend_ema9_gt_ema50","rule_adx_ge_25"]}}'
              spellCheck={false}
            />
          </div>

          {parseError && (
            <div className="flex items-start gap-3 p-4 rounded-xl bg-red-500/8 border border-red-500/20 text-red-400 text-[13px]">
              <XCircle className="w-5 h-5 shrink-0 mt-0.5" />
              <span>{parseError}</span>
            </div>
          )}

          {/* Schema reference card */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-4">
            <h3 className="text-[13px] font-semibold text-[var(--text-primary)] flex items-center gap-2">
              <FileJson className="w-4 h-4 text-[var(--text-tertiary)]" />
              Estrutura esperada
            </h3>
            <pre className="text-[11px] text-[var(--text-secondary)] font-mono overflow-x-auto leading-relaxed">{`{
  "apply_to_active_profiles": false,          // true = nao cria profiles; substitui scoring de todos os ativos
  "profile_scoring": {
    "enabled": true,
    "selected_rule_ids": [
      "rule_ema_trend_ema9_gt_ema50",
      "rule_adx_ge_25",
      "rule_taker_ratio_ge_055"
    ],
    "weights": { "signal": 25, "momentum": 25, "liquidity": 25, "market_structure": 25 },
    "thresholds": { "buy": 65, "strong_buy": 80, "neutral": 40 }
  },
  "profiles": [
    {
      "name": "L3_TREND_FORTE_V1",           // obrigatório
      "description": "texto livre",          // opcional
      "funnel_role": "acquisition_queue",    // universe_filter | primary_filter | score_engine | acquisition_queue
      "pipeline_label": "L3_TREND_V1",      // opcional
      "default_timeframe": "5m",             // 1m | 3m | 5m | 15m | 1h  (default: 5m)

      // ── filters / signals ─────────────────────────────────────────
      // usar "field" + "operator" + "value"  (+ "period" se suportado)
      "filters": {
        "logic": "AND",
        "conditions": [
          { "field": "volume_24h",      "operator": ">=", "value": 500000        },
          { "field": "spread_pct",      "operator": "<=", "value": 0.5           },
          { "field": "adx",             "operator": ">=", "value": 25, "period": 14, "timeframe": "5m" },
          { "field": "rsi",             "operator": "between", "min": 40, "max": 70, "period": 14 },
          { "field": "ema9_gt_ema21",   "operator": "==", "value": true          },
          { "field": "macd_signal",     "operator": "==", "value": "bullish"     },
          { "field": "psar_trend",      "operator": "==", "value": "RISING"      }
        ]
      },
      "signals": {
        "logic": "AND",
        "conditions": [
          { "field": "score",           "operator": ">=", "value": 65            },
          { "field": "taker_ratio",     "operator": ">=", "value": 0.52          },
          { "field": "volume_spike",    "operator": ">=", "value": 1.5, "period": 20 },
          { "field": "vwap_distance_pct","operator": "between", "min": -1, "max": 2, "period": 20 }
        ]
      },

      // ── block_rules ───────────────────────────────────────────────
      // usar "type" (threshold | boolean | comparison) + "indicator"
      "block_rules": {
        "blocks": [
          {
            "name": "RSI Sobrecomprado",
            "enabled": true,
            "logic": "AND",
            "reason": "RSI extremo + volume caindo",
            "timeframe": "5m",
            "conditions": [
              { "type": "threshold",  "indicator": "rsi",          "operator": ">=", "value": 75, "period": 14 },
              { "type": "threshold",  "indicator": "volume_delta", "operator": "<",  "value": 0,  "period": 20 }
            ]
          },
          {
            "name": "EMA Bearish",
            "enabled": true,
            "logic": "AND",
            "conditions": [
              { "type": "boolean",    "indicator": "ema9_gt_ema21", "operator": "is_false" },
              { "type": "comparison", "left": "price",              "operator": "<", "right": "ema50" }
            ]
          }
        ]
      },

      // ── entry_triggers ────────────────────────────────────────────
      // igual block_rules + "required" (bool) + "enabled" (bool)
      "entry_triggers": {
        "logic": "AND",
        "conditions": [
          { "type": "threshold", "indicator": "rsi",    "operator": "between", "min": 45, "max": 65,
            "period": 14, "timeframe": "5m", "required": true,  "enabled": true },
          { "type": "threshold", "indicator": "adx",    "operator": ">=",      "value": 20,
            "period": 14,                    "required": false, "enabled": true },
          { "type": "boolean",   "indicator": "di_trend","operator": "is_true",
            "required": true,  "enabled": true }
        ]
      },

      // ── scoring ───────────────────────────────────────────────────
      "scoring": {
        "selected_rule_ids": ["rule_ema_trend_ema9_gt_ema50"],  // opcional; substitui profile_scoring neste profile
        "weights":    { "signal": 25, "momentum": 25, "liquidity": 25, "market_structure": 25 },
        "thresholds": { "buy": 65, "strong_buy": 80, "neutral": 40 }
      }
    }
  ]
}`}</pre>

            {/* Toggle indicator reference */}
            <button
              className="flex items-center gap-2 text-[12px] text-[var(--accent-primary)] hover:underline font-medium"
              onClick={() => setShowRef((v) => !v)}
            >
              <BookOpen className="w-3.5 h-3.5" />
              {showRef ? "Ocultar referência de indicadores" : "Ver todos os indicadores disponíveis"}
              <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showRef ? "rotate-180" : ""}`} />
            </button>

            {showRef && (
              <div className="space-y-5 border-t border-[var(--border-subtle)] pt-4">

                {/* Condition syntax */}
                <div>
                  <p className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider mb-2">Sintaxe das condições</p>
                  <pre className="text-[11px] text-[var(--text-secondary)] font-mono leading-relaxed bg-[var(--bg-tertiary)] rounded-lg p-3 overflow-x-auto">{`// filters e signals → usar "field"
{ "field": "rsi", "operator": ">=", "value": 30, "period": 14, "timeframe": "5m" }
{ "field": "adx", "operator": "between", "min": 20, "max": 50 }
{ "field": "ema9_gt_ema21", "operator": "==", "value": true }

// block_rules (condições dentro de cada bloco) → usar "type" + "indicator"
{ "type": "threshold",  "indicator": "rsi",          "operator": "<",      "value": 75, "period": 14 }
{ "type": "boolean",    "indicator": "ema9_gt_ema21", "operator": "is_true"                           }
{ "type": "comparison", "left": "price",              "operator": ">",      "right": "ema9"           }

// entry_triggers → igual block_rules + "required" + "enabled"
{ "type": "threshold", "indicator": "rsi", "operator": "between", "min": 40, "max": 65,
  "period": 14, "timeframe": "5m", "required": true, "enabled": true }

// Operadores numéricos: >  <  >=  <=  ==  !=  between
// Operadores booleanos: is_true  is_false`}</pre>
                </div>

                {/* Indicator table */}
                <div>
                  <p className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider mb-3">Indicadores disponíveis</p>
                  <div className="grid grid-cols-1 gap-3">

                    {[
                      {
                        group: "Preço e Volume",
                        color: "#8B92A5",
                        rows: [
                          { field: "volume_24h",  label: "Volume 24h",      type: "number",  period: false, note: "" },
                          { field: "market_cap",  label: "Market Cap",      type: "number",  period: false, note: "" },
                          { field: "price",       label: "Preço",           type: "number",  period: false, note: "usado como left/right em comparison" },
                          { field: "change_24h",  label: "Variação 24h %",  type: "number",  period: false, note: "" },
                        ],
                      },
                      {
                        group: "Liquidez Real",
                        color: "#4F7BF7",
                        rows: [
                          { field: "spread_pct",          label: "Spread %",                    type: "number", period: false, note: "" },
                          { field: "orderbook_depth_usdt",label: "Profundidade Book (USDT)",    type: "number", period: false, note: "" },
                          { field: "taker_ratio",         label: "Taker Ratio (buy/(b+s), 0-1)",type: "number", period: false, note: "" },
                          { field: "volume_spike",        label: "Volume Spike",                type: "number", period: true,  note: "default period: 20" },
                          { field: "volume_delta",        label: "Volume Delta",                type: "number", period: true,  note: "default period: 20" },
                          { field: "orderbook_pressure",  label: "Orderbook Pressure",          type: "number", period: false, note: "" },
                          { field: "bid_ask_imbalance",   label: "Bid/Ask Imbalance",           type: "number", period: false, note: "" },
                          { field: "obv",                 label: "OBV",                         type: "number", period: true,  note: "default period: 20" },
                          { field: "vwap_distance_pct",   label: "VWAP Distance %",             type: "number", period: true,  note: "default period: 20" },
                        ],
                      },
                      {
                        group: "Momentum",
                        color: "#F59E0B",
                        rows: [
                          { field: "rsi",            label: "RSI",              type: "number", period: true,  note: "default period: 14" },
                          { field: "macd",           label: "MACD",             type: "number", period: true,  note: "default period: 12" },
                          { field: "macd_histogram", label: "MACD Histogram",   type: "number", period: true,  note: "default period: 12" },
                          { field: "macd_signal",    label: "MACD Signal",      type: "string", period: false, note: 'valor: "bullish" | "bearish"' },
                          { field: "stoch_k",        label: "Stochastic %K",    type: "number", period: true,  note: "default period: 14" },
                          { field: "stoch_d",        label: "Stochastic %D",    type: "number", period: true,  note: "default period: 14" },
                          { field: "zscore",         label: "Z-Score",          type: "number", period: true,  note: "default period: 20" },
                        ],
                      },
                      {
                        group: "Tendência e Estrutura",
                        color: "#34D399",
                        rows: [
                          { field: "adx",        label: "ADX",             type: "number",  period: true,  note: "default period: 14" },
                          { field: "di_plus",    label: "DI+",             type: "number",  period: true,  note: "default period: 14" },
                          { field: "di_minus",   label: "DI-",             type: "number",  period: true,  note: "default period: 14" },
                          { field: "di_trend",   label: "DI+ > DI- (Alta)",type: "boolean", period: false, note: 'value: true | false' },
                          { field: "atr",        label: "ATR",             type: "number",  period: true,  note: "default period: 14" },
                          { field: "atr_percent",label: "ATR %",           type: "number",  period: true,  note: "default period: 14" },
                          { field: "bb_width",   label: "Bollinger Width", type: "number",  period: true,  note: "default period: 20" },
                          { field: "psar_trend", label: "PSAR Trend",      type: "string",  period: false, note: 'valor: "RISING" | "FALLING"' },
                        ],
                      },
                      {
                        group: "EMA e Alinhamento",
                        color: "#A78BFA",
                        rows: [
                          { field: "ema_full_alignment", label: "EMA Full Alignment", type: "boolean", period: false, note: 'value: true | false' },
                          { field: "ema9_gt_ema21",      label: "EMA9 > EMA21",       type: "boolean", period: false, note: 'value: true | false' },
                          { field: "ema9_gt_ema50",      label: "EMA9 > EMA50",       type: "boolean", period: false, note: 'value: true | false' },
                          { field: "ema50_gt_ema200",    label: "EMA50 > EMA200",     type: "boolean", period: false, note: 'value: true | false' },
                          { field: "ema5",               label: "EMA5  (valor)",      type: "number",  period: false, note: "usar como left/right em comparison" },
                          { field: "ema9",               label: "EMA9  (valor)",      type: "number",  period: false, note: "usar como left/right em comparison" },
                          { field: "ema21",              label: "EMA21 (valor)",      type: "number",  period: false, note: "usar como left/right em comparison" },
                          { field: "ema50",              label: "EMA50 (valor)",      type: "number",  period: false, note: "usar como left/right em comparison" },
                          { field: "ema200",             label: "EMA200 (valor)",     type: "number",  period: false, note: "usar como left/right em comparison" },
                        ],
                      },
                      {
                        group: "Scores",
                        color: "#EC4899",
                        rows: [
                          { field: "score",           label: "Alpha Score",      type: "number", period: false, note: "0–100" },
                          { field: "liquidity_score", label: "Liquidity Score",  type: "number", period: false, note: "0–100" },
                          { field: "momentum_score",  label: "Momentum Score",   type: "number", period: false, note: "0–100" },
                        ],
                      },
                    ].map((grp) => (
                      <div key={grp.group} className="bg-[var(--bg-tertiary)] rounded-lg overflow-hidden">
                        <div
                          className="px-3 py-2 text-[11px] font-bold uppercase tracking-wider"
                          style={{ color: grp.color, backgroundColor: `${grp.color}14` }}
                        >
                          {grp.group}
                        </div>
                        <table className="w-full text-[11px]">
                          <thead>
                            <tr className="border-b border-[var(--border-subtle)]">
                              <th className="text-left px-3 py-1.5 text-[10px] font-semibold text-[var(--text-tertiary)] uppercase w-[200px]">field / indicator</th>
                              <th className="text-left px-3 py-1.5 text-[10px] font-semibold text-[var(--text-tertiary)] uppercase w-[180px]">Label</th>
                              <th className="text-center px-3 py-1.5 text-[10px] font-semibold text-[var(--text-tertiary)] uppercase w-20">Tipo</th>
                              <th className="text-center px-3 py-1.5 text-[10px] font-semibold text-[var(--text-tertiary)] uppercase w-16">Period</th>
                              <th className="text-left px-3 py-1.5 text-[10px] font-semibold text-[var(--text-tertiary)] uppercase">Nota</th>
                            </tr>
                          </thead>
                          <tbody>
                            {grp.rows.map((row) => (
                              <tr key={row.field} className="border-b border-[var(--border-subtle)]/50 last:border-0 hover:bg-[var(--bg-surface)]/30">
                                <td className="px-3 py-1.5 font-mono font-semibold" style={{ color: grp.color }}>{row.field}</td>
                                <td className="px-3 py-1.5 text-[var(--text-secondary)]">{row.label}</td>
                                <td className="px-3 py-1.5 text-center">
                                  <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold font-mono ${
                                    row.type === "boolean" ? "bg-purple-500/15 text-purple-400" :
                                    row.type === "string"  ? "bg-yellow-500/15 text-yellow-400" :
                                    "bg-blue-500/15 text-blue-400"
                                  }`}>
                                    {row.type}
                                  </span>
                                </td>
                                <td className="px-3 py-1.5 text-center">
                                  {row.period
                                    ? <CheckCircle2 className="w-3 h-3 text-[var(--color-profit)] mx-auto" />
                                    : <span className="text-[var(--text-tertiary)]">—</span>
                                  }
                                </td>
                                <td className="px-3 py-1.5 text-[var(--text-tertiary)] italic">{row.note}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}

                    {/* Block structure example */}
                    <div>
                      <p className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider mb-2">Estrutura de block_rules</p>
                      <pre className="text-[11px] text-[var(--text-secondary)] font-mono leading-relaxed bg-[var(--bg-tertiary)] rounded-lg p-3 overflow-x-auto">{`"block_rules": {
  "blocks": [
    {
      "name":    "Nome do Bloco",   // obrigatório
      "enabled": true,
      "logic":   "AND",            // AND | OR (entre as condições do bloco)
      "reason":  "Motivo do veto", // opcional
      "timeframe": "5m",           // opcional — timeframe compartilhado do bloco
      "conditions": [
        { "type": "threshold",  "indicator": "taker_ratio",    "operator": "<",      "value": 0.2 },
        { "type": "threshold",  "indicator": "rsi",            "operator": ">=",     "value": 75, "period": 14 },
        { "type": "boolean",    "indicator": "ema9_gt_ema21",  "operator": "is_true"              },
        { "type": "comparison", "left": "price",               "operator": ">",      "right": "ema50" }
      ]
    }
  ]
}`}</pre>
                    </div>

                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Scoring association reference card */}
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-4">
            <div>
              <h3 className="text-[13px] font-semibold text-[var(--text-primary)] flex items-center gap-2">
                <Target className="w-4 h-4 text-[#FBBF24]" />
                Estrutura esperada — Associação de Scoring (profiles existentes)
              </h3>
              <p className="text-[12px] text-[var(--text-secondary)] mt-1">
                Use <code className="font-mono text-[var(--accent-primary)]">scoring_assignments</code> para associar
                regras da matriz global à aba Scoring de profiles já existentes, por{" "}
                <code className="font-mono text-[var(--accent-primary)]">profile_id</code> ou{" "}
                <code className="font-mono text-[var(--accent-primary)]">profile_name</code>. Os IDs válidos estão
                nas tabelas abaixo — qualquer ID fora delas falha na importação.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn btn-primary text-[12px] px-3 py-1.5"
                disabled={existingProfiles.length === 0}
                onClick={() => {
                  setRawJson(buildAssignmentsTemplate());
                  setParseError(null);
                  setTemplateNotice(
                    `Modelo preenchido com ${existingProfiles.length} profiles carregado no campo "Colar JSON" acima — ajuste os selected_rule_ids de cada profile e clique em Revisar JSON.`
                  );
                }}
                data-testid="load-scoring-template"
              >
                <FileJson className="w-3.5 h-3.5 mr-1.5" />
                Carregar modelo preenchido no editor
              </button>
              <button
                className="btn btn-secondary text-[12px] px-3 py-1.5"
                disabled={existingProfiles.length === 0}
                onClick={() => {
                  navigator.clipboard.writeText(buildAssignmentsTemplate())
                    .then(() => setTemplateNotice(`Modelo com ${existingProfiles.length} profiles copiado para a área de transferência.`))
                    .catch(() => setTemplateNotice("Não foi possível copiar — use o botão de carregar no editor."));
                }}
                data-testid="copy-scoring-template"
              >
                Copiar modelo
              </button>
            </div>
            {templateNotice && (
              <div className="rounded-lg border border-[var(--accent-primary)]/25 bg-[var(--accent-primary)]/8 px-3 py-2 text-[12px] text-[var(--text-secondary)]">
                {templateNotice}
              </div>
            )}
            <p className="text-[11px] text-[var(--text-tertiary)]">
              O modelo já vem com <code className="font-mono">profile_id</code>, <code className="font-mono">profile_name</code> e os{" "}
              <code className="font-mono">selected_rule_ids</code> atuais de cada profile — não altere id/nome, apenas as regras.
              Campos opcionais por profile: <code className="font-mono">weights</code> e <code className="font-mono">thresholds</code>.
            </p>
            <pre className="text-[11px] text-[var(--text-secondary)] font-mono overflow-x-auto leading-relaxed">{`{
  "scoring_assignments": [
    {
      "profile_id": "<preenchido automaticamente>",
      "profile_name": "<preenchido automaticamente>",
      "selected_rule_ids": ["rule_adx_ge_25", "rule_taker_ratio_ge_055"]
    }
  ]
}`}</pre>

            {/* Live: available profiles */}
            <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-base)] overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-3 py-2">
                <div>
                  <h4 className="text-[12px] font-semibold text-[var(--text-primary)]">Profiles disponíveis em Strategy Profiles</h4>
                  <p className="text-[10px] text-[var(--text-tertiary)]">{existingProfiles.length} profiles — use profile_id ou profile_name</p>
                </div>
                <span className="rounded bg-[var(--accent-primary)]/10 px-2 py-1 text-[10px] font-semibold text-[var(--accent-primary)]">ao vivo</span>
              </div>
              {existingProfiles.length > 0 ? (
                <div className="max-h-56 overflow-auto">
                  <table className="w-full text-[11px]">
                    <thead className="sticky top-0 bg-[var(--bg-tertiary)]">
                      <tr className="border-b border-[var(--border-subtle)]">
                        <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">profile_id</th>
                        <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">profile_name</th>
                        <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">papel</th>
                        <th className="px-3 py-2 text-center text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">ativo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {existingProfiles.map((p) => (
                        <tr key={p.id} className="border-b border-[var(--border-subtle)]/60 last:border-0">
                          <td className="px-3 py-1.5 font-mono text-[10px] text-[var(--text-secondary)] select-all">{p.id}</td>
                          <td className="px-3 py-1.5 font-medium text-[var(--text-primary)]">{p.name}</td>
                          <td className="px-3 py-1.5"><RoleBadge role={p.profile_role ?? undefined} /></td>
                          <td className="px-3 py-1.5 text-center">
                            {p.is_active
                              ? <CheckCircle2 className="w-3 h-3 text-[var(--color-profit)] mx-auto" />
                              : <span className="text-[var(--text-tertiary)]">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="px-3 py-3 text-[11px] text-[var(--text-tertiary)]">Nenhum profile encontrado (ou ainda carregando).</p>
              )}
            </div>

            {/* Live: available rule ids */}
            <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-base)] overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-3 py-2">
                <div>
                  <h4 className="text-[12px] font-semibold text-[var(--text-primary)]">Rule IDs disponíveis na matriz global de Score</h4>
                  <p className="text-[10px] text-[var(--text-tertiary)]">{globalRules.length} regras — valores aceitos em selected_rule_ids</p>
                </div>
                <span className="rounded bg-[var(--accent-primary)]/10 px-2 py-1 text-[10px] font-semibold text-[var(--accent-primary)]">ao vivo</span>
              </div>
              {globalRules.length > 0 ? (
                <div className="max-h-56 overflow-auto">
                  <table className="w-full text-[11px]">
                    <thead className="sticky top-0 bg-[var(--bg-tertiary)]">
                      <tr className="border-b border-[var(--border-subtle)]">
                        <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">rule_id</th>
                        <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">indicator</th>
                        <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">category</th>
                        <th className="px-3 py-2 text-right text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">points</th>
                      </tr>
                    </thead>
                    <tbody>
                      {globalRules.map((rule) => (
                        <tr key={String(rule.id)} className="border-b border-[var(--border-subtle)]/60 last:border-0">
                          <td className="px-3 py-1.5 font-mono text-[10px] font-semibold text-[var(--text-primary)] select-all">{String(rule.id)}</td>
                          <td className="px-3 py-1.5 font-mono text-[var(--text-secondary)]">{rule.indicator}</td>
                          <td className="px-3 py-1.5 text-[var(--text-secondary)]">{String(rule.category || "").replace("_", " ")}</td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--accent-primary)]">{rule.points}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="px-3 py-3 text-[11px] text-[var(--text-tertiary)]">
                  Matriz global vazia — importe e salve a matriz em{" "}
                  <a href="/settings/score" className="text-[var(--accent-primary)] hover:underline">Settings → Score Engine</a>{" "}
                  antes de associar regras aos profiles.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── STAGE: PREVIEW ── */}
      {stage === "preview" && (
        <div className="space-y-4">
          {/* Summary bar */}
          <div className="flex items-center gap-4 p-4 bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl">
            {(applyToActiveProfiles || parsed.length > 0) && (
              <div className="flex items-center gap-2 text-[13px]">
                <CheckCircle2 className="w-4 h-4 text-[var(--color-profit)]" />
                <span className="text-[var(--text-primary)] font-semibold">{validCount}</span>
                <span className="text-[var(--text-secondary)]">válidos</span>
              </div>
            )}
            {scoringAssignments.length > 0 && (
              <div className="flex items-center gap-2 text-[13px]">
                <Target className="w-4 h-4 text-[#FBBF24]" />
                <span className="text-[var(--text-primary)] font-semibold">{scoringAssignments.length}</span>
                <span className="text-[var(--text-secondary)]">associações de scoring</span>
              </div>
            )}
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
                onClick={() => { setParsed([]); setScoringAssignments([]); setApplyToActiveProfiles(false); setStage("upload"); setParseError(null); }}
              >
                Trocar arquivo
              </button>
              <button
                className="btn btn-primary px-5"
                onClick={handleImport}
                disabled={importing || !canImport}
              >
                {importing
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Importando...</>
                  : applyToActiveProfiles
                  ? <><Upload className="w-4 h-4 mr-2" />Atualizar profiles ativos</>
                  : <><Upload className="w-4 h-4 mr-2" />
                      {[
                        validCount > 0 ? `Importar ${validCount} profile${validCount !== 1 ? "s" : ""}` : null,
                        scoringAssignments.length > 0 ? `${validCount > 0 ? "+ " : "Aplicar "}${scoringAssignments.length} scoring${scoringAssignments.length !== 1 ? "s" : ""}` : null,
                      ].filter(Boolean).join(" ")}
                    </>
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

          {applyToActiveProfiles && (
            <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-3">
              <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">
                Atualizacao em massa do Scoring
              </h3>
              <p className="text-[13px] text-[var(--text-secondary)] leading-relaxed">
                Esta importacao nao cria profiles. Ela atualiza todos os profiles ativos do usuario e substitui
                <code className="mx-1 font-mono text-[var(--accent-primary)]">config.scoring.selected_rule_ids</code>
                pelos IDs informados em <code className="font-mono text-[var(--accent-primary)]">profile_scoring.selected_rule_ids</code>.
              </p>
              <div className="text-[12px] text-[var(--text-secondary)]">
                Regras selecionadas: <span className="font-semibold text-[var(--text-primary)]">{selectedScoringCount}</span>
              </div>
            </div>
          )}

          {/* Scoring assignments preview */}
          {!applyToActiveProfiles && scoringAssignments.length > 0 && (
            <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-[var(--border-default)] bg-[var(--bg-tertiary)]">
                <h3 className="text-[13px] font-semibold text-[var(--text-primary)] flex items-center gap-2">
                  <Target className="w-4 h-4 text-[#FBBF24]" />
                  Associações de Scoring ({scoringAssignments.length})
                </h3>
              </div>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-[var(--border-subtle)]">
                    <th className="text-left px-4 py-2 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider w-8">#</th>
                    <th className="text-left px-4 py-2 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Profile alvo</th>
                    <th className="text-center px-4 py-2 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Regras</th>
                    <th className="text-left px-4 py-2 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Verificação</th>
                  </tr>
                </thead>
                <tbody>
                  {scoringAssignments.map((a, idx) => {
                    const resolved = resolveAssignmentProfile(a);
                    const ruleIds = assignmentRuleIds(a, sharedScoring);
                    const unknownRules = globalRuleIds.size > 0
                      ? ruleIds.filter((id) => !globalRuleIds.has(id))
                      : [];
                    return (
                      <tr key={idx} className="border-b border-[var(--border-subtle)] last:border-0">
                        <td className="px-4 py-2.5 text-[var(--text-tertiary)] font-mono text-[11px]">{idx + 1}</td>
                        <td className="px-4 py-2.5">
                          <span className="font-medium text-[var(--text-primary)]">
                            {resolved?.name ?? assignmentTarget(a)}
                          </span>
                          {resolved && (
                            <span className="ml-2 font-mono text-[10px] text-[var(--text-tertiary)]">{resolved.id.slice(0, 8)}…</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-center font-semibold text-[var(--accent-primary)]">{ruleIds.length}</td>
                        <td className="px-4 py-2.5 text-[12px]">
                          {!resolved ? (
                            <span className="flex items-center gap-1.5 text-[var(--color-loss)]">
                              <XCircle className="w-3.5 h-3.5 shrink-0" />
                              Profile não encontrado em Strategy Profiles
                            </span>
                          ) : unknownRules.length > 0 ? (
                            <span className="flex items-center gap-1.5 text-yellow-400">
                              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                              {unknownRules.length} rule_id{unknownRules.length > 1 ? "s" : ""} fora da matriz global:{" "}
                              <span className="font-mono text-[10px]">{unknownRules.join(", ")}</span>
                            </span>
                          ) : (
                            <span className="flex items-center gap-1.5 text-[var(--color-profit)]">
                              <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                              OK
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Profiles table */}
          {!applyToActiveProfiles && parsed.length > 0 && (
          <div className="bg-[var(--bg-secondary)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--border-default)] bg-[var(--bg-tertiary)]">
                  <th className="text-left px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider w-8">#</th>
                  <th className="text-left px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Nome</th>
                  <th className="text-left px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Papel no Funil</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">TF</th>
                  <th className="text-center px-4 py-3 text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Scoring</th>
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
                      {countScoreRules(p.raw) > 0
                        ? <span className="text-[12px] font-semibold text-[var(--accent-primary)]">{countScoreRules(p.raw)} rules</span>
                        : <span className="text-[11px] text-[var(--text-tertiary)]">all</span>}
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
          )}
        </div>
      )}

      {/* ── STAGE: RESULT ── */}
      {stage === "result" && (
        <div className="max-w-2xl mx-auto mt-4 space-y-6">
          {/* Summary */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[var(--color-profit)]/8 border border-[var(--color-profit)]/20 rounded-xl p-6 text-center">
              <CheckCircle2 className="w-8 h-8 text-[var(--color-profit)] mx-auto mb-2" />
              <div className="text-3xl font-bold text-[var(--color-profit)]">
                {applyToActiveProfiles ? summary.updated : summary.created + summary.updated}
              </div>
              <div className="text-[13px] text-[var(--text-secondary)] mt-1">
                {applyToActiveProfiles
                  ? "profiles atualizados"
                  : summary.updated > 0
                  ? `${summary.created} criados · ${summary.updated} scoring atualizado${summary.updated !== 1 ? "s" : ""}`
                  : "profiles criados"}
              </div>
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
                {r.status !== "error"
                  ? <CheckCircle2 className="w-4 h-4 text-[var(--color-profit)] shrink-0" />
                  : <XCircle className="w-4 h-4 text-[var(--color-loss)] shrink-0" />
                }
                <span className="font-medium text-[var(--text-primary)] text-[13px] flex-1">{r.name}</span>
                {r.status === "error" && (
                  <span className="text-[12px] text-[var(--color-loss)]">{r.error}</span>
                )}
                {r.status !== "error" && r.id && (
                  <span className="text-[11px] text-[var(--text-tertiary)] font-mono">{r.id.slice(0, 8)}…</span>
                )}
              </div>
            ))}
          </div>

          <div className="flex gap-3">
            <button className="btn btn-secondary flex-1" onClick={() => { setParsed([]); setScoringAssignments([]); setRawJson(""); setApplyToActiveProfiles(false); setStage("upload"); }}>
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
