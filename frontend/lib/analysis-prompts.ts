export type AnalysisPromptVersion = {
  id: string;
  prompt_id: string;
  version_number: number;
  name: string;
  description: string | null;
  content_hash: string;
  source_type: "UPLOAD_MD" | "PASTE" | "SEEDED";
  source_filename: string | null;
  created_by: string | null;
  created_by_name: string;
  created_at: string;
  content_markdown?: string;
};

export type AnalysisPrompt = {
  id: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  current_version: AnalysisPromptVersion;
  versions?: AnalysisPromptVersion[];
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type AnalysisPromptListResponse = {
  items: AnalysisPrompt[];
  can_manage: boolean;
};

export const MAX_ANALYSIS_PROMPT_CHARACTERS = 100_000;
export const MAX_ANALYSIS_PROMPT_FILE_BYTES = 256 * 1024;
