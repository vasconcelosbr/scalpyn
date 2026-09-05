"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Boxes, Check, ChevronDown, ChevronUp, Download, FileJson,
  Gauge, Layers3, RefreshCw, Save, ShieldCheck, SlidersHorizontal,
  TimerReset, Upload, WalletCards,
} from "lucide-react";

import { ModuleAIAnalysisAction } from "@/components/ai/ModuleAIAnalysisAction";
import { StrategySettingsJsonModal } from "@/components/settings/StrategySettingsJsonModal";
import {
  ENUM_OPTIONS, JsonObject, JsonValue, R6_READ_ONLY_PATHS, StrategyDefinition,
  StrategySettingsBundle, StrategySettingsValidation,
  downloadSavedStrategySettings, editablePayload, loadStrategySettings,
  normaliseBarrierContract, saveStrategySettings, updateAtPath,
  validateStrategySettings,
} from "@/lib/strategySettings";

const COPY: Record<string, { label: string; help?: string }> = {
  "spot_engine.scanner.multilayer_contract.enabled": { label: "Autoridade multicamada", help: "Permanece desligada no R6. A ativação exige release e autorização posteriores." },
  "spot_engine.scanner.multilayer_contract.execution_contract_version": { label: "Contrato de execução multicamada" },
  "spot_engine.scanner.multilayer_contract.execution_contract_valid_from": { label: "Válido desde", help: "Identidade temporal da estrutura R6; não ativa L1 ou L2." },
  "spot_engine.scanner.multilayer_contract.provenance_policy_version": { label: "Política de proveniência por camada" },
  "spot_engine.scanner.multilayer_contract.consolidation_rule_version": { label: "Unidade de consolidação" },
  "spot_engine.scanner.multilayer_contract.consolidation_valid_from": { label: "Consolidação válida desde" },
  "spot_engine.scanner.multilayer_contract.decision_feature_contract_version": { label: "Contrato futuro das features de decisão" },
  "spot_engine.scanner.multilayer_contract.decision_feature_valid_from": { label: "Fronteira futura de dados", help: "Deve permanecer vazia nesta release; será preenchida somente na migração de autoridade." },
  "spot_engine.scanner.l3_single_profile_per_symbol_enabled": { label: "Consolidar Shadows aprovados por ativo", help: "Cria um único Shadow aprovado por símbolo e direção. Esta ativação é independente da consolidação dos rejeitados." },
  "spot_engine.scanner.l3_rejected_single_profile_per_symbol_enabled": { label: "Consolidar Shadows rejeitados por ativo", help: "Cria um único L3_REJECTED por símbolo e direção, mantém o profile prioritário como principal e registra os demais como associados. Não autoriza trades e não altera a consolidação dos aprovados." },
  "ml_shadow.shadow_capture_l3_rejected_max_per_hour": { label: "Limite horário de rejeitados consolidados", help: "Aplicado somente depois da consolidação; conta vencedores canônicos, não a quantidade bruta de profiles. O valor precisa estar persistido antes da ativação." },
  "spot_engine.scanner.l3_v3_contract_preserve": { label: "Preservar contrato v3 do L3", help: "Mantém o contrato de autorização ao atualizar métricas após o Social Score. Desligar atua como kill switch imediato." },
  "spot_engine.scanner.l3_condition_status_capture": { label: "Capturar status por condição L3", help: "Persiste PASS, FAIL ou SKIPPED, valor observado e motivo em cada condição de block rule." },
  "spot_engine.scanner.l3_metrics_provenance": { label: "Proveniência das métricas L3", help: "Usa no topo o objeto realmente avaliado pelo gate e mantém a projeção bruta separada para auditoria." },
  "spot_engine.scanner.l3_zero_is_value": { label: "Zero legítimo é valor", help: "Trata zero de indicadores compatíveis, como volume_spike, como valor observado em vez de ausência." },
  "spot_engine.scanner.l3_block_and_skipped_policy": { label: "Política AND + SKIPPED", help: "legacy mantém o comportamento atual; not_satisfied registra a condição pulada como não satisfeita sem fazê-la bloquear." },
  "spot_engine.scanner.l3_missing_indicator_policy": { label: "Política de indicador inexistente", help: "warn mantém a regra visível como SKIPPED; disable_rule a desativa explicitamente. breakout_distance_pct e psar_trend ainda não têm produtor canônico." },
  "spot_engine.scanner.l3_v3_provenance_resolver.enabled": { label: "Resolvedor de proveniência L3 v3", help: "Ativa a resolução determinística somente para os profiles presentes na lista de canário." },
  "spot_engine.scanner.l3_v3_provenance_resolver.profile_allowlist": { label: "Profiles autorizados no canário", help: "IDs exatos dos profiles que podem usar o resolvedor. Lista vazia mantém todos desativados." },
  "spot_engine.scanner.l3_v3_provenance_resolver.policy_version": { label: "Versão da política de proveniência", help: "Identifica de forma imutável o contrato usado para resolver as features congeladas na decisão." },
  "spot_engine.scanner.l3_v3_provenance_resolver.source_policies": { label: "Políticas por fonte", help: "Configuração auditável para OHLCV, fluxo de trades, livro de ofertas e contexto da decisão." },
  "spot_engine.scanner.l3_global_block_range_compiler.enabled": { label: "Compilador de faixa global", help: "Quando ativo, regras planas com mínimo e máximo bloqueiam somente fora da faixa, apenas nos profiles autorizados." },
  "spot_engine.scanner.l3_global_block_range_compiler.profile_allowlist": { label: "Profiles autorizados para regras de faixa", help: "IDs exatos do canário. Lista vazia mantém o compilador sem efeito operacional." },
  "spot_engine.scanner.l3_global_block_range_compiler.policy_version": { label: "Versão do compilador de faixa" },
  "spot_engine.selling.never_sell_at_loss": { label: "Nunca vender com prejuízo", help: "Proteção do Spot real. O Kill Switch pode sobrepor esta regra em emergência." },
  "spot_engine.shadow.amount_usdt": { label: "Valor por trade Shadow", help: "Valor nominal congelado no snapshot de cada novo trade." },
  "spot_engine.shadow.timeout_candles": { label: "Prazo do outcome operacional (candles)", help: "Quantidade máxima de candles antes do encerramento operacional por TIMEOUT." },
  "spot_engine.shadow.trailing_contract_version": { label: "Contrato técnico do trailing" },
  "spot_engine.shadow.ttt.enabled": { label: "TTT ativo", help: "Habilita a política Time to Target para novos trades Shadow." },
  "spot_engine.shadow.ttt.tp_pct": { label: "Alvo do TTT (%)" },
  "spot_engine.shadow.ttt.timeout_minutes": { label: "Janela do label analítico TTT (minutos)", help: "Usada apenas no label analítico; não encerra a posição." },
  "ml_shadow.shadow_barrier_mode": { label: "Modo das barreiras" },
  "ml_shadow.shadow_atr_timeframe": { label: "Timeframe do ATR" },
  "ml_shadow.shadow_atr_multiplier_tp": { label: "Multiplicador ATR do TP" },
  "ml_shadow.shadow_atr_multiplier_sl": { label: "Multiplicador ATR do SL" },
  "ml_shadow.shadow_barrier_min_pct": { label: "Piso do SL dinâmico (%)" },
  "ml_shadow.shadow_barrier_max_pct": { label: "Teto do SL dinâmico (%)" },
  "ml_shadow.ml_fee_roundtrip_pct": { label: "Taxa round-trip (%)", help: "Custo total usado no retorno líquido do Shadow." },
  "ml_shadow.ml_active_barrier_contract_version": { label: "Versão do contrato de barreiras" },
  "ml_shadow.shadow_measurement_timeframe_priority": { label: "Prioridade de timeframes da medição", help: "Ordem explícita para reconciliar MFE/MAE, por exemplo: 1m, 5m." },
  "ml_shadow.shadow_entry_max_lag_seconds": { label: "Lag máximo da entrada (segundos)", help: "Vazio mantém a captura inelegível como UNCONFIGURED; não bloqueia a simulação." },
  "ml_shadow.shadow_barrier_geometry_policy": { label: "Política geométrica", help: "LEGACY mantém o contrato atual. As outras políticas só afetam novos trades quando o contrato v3 for escolhido." },
  "ml_shadow.shadow_canonical_barrier_enabled": { label: "Avaliador canônico por OHLCV fechada", help: "Ativa a detecção somente para IDs presentes na lista de canário." },
  "ml_shadow.shadow_canonical_barrier_profile_allowlist": { label: "Profiles do canário da barreira", help: "Lista de IDs exatos. Vazio significa nenhum profile ativo." },
  "ml_shadow.shadow_canonical_barrier_policy_version": { label: "Versão do avaliador canônico" },
  "ml_shadow.canary_minimum_outcomes": { label: "Mínimo de outcomes para avaliação", help: "Governança: impede conclusões antes deste número de Shadows terminais; não altera TP, SL ou autorização." },
};

