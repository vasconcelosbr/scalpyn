export interface CopilotQuery {
  id?: string;
  classification: string;
  query: string;
  query_hash?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  rows_returned: number;
  execution_ms: number;
  truncated: boolean;
}
export interface CopilotActionPlan {
  id: string;
  mode: string;
  status: string;
  objective: string;
  profile_id?: string;
  action_type: string;
  evidence: Record<string, unknown>;
  changes: Array<{ path: string; old_value: unknown; new_value: unknown; reason: string }>;
  risk?: string;
  rollback_plan: Record<string, unknown>;
  approval_required_text: string;
  execution_result?: Record<string, unknown> | null;
}

export interface CopilotSkill {
  id: string;
  name: string;
  skill_type: string;
  content: string;
  version: number;
  status: string;
  confidence?: number | null;
  source?: string | null;
  requires_approval: boolean;
  updated_at?: string | null;
}

export interface SchemaTable {
  name: string;
  primary_key?: string | null;
  important_columns: string[];
  relationships: Array<{
    target_table: string;
    source_column: string;
    target_column: string;
    type: string;
  }>;
}

export const APPROVAL_TEXT = "CONFIRMO EXECUTAR";

export function isApprovalValid(value: string): boolean {
  const normalized = value.trim().toUpperCase().replace(/\s+/g, " ");
  return normalized === APPROVAL_TEXT || normalized === "APROVADO, EXECUTAR";
}

export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
