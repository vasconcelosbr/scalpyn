"use client";

import type { CopilotSkill } from "@/lib/copilot";

export function CopilotSkillLibrary({ skills, onToggle, onApprove }: {
  skills: CopilotSkill[];
  onToggle: (skill: CopilotSkill) => Promise<void>;
  onApprove: (skill: CopilotSkill) => Promise<void>;
}) {
  return (
    <section className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <h2 className="mb-3 text-sm font-semibold">Skill Library</h2>
      <div className="space-y-2">
        {skills.length === 0 && <p className="text-xs text-[var(--text-muted)]">Nenhuma skill operacional salva.</p>}
        {skills.map((skill) => (
          <article key={skill.id} className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 text-xs">
            <div className="flex items-start justify-between gap-3">
              <div><strong>{skill.name}</strong><p className="mt-1 font-mono text-[var(--text-muted)]">{skill.skill_type} · v{skill.version} · {skill.status}</p></div>
              <div className="flex gap-2">
                {skill.status === "PENDING_APPROVAL" && <button type="button" onClick={() => onApprove(skill)} className="rounded bg-amber-400 px-2 py-1 font-semibold text-black">Aprovar</button>}
                {skill.status !== "PENDING_APPROVAL" && <button type="button" onClick={() => onToggle(skill)} className="rounded border border-[var(--border-subtle)] px-2 py-1">{skill.status === "ACTIVE" ? "Desativar" : "Ativar"}</button>}
              </div>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-[var(--text-secondary)]">{skill.content}</p>
            <p className="mt-2 text-[var(--text-muted)]">Confiança: {skill.confidence ?? "—"} · Origem: {skill.source ?? "—"}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