const GROUP_COPY: Record<string, { title: string; subtitle: string; effect: string }> = {
  scanner: { title: "Scanner e entrada", subtitle: "Universo, score, frequência e cooldown de oportunidades.", effect: "Ambos" },
  buying: { title: "Capital e compra", subtitle: "Alocação, exposição, posições e execução de ordens.", effect: "Spot real" },
  shadow: { title: "Shadow Portfolio — Execução", subtitle: "Valor, timeout, TTT e versão técnica congelados em novos trades.", effect: "Shadow" },
  selling: { title: "Política global de venda", subtitle: "Lucro, proteção contra prejuízo e consulta de IA.", effect: "Spot real" },
  holding_underwater: { title: "Holding underwater", subtitle: "Alertas e custo de oportunidade de posições submersas.", effect: "Spot real" },
  dca: { title: "DCA", subtitle: "Reentrada, camadas, decaimento e exposição máxima.", effect: "Spot real" },
  sell_flow: { title: "Pipeline de saída", subtitle: "Mean Reversion, Momentum, AI Hold, trailing, Kill Switch e filtros.", effect: "Ambos" },
  macro_filter: { title: "Filtro macro", subtitle: "Bloqueio de entrada em regime risk-off.", effect: "Spot real" },
};

function humanise(value: string) {
  const labels: Record<string, string> = {
    allowed_source_providers: "Provedores permitidos",
    provider_policy_id: "ID da política do provedor",
    max_age_seconds: "Frescor máximo (segundos)",
    timeframe: "Timeframe",
    window_seconds: "Janela (segundos)",
    snapshot: "Snapshot pontual",
    candle_policy: "Política de candle",
    ohlcv: "OHLCV",
    live_trade_flow: "Fluxo de trades",
    live_order_book: "Livro de ofertas",
    decision_context: "Contexto da decisão",
  };
  return labels[value] ?? value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function EffectBadge({ children, tone = "violet" }: { children: React.ReactNode; tone?: "violet" | "green" | "amber" | "blue" }) {
  const tones = { violet: "border-violet-500/30 bg-violet-500/10 text-violet-300", green: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300", amber: "border-amber-500/30 bg-amber-500/10 text-amber-300", blue: "border-blue-500/30 bg-blue-500/10 text-blue-300" };
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tones[tone]}`}>{children}</span>;
}

function FieldEditor({ path, value, onChange }: { path: string; value: JsonValue; onChange: (path: string, value: JsonValue) => void }) {
  const copy = COPY[path];
  const label = copy?.label ?? humanise(path.split(".").at(-1) ?? path);
  const options = ENUM_OPTIONS[path];
  const readOnly = R6_READ_ONLY_PATHS.has(path);
  if (typeof value === "boolean") {
    return <label className="flex min-h-20 items-center justify-between gap-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4"><span><span className="block text-sm font-medium text-[var(--text-primary)]">{label}</span>{copy?.help && <span className="mt-1 block text-[11px] leading-relaxed text-[var(--text-tertiary)]">{copy.help}</span>}</span><button disabled={readOnly} type="button" role="switch" aria-checked={value} onClick={() => onChange(path, !value)} className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 ${value ? "bg-violet-500" : "bg-[var(--border-default)]"}`}><span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition ${value ? "left-6" : "left-1"}`} /></button></label>;
  }
  if (Array.isArray(value) || path === "ml_shadow.shadow_measurement_timeframe_priority") {
    const arrayValue = Array.isArray(value) ? value : [];
    const placeholder = path.endsWith("profile_allowlist")
      ? "UUID do profile, UUID do profile"
      : path.endsWith("allowed_source_providers")
        ? "gate_ohlcv_canonical, outro_provedor"
        : path === "ml_shadow.shadow_measurement_timeframe_priority"
          ? "1m, 5m"
          : "";
    return <label className="block space-y-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4"><span className="text-xs font-medium text-[var(--text-secondary)]">{label}</span><input className="input w-full font-mono text-sm" type="text" value={arrayValue.join(", ")} placeholder={placeholder} onChange={(event) => onChange(path, event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} />{copy?.help && <span className="block text-[11px] leading-relaxed text-[var(--text-tertiary)]">{copy.help}</span>}</label>;
  }
  const nullableNumber = path === "ml_shadow.shadow_entry_max_lag_seconds" || path === "ml_shadow.canary_minimum_outcomes";
  return <label className="block space-y-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4"><span className="text-xs font-medium text-[var(--text-secondary)]">{label}</span>{options ? <select disabled={readOnly} className="input w-full text-sm disabled:cursor-not-allowed disabled:opacity-60" value={String(value)} onChange={(event) => onChange(path, event.target.value)}>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select> : <input readOnly={readOnly} className="input w-full font-mono text-sm read-only:cursor-not-allowed read-only:opacity-60" type={typeof value === "number" || nullableNumber ? "number" : "text"} step={typeof value === "number" || nullableNumber ? "any" : undefined} value={String(value ?? "")} onChange={(event) => onChange(path, nullableNumber ? (event.target.value === "" ? null : Number(event.target.value)) : typeof value === "number" ? Number(event.target.value) : event.target.value)} />}{copy?.help && <span className="block text-[11px] leading-relaxed text-[var(--text-tertiary)]">{copy.help}</span>}</label>;
}

function RecursiveEditor({ value, prefix, onChange }: { value: JsonObject; prefix: string; onChange: (path: string, value: JsonValue) => void }) {
  return <div className="grid gap-3 md:grid-cols-2">{Object.entries(value).map(([key, child]) => { const path = `${prefix}.${key}`; if (child && typeof child === "object" && !Array.isArray(child)) return <div key={path} className="md:col-span-2 rounded-xl border border-[var(--border-subtle)] p-4"><h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">{humanise(key)}</h4><RecursiveEditor value={child as JsonObject} prefix={path} onChange={onChange} /></div>; return <FieldEditor key={path} path={path} value={child} onChange={onChange} />; })}</div>;
}

function ConfigSection({ group, value, onChange }: { group: string; value: JsonObject; onChange: (path: string, value: JsonValue) => void }) {
  const [open, setOpen] = useState(group === "shadow" || group === "selling");
  const copy = GROUP_COPY[group] ?? { title: humanise(group), subtitle: "Configuração operacional persistida.", effect: "Spot real" };
  return <section className="overflow-hidden rounded-2xl border border-[var(--border-default)] bg-[var(--bg-secondary)]"><button type="button" onClick={() => setOpen(!open)} className="flex w-full items-start justify-between gap-4 p-5 text-left"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-[15px] font-semibold">{copy.title}</h3><EffectBadge tone={copy.effect === "Shadow" ? "blue" : copy.effect === "Ambos" ? "violet" : "green"}>{copy.effect}</EffectBadge></div><p className="mt-1 text-xs text-[var(--text-secondary)]">{copy.subtitle}</p></div>{open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</button>{open && <div className="border-t border-[var(--border-subtle)] p-5"><RecursiveEditor value={value} prefix={`spot_engine.${group}`} onChange={onChange} /></div>}</section>;
}

function StrategyEditor({ strategies, onChange }: { strategies: StrategyDefinition[]; onChange: (value: StrategyDefinition[]) => void }) {
  return <section className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-secondary)] p-5"><div className="mb-4 flex flex-wrap items-center gap-2"><Layers3 className="h-4 w-4 text-violet-400" /><h2 className="font-semibold">Estratégias de entrada</h2><EffectBadge>Ambos</EffectBadge></div>{strategies.length === 0 ? <p className="text-sm text-[var(--text-secondary)]">Nenhuma estratégia cadastrada.</p> : <div className="grid gap-3 lg:grid-cols-2">{strategies.map((strategy, index) => <div key={`${strategy.id}-${index}`} className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4"><div className="flex items-center justify-between gap-3"><div><p className="text-sm font-semibold">{strategy.name}</p><code className="text-[10px] text-[var(--text-tertiary)]">{strategy.id}</code></div><button role="switch" aria-checked={strategy.enabled} className={`relative h-6 w-11 rounded-full ${strategy.enabled ? "bg-violet-500" : "bg-[var(--border-default)]"}`} onClick={() => onChange(strategies.map((item, itemIndex) => itemIndex === index ? { ...item, enabled: !item.enabled } : item))}><span className={`absolute top-1 h-4 w-4 rounded-full bg-white ${strategy.enabled ? "left-6" : "left-1"}`} /></button></div><div className="mt-4 grid gap-2">{Object.entries(strategy.params).map(([key, value]) => <label key={key} className="flex items-center justify-between gap-3 text-xs text-[var(--text-secondary)]"><span>{humanise(key)}</span><input className="input w-28 font-mono text-xs" type="number" step="any" value={value} onChange={(event) => onChange(strategies.map((item, itemIndex) => itemIndex === index ? { ...item, params: { ...item.params, [key]: Number(event.target.value) } } : item))} /></label>)}</div></div>)}</div>}</section>;
}

export default function StrategySettingsPage() {
  const [saved, setSaved] = useState<StrategySettingsBundle | null>(null);
  const [draft, setDraft] = useState<JsonObject | null>(null);
  const [catalog, setCatalog] = useState<JsonObject | null>(null);
  const [persisted, setPersisted] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);

  const refresh = async () => { setBusy(true); setError(null); try { const result = await loadStrategySettings(); setSaved(result.config); setDraft(editablePayload(result.config)); setCatalog(result.catalog); setPersisted(result.persisted); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(false); } };
  useEffect(() => { void refresh(); }, []);
  const dirty = useMemo(() => saved && draft ? JSON.stringify(draft) !== JSON.stringify(editablePayload(saved)) : false, [draft, saved]);
  const barrierMode = String((draft?.ml_shadow as JsonObject | undefined)?.shadow_barrier_mode ?? "");
  const change = (path: string, value: JsonValue) => { if (!draft) return; let next = updateAtPath(draft, path.split("."), value); if (path === "ml_shadow.shadow_barrier_mode") next = normaliseBarrierContract(next); setDraft(next); setMessage(null); };
  const save = async (payload = draft, source: "FORM" | "JSON_IMPORT" = "FORM") => { if (!saved || !payload) return; setBusy(true); setError(null); setMessage(null); try { const result = await saveStrategySettings(payload, saved.source_hash, source); setSaved(result.config); setDraft(editablePayload(result.config)); setCatalog(result.catalog); setMessage("Configuração salva e confirmada. Somente novos trades usarão os novos valores."); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); throw cause; } finally { setBusy(false); } };

  if (busy && !draft) return <div className="space-y-4 p-8">{[1, 2, 3].map((item) => <div key={item} className="skeleton h-40 rounded-2xl" />)}</div>;
  if (!draft || !saved) return <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-5 text-sm text-red-300">{error ?? "Configuração indisponível."}</div>;
  const spot = draft.spot_engine as JsonObject;
  const strategies = ((draft.strategy as JsonObject).strategies ?? []) as unknown as StrategyDefinition[];
  const expected = JSON.stringify({ ...saved, ...draft }, null, 2);

  return <div className="space-y-6 pb-12">
    <header className="relative overflow-hidden rounded-2xl border border-violet-500/20 bg-[radial-gradient(circle_at_top_right,rgba(124,58,237,.18),transparent_38%),var(--bg-secondary)] p-6"><div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-start"><div><div className="mb-2 flex flex-wrap gap-2"><EffectBadge>Centro operacional</EffectBadge><EffectBadge tone="green">Configuração persistida</EffectBadge><EffectBadge tone="amber">Fallback: removido</EffectBadge></div><h1 className="text-2xl font-bold tracking-tight">Módulo Estratégias</h1><p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--text-secondary)]">Controle completo do SpotEngine, Shadow Portfolio, barreiras ATR, trailing, taxas, timeout e TTT. Cada novo trade recebe um snapshot imutável.</p></div><div className="flex flex-wrap gap-2"><ModuleAIAnalysisAction originModule="strategies" originView="settings-strategies" compact /><button className="btn btn-secondary" onClick={async () => { if (dirty && !confirm("Há alterações locais não salvas. O arquivo exportado conterá apenas a versão salva. Continuar?")) return; await downloadSavedStrategySettings(); }}><Download className="mr-2 h-4 w-4" />Exportar JSON</button><button className="btn btn-secondary" onClick={() => setImportOpen(true)}><Upload className="mr-2 h-4 w-4" />Importar JSON</button><button className="btn btn-primary" disabled={!dirty || busy} onClick={() => void save()}>{busy ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Salvar tudo</button></div></div><div className="mt-5 grid gap-3 sm:grid-cols-3"><div className="rounded-xl border border-[var(--border-subtle)] bg-black/10 p-3"><p className="text-[10px] uppercase text-[var(--text-tertiary)]">Hash salvo</p><code className="mt-1 block text-xs text-violet-300">{saved.source_hash.slice(0, 16)}…</code></div><div className="rounded-xl border border-[var(--border-subtle)] bg-black/10 p-3"><p className="text-[10px] uppercase text-[var(--text-tertiary)]">Documentos</p><p className="mt-1 text-xs">{Object.values(persisted).every(Boolean) ? "3 de 3 persistidos" : "Materialização pendente"}</p></div><div className="rounded-xl border border-[var(--border-subtle)] bg-black/10 p-3"><p className="text-[10px] uppercase text-[var(--text-tertiary)]">Estado local</p><p className={`mt-1 text-xs ${dirty ? "text-amber-300" : "text-emerald-300"}`}>{dirty ? "Alterações não salvas" : "Sincronizado"}</p></div></div></header>
    {message && <div className="flex gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/10 p-4 text-sm text-emerald-300"><Check className="h-4 w-4" />{message}</div>}{error && <div className="flex gap-2 rounded-xl border border-red-500/25 bg-red-500/10 p-4 text-sm text-red-300"><AlertTriangle className="h-4 w-4" />{error}</div>}
    <div className="grid gap-4 lg:grid-cols-3"><div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-secondary)] p-5"><WalletCards className="h-5 w-5 text-emerald-400" /><h2 className="mt-3 font-semibold">Spot real</h2><p className="mt-1 text-xs text-[var(--text-secondary)]">Entrada, capital, venda, underwater, DCA e filtro macro.</p></div><div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-secondary)] p-5"><Boxes className="h-5 w-5 text-blue-400" /><h2 className="mt-3 font-semibold">Shadow</h2><p className="mt-1 text-xs text-[var(--text-secondary)]">Barreiras, custos, timeout, TTT, valor e contratos técnicos.</p></div><div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-secondary)] p-5"><ShieldCheck className="h-5 w-5 text-violet-400" /><h2 className="mt-3 font-semibold">Snapshot imutável</h2><p className="mt-1 text-xs text-[var(--text-secondary)]">Trades abertos não são recalculados quando esta tela muda.</p></div></div>
    <StrategyEditor strategies={strategies} onChange={(value) => change("strategy.strategies", value as unknown as JsonValue)} />
    <div className="space-y-3">{Object.entries(spot).map(([group, value]) => <ConfigSection key={group} group={group} value={value as JsonObject} onChange={change} />)}</div>
    <section className="rounded-2xl border border-blue-500/25 bg-[linear-gradient(135deg,rgba(37,99,235,.10),transparent_55%),var(--bg-secondary)] p-5"><div className="mb-4 flex flex-wrap items-center gap-2"><Gauge className="h-5 w-5 text-blue-400" /><h2 className="font-semibold">Shadow Portfolio — Barreiras e custos</h2><EffectBadge tone="blue">Shadow</EffectBadge>{barrierMode === "ATR_DYNAMIC" && <EffectBadge tone="green">Ativo neste modo</EffectBadge>}</div>{barrierMode === "ATR_DYNAMIC" && <div className="mb-4 flex gap-3 rounded-xl border border-amber-500/25 bg-amber-500/10 p-4"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" /><p className="text-xs leading-relaxed text-amber-100"><strong>ATR_DYNAMIC v2 está ativo.</strong> O TP Spot e o Kill Switch exibidos acima não são as barreiras efetivas deste modo. TP e SL são calculados pelo ATR no momento da entrada, limitados pelo piso e teto abaixo. O trailing continua sendo avaliado pelo contrato HWM congelado no trade.</p></div>}<RecursiveEditor value={draft.ml_shadow as JsonObject} prefix="ml_shadow" onChange={change} /></section>
    <section className="overflow-hidden rounded-2xl border border-[var(--border-default)] bg-[var(--bg-secondary)]"><button className="flex w-full items-center justify-between p-5 text-left" onClick={() => setCatalogOpen(!catalogOpen)}><div className="flex items-center gap-3"><FileJson className="h-5 w-5 text-violet-400" /><div><h2 className="font-semibold">Estrutura esperada</h2><p className="text-xs text-[var(--text-secondary)]">Contrato fornecido pelo backend: campos, tipos, limites e enums.</p></div></div>{catalogOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</button>{catalogOpen && <div className="grid gap-4 border-t border-[var(--border-subtle)] p-5 xl:grid-cols-2"><div><p className="mb-2 text-xs font-semibold uppercase text-[var(--text-tertiary)]">Catálogo validado</p><pre className="max-h-[560px] overflow-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-base)] p-4 text-[10px] leading-relaxed text-[var(--text-secondary)]">{JSON.stringify(catalog, null, 2)}</pre></div><div><p className="mb-2 text-xs font-semibold uppercase text-[var(--text-tertiary)]">Exemplo exportável atual</p><pre className="max-h-[560px] overflow-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-base)] p-4 text-[10px] leading-relaxed text-[var(--text-secondary)]">{expected}</pre></div></div>}</section>
    <div className="sticky bottom-4 flex items-center justify-between gap-4 rounded-2xl border border-[var(--border-default)] bg-[color-mix(in_srgb,var(--bg-base)_92%,transparent)] p-4 shadow-2xl backdrop-blur"><div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]"><TimerReset className="h-4 w-4" />{dirty ? "Há alterações locais aguardando gravação." : "Todos os valores estão sincronizados com o backend."}</div><button className="btn btn-primary" disabled={!dirty || busy} onClick={() => void save()}><SlidersHorizontal className="mr-2 h-4 w-4" />Aplicar aos novos trades</button></div>
    {importOpen && <StrategySettingsJsonModal template={expected} sourceHash={saved.source_hash} onClose={() => setImportOpen(false)} onValidate={(payload) => validateStrategySettings(payload, saved.source_hash)} onApply={async (validation: StrategySettingsValidation) => { await save(editablePayload(validation.config), "JSON_IMPORT"); }} />}
  </div>;
}
