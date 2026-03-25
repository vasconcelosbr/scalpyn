"use client";

import { useState, useEffect, useCallback } from "react";
import { Sparkles, Plus, Edit2, Trash2, ChevronDown, X, AlertTriangle, RefreshCw } from "lucide-react";

const API_BASE = "/api/ai-skills";

const ROLE_OPTIONS = [
  { value: "",                  label: "— Nenhum (uso geral) —" },
  { value: "universe_filter",   label: "Pool — Filtro de Universo (Stage 0)" },
  { value: "primary_filter",    label: "L1 — Filtro Primário (Stage 1)" },
  { value: "score_engine",      label: "L2 — Score Engine (Stage 2)" },
  { value: "acquisition_queue", label: "L3 — Fila de Aquisição (Stage 3)" },
];

interface Skill {
  id: string;
  name: string;
  description: string | null;
  role_key: string | null;
  role_label: string | null;
  prompt_text: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface DefaultPrompt {
  role_key: string;
  role_label: string;
  prompt_text: string;
}

function useAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("access_token") || sessionStorage.getItem("access_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function AiSkillsPage() {
  const [skills, setSkills]       = useState<Skill[]>([]);
  const [defaults, setDefaults]   = useState<DefaultPrompt[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);

  const [modal, setModal]         = useState<"create" | "edit" | null>(null);
  const [editing, setEditing]     = useState<Skill | null>(null);
  const [deleteTarget, setDelete] = useState<Skill | null>(null);
  const [saving, setSaving]       = useState(false);
  const [deleting, setDeleting]   = useState(false);
  const [toggling, setToggling]   = useState<string | null>(null);

  const [form, setForm]           = useState({
    name: "",
    description: "",
    role_key: "",
    prompt_text: "",
    is_active: true,
  });

  const headers = useAuthHeaders();
  const jsonHeaders = { ...headers, "Content-Type": "application/json" };

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sRes, dRes] = await Promise.all([
        fetch(API_BASE, { headers }),
        fetch(`${API_BASE}/defaults`, { headers }),
      ]);
      if (!sRes.ok) throw new Error("Falha ao carregar Skills.");
      setSkills(await sRes.json());
      if (dRes.ok) setDefaults(await dRes.json());
    } catch (e: any) {
      setError(e.message ?? "Erro desconhecido.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: "", description: "", role_key: "", prompt_text: "", is_active: true });
    setModal("create");
  };

  const openEdit = (skill: Skill) => {
    setEditing(skill);
    setForm({
      name:        skill.name,
      description: skill.description ?? "",
      role_key:    skill.role_key ?? "",
      prompt_text: skill.prompt_text,
      is_active:   skill.is_active,
    });
    setModal("edit");
  };

  const closeModal = () => { setModal(null); setEditing(null); };

  const handleRoleChange = (role_key: string) => {
    setForm(prev => {
      const alreadyHasContent = prev.prompt_text.trim().length > 0;
      if (alreadyHasContent) return { ...prev, role_key };
      const def = defaults.find(d => d.role_key === role_key);
      return { ...prev, role_key, prompt_text: def?.prompt_text ?? "" };
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        name:        form.name.trim(),
        description: form.description.trim() || null,
        role_key:    form.role_key || null,
        prompt_text: form.prompt_text.trim(),
        is_active:   form.is_active,
      };

      let res: Response;
      if (modal === "edit" && editing) {
        res = await fetch(`${API_BASE}/${editing.id}`, {
          method: "PUT",
          headers: jsonHeaders,
          body: JSON.stringify(payload),
        });
      } else {
        res = await fetch(API_BASE, {
          method: "POST",
          headers: jsonHeaders,
          body: JSON.stringify(payload),
        });
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Erro ${res.status}`);
      }

      closeModal();
      await fetchSkills();
    } catch (e: any) {
      alert(e.message ?? "Erro ao salvar.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const res = await fetch(`${API_BASE}/${deleteTarget.id}`, {
        method: "DELETE",
        headers,
      });
      if (!res.ok) throw new Error(`Erro ${res.status}`);
      setDelete(null);
      await fetchSkills();
    } catch (e: any) {
      alert(e.message ?? "Erro ao excluir.");
    } finally {
      setDeleting(false);
    }
  };

  const handleToggle = async (skill: Skill) => {
    setToggling(skill.id);
    try {
      const res = await fetch(`${API_BASE}/${skill.id}`, {
        method: "PUT",
        headers: jsonHeaders,
        body: JSON.stringify({ is_active: !skill.is_active }),
      });
      if (!res.ok) throw new Error(`Erro ${res.status}`);
      await fetchSkills();
    } catch (e: any) {
      alert(e.message ?? "Erro ao alterar status.");
    } finally {
      setToggling(null);
    }
  };

  const fmtDate = (iso: string | null) => {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)] flex items-center gap-2">
            <Sparkles className="w-6 h-6 text-[var(--accent-primary)]" />
            AI Skills
          </h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Gerencie prompts de sistema personalizados. Skills ativas substituem os prompts padrão no Preset IA.
          </p>
        </div>
        <button onClick={openCreate} className="btn btn-primary flex items-center gap-2">
          <Plus className="w-4 h-4" />
          Nova Skill
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="card border-[var(--color-red)] p-4 flex items-center gap-3 text-[var(--color-red)]">
          <AlertTriangle className="w-5 h-5 shrink-0" />
          <span className="text-[13px]">{error}</span>
          <button onClick={fetchSkills} className="ml-auto text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="card p-5">
              <div className="skeleton h-5 w-48 mb-3" />
              <div className="skeleton h-3 w-64 mb-2" />
              <div className="skeleton h-3 w-32" />
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && skills.length === 0 && (
        <div className="card p-12 text-center">
          <Sparkles className="w-10 h-10 text-[var(--text-tertiary)] mx-auto mb-4" />
          <h3 className="font-semibold text-[var(--text-primary)] mb-1">Nenhuma Skill cadastrada</h3>
          <p className="text-[var(--text-secondary)] text-[13px] mb-6">
            Crie uma Skill para personalizar o comportamento do Preset IA. Sem Skills, os prompts padrão são usados automaticamente.
          </p>
          <button onClick={openCreate} className="btn btn-primary mx-auto">
            <Plus className="w-4 h-4" />
            Criar primeira Skill
          </button>
        </div>
      )}

      {/* Skills list */}
      {!loading && skills.length > 0 && (
        <div className="space-y-3">
          {skills.map(skill => (
            <div
              key={skill.id}
              className={`card p-5 transition-all ${skill.is_active ? "border-[var(--accent-primary-border)]" : ""}`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1 flex-wrap">
                    <h3 className="font-semibold text-[15px] text-[var(--text-primary)] truncate">{skill.name}</h3>
                    {skill.role_label && (
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-secondary)] whitespace-nowrap">
                        {skill.role_label}
                      </span>
                    )}
                    {!skill.role_key && (
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] whitespace-nowrap">
                        Uso geral
                      </span>
                    )}
                  </div>
                  {skill.description && (
                    <p className="text-[13px] text-[var(--text-secondary)] mb-2 line-clamp-1">{skill.description}</p>
                  )}
                  <p className="text-[12px] text-[var(--text-tertiary)] font-mono line-clamp-2 bg-[var(--bg-secondary)] rounded p-2 mt-2">
                    {skill.prompt_text.slice(0, 200)}{skill.prompt_text.length > 200 ? "…" : ""}
                  </p>
                  <p className="text-[11px] text-[var(--text-tertiary)] mt-2">
                    Atualizado em {fmtDate(skill.updated_at)}
                  </p>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <div
                    className={`toggle ${skill.is_active ? "active" : ""} ${toggling === skill.id ? "opacity-50 pointer-events-none" : ""}`}
                    onClick={() => handleToggle(skill)}
                    title={skill.is_active ? "Desativar Skill" : "Ativar Skill"}
                  >
                    <div className="knob" />
                  </div>

                  <button
                    onClick={() => openEdit(skill)}
                    className="btn btn-ghost p-2"
                    title="Editar"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>

                  <button
                    onClick={() => setDelete(skill)}
                    className="btn btn-ghost p-2 text-[var(--color-red)] hover:bg-[rgba(239,68,68,0.1)]"
                    title="Excluir"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create / Edit Modal */}
      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="card w-full max-w-2xl max-h-[90vh] flex flex-col">
            {/* Modal header */}
            <div className="flex items-center justify-between p-5 border-b border-[var(--border-subtle)]">
              <h2 className="font-semibold text-[16px] text-[var(--text-primary)]">
                {modal === "edit" ? "Editar Skill" : "Nova Skill"}
              </h2>
              <button onClick={closeModal} className="btn btn-ghost p-1.5">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal body */}
            <div className="overflow-y-auto flex-1 p-5 space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">
                  Nome <span className="text-[var(--color-red)]">*</span>
                </label>
                <input
                  className="input w-full"
                  placeholder="Ex: Filtro BULL Agressivo"
                  value={form.name}
                  onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
                />
              </div>

              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">
                  Descrição
                </label>
                <input
                  className="input w-full"
                  placeholder="Breve descrição do objetivo desta Skill"
                  value={form.description}
                  onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                />
              </div>

              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">
                  Role (estágio do pipeline)
                </label>
                <div className="relative">
                  <select
                    className="input w-full appearance-none pr-8"
                    value={form.role_key}
                    onChange={e => handleRoleChange(e.target.value)}
                  >
                    {ROLE_OPTIONS.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)] pointer-events-none" />
                </div>
                {form.role_key && (
                  <p className="text-[11px] text-[var(--accent-primary)] mt-1">
                    Ao selecionar um role, o prompt padrão é pré-carregado se o campo estiver vazio.
                  </p>
                )}
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)]">
                    Prompt do sistema <span className="text-[var(--color-red)]">*</span>
                  </label>
                  {form.role_key && (
                    <button
                      type="button"
                      className="text-[11px] text-[var(--accent-primary)] hover:underline"
                      onClick={() => {
                        const def = defaults.find(d => d.role_key === form.role_key);
                        if (def) setForm(prev => ({ ...prev, prompt_text: def.prompt_text }));
                      }}
                    >
                      Restaurar padrão
                    </button>
                  )}
                </div>
                <textarea
                  className="input w-full font-mono text-[12px] resize-y"
                  rows={14}
                  placeholder="Escreva o prompt de sistema que o Claude irá receber como contexto..."
                  value={form.prompt_text}
                  onChange={e => setForm(prev => ({ ...prev, prompt_text: e.target.value }))}
                />
              </div>

              <div className="flex items-center gap-3">
                <div
                  className={`toggle ${form.is_active ? "active" : ""}`}
                  onClick={() => setForm(prev => ({ ...prev, is_active: !prev.is_active }))}
                >
                  <div className="knob" />
                </div>
                <span className="text-[13px] text-[var(--text-secondary)]">
                  {form.is_active ? "Skill ativa — será usada no Preset IA" : "Skill inativa — não será aplicada"}
                </span>
              </div>
            </div>

            {/* Modal footer */}
            <div className="flex justify-end gap-3 p-5 border-t border-[var(--border-subtle)]">
              <button onClick={closeModal} className="btn btn-ghost">Cancelar</button>
              <button
                onClick={handleSave}
                disabled={saving || !form.name.trim() || !form.prompt_text.trim()}
                className="btn btn-primary"
              >
                {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : null}
                {saving ? "Salvando…" : modal === "edit" ? "Salvar alterações" : "Criar Skill"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="card w-full max-w-md p-6">
            <div className="flex items-center gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-[var(--color-red)] shrink-0" />
              <h2 className="font-semibold text-[16px] text-[var(--text-primary)]">Excluir Skill</h2>
            </div>
            <p className="text-[13px] text-[var(--text-secondary)] mb-6">
              Tem certeza que deseja excluir a Skill <strong className="text-[var(--text-primary)]">"{deleteTarget.name}"</strong>?
              Esta ação não pode ser desfeita.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setDelete(null)} className="btn btn-ghost" disabled={deleting}>
                Cancelar
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="btn bg-[var(--color-red)] text-white hover:opacity-90"
              >
                {deleting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                {deleting ? "Excluindo…" : "Excluir"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
