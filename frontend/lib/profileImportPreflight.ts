import {
  BREAKOUT_REFERENCE_WINDOWS,
  STRATEGY_PROFILE_INDICATOR_MAP,
  type IndicatorKind,
  type StrategyProfileSection,
} from "./indicatorCatalog";

export interface ImportPreflightIssue {
  code: string;
  path: string;
  message: string;
}

export interface ImportPreflightResult {
  valid: boolean;
  issues: ImportPreflightIssue[];
}

const VALID_TIMEFRAMES = new Set(["1m", "3m", "5m", "15m", "1h"]);
const VALID_FUNNEL_ROLES = new Set(["universe_filter", "primary_filter", "score_engine", "acquisition_queue"]);
const NUMERIC_OPERATORS = new Set([">", ">=", "<", "<=", "=", "==", "!=", "between"]);
const BOOLEAN_OPERATORS = new Set(["is_true", "is_false", "=", "==", "!="]);
const STRING_OPERATORS = new Set(["=", "==", "!="]);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH_RE = /^[0-9a-f]{64}$/i;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

function issue(code: string, path: string, message: string): ImportPreflightIssue {
  return { code, path, message };
}

function validateIndicator(
  rawId: unknown,
  section: StrategyProfileSection,
  path: string,
  condition: Record<string, unknown>,
): ImportPreflightIssue[] {
  const id = typeof rawId === "string" ? rawId.trim() : "";
  if (!id) return [issue("INDICATOR_REQUIRED", path, "indicador é obrigatório")];
  const contract = STRATEGY_PROFILE_INDICATOR_MAP.get(id);
  if (!contract) {
    return [issue("UNKNOWN_INDICATOR", path, `indicador não suportado: ${id}`)];
  }
  const issues: ImportPreflightIssue[] = [];
  if (!contract.sections.includes(section)) {
    issues.push(issue("INDICATOR_SECTION_NOT_ALLOWED", path, `${id} não é permitido em ${section}`));
  }
  if (contract.requiresReferenceWindow) {
    const referenceWindow = condition.reference_window;
    if (!BREAKOUT_REFERENCE_WINDOWS.includes(referenceWindow as typeof BREAKOUT_REFERENCE_WINDOWS[number])) {
      issues.push(issue(
        "REFERENCE_WINDOW_REQUIRED",
        `${path.replace(/\.(field|indicator|left|right)$/, "")}.reference_window`,
        `${id} exige reference_window: ${BREAKOUT_REFERENCE_WINDOWS.join(", ")}`,
      ));
    }
  }
  if (contract.fixedPeriod !== undefined && condition.period !== undefined && condition.period !== contract.fixedPeriod) {
    issues.push(issue(
      "INDICATOR_PERIOD_INVALID",
      `${path.replace(/\.(field|indicator|left|right)$/, "")}.period`,
      `${id} exige período ${contract.fixedPeriod}`,
    ));
  }
  return issues;
}

function allowedOperators(kind: IndicatorKind): Set<string> {
  if (kind === "boolean") return BOOLEAN_OPERATORS;
  if (kind === "string") return STRING_OPERATORS;
  return NUMERIC_OPERATORS;
}

function validateCondition(
  value: unknown,
  section: StrategyProfileSection,
  path: string,
): ImportPreflightIssue[] {
  if (!isRecord(value)) return [issue("CONDITION_OBJECT_REQUIRED", path, "condição deve ser um objeto")];
  const issues: ImportPreflightIssue[] = [];
  const isComparison = value.type === "comparison" || Boolean(value.left || value.right);
  const operator = typeof value.operator === "string" ? value.operator.trim() : "";

  if (isComparison) {
    issues.push(...validateIndicator(value.left, section, `${path}.left`, value));
    if (operator !== "between") {
      issues.push(...validateIndicator(value.right, section, `${path}.right`, value));
    }
    if (!NUMERIC_OPERATORS.has(operator)) {
      issues.push(issue("OPERATOR_INVALID", `${path}.operator`, `operador inválido para comparação: ${operator || "<vazio>"}`));
    }
  } else {
    const indicatorKey = Object.prototype.hasOwnProperty.call(value, "field") ? "field" : "indicator";
    const rawId = value[indicatorKey];
    issues.push(...validateIndicator(rawId, section, `${path}.${indicatorKey}`, value));
    const id = typeof rawId === "string" ? rawId.trim() : "";
    const kind = STRATEGY_PROFILE_INDICATOR_MAP.get(id)?.kind || "number";
    if (!allowedOperators(kind).has(operator)) {
      issues.push(issue("OPERATOR_INVALID", `${path}.operator`, `operador inválido para ${kind}: ${operator || "<vazio>"}`));
    }
    if (operator === "between") {
      if (!isFiniteNumber(value.min) || !isFiniteNumber(value.max)) {
        issues.push(issue("RANGE_REQUIRED", path, "operador between exige min e max numéricos"));
      } else if (value.min > value.max) {
        issues.push(issue("RANGE_INVALID", path, "min deve ser menor ou igual a max"));
      }
    } else if (kind === "number" && !isFiniteNumber(value.value)) {
      issues.push(issue("VALUE_REQUIRED", `${path}.value`, "valor numérico é obrigatório"));
    } else if (kind === "string" && typeof value.value !== "string") {
      issues.push(issue("VALUE_REQUIRED", `${path}.value`, "valor textual é obrigatório"));
    }
  }

  if (value.period !== undefined && (!Number.isInteger(value.period) || Number(value.period) <= 0)) {
    issues.push(issue("PERIOD_INVALID", `${path}.period`, "period deve ser inteiro positivo"));
  }
  if (value.timeframe !== undefined && !VALID_TIMEFRAMES.has(String(value.timeframe))) {
    issues.push(issue("TIMEFRAME_INVALID", `${path}.timeframe`, `timeframe inválido: ${String(value.timeframe)}`));
  }
  for (const flag of ["required", "enabled"] as const) {
    if (value[flag] !== undefined && typeof value[flag] !== "boolean") {
      issues.push(issue("BOOLEAN_FIELD_INVALID", `${path}.${flag}`, `${flag} deve ser booleano`));
    }
  }
  return issues;
}

function validateConditionArray(
  sectionValue: unknown,
  section: Exclude<StrategyProfileSection, "block_rules">,
  path: string,
  required: boolean,
): ImportPreflightIssue[] {
  if (sectionValue === undefined && !required) return [];
  if (!isRecord(sectionValue)) return [issue("SECTION_OBJECT_REQUIRED", path, "seção deve ser um objeto")];
  if (!Array.isArray(sectionValue.conditions)) {
    return [issue("CONDITIONS_ARRAY_REQUIRED", `${path}.conditions`, "conditions deve ser um array")];
  }
  return sectionValue.conditions.flatMap((condition, index) =>
    validateCondition(condition, section, `${path}.conditions[${index}]`));
}

function validateBlocks(sectionValue: unknown, path: string, required: boolean): ImportPreflightIssue[] {
  if (sectionValue === undefined && !required) return [];
  if (!isRecord(sectionValue)) return [issue("SECTION_OBJECT_REQUIRED", path, "seção deve ser um objeto")];
  if (!Array.isArray(sectionValue.blocks)) {
    return [issue("BLOCKS_ARRAY_REQUIRED", `${path}.blocks`, "blocks deve ser um array")];
  }
  return sectionValue.blocks.flatMap((block, blockIndex) => {
    const blockPath = `${path}.blocks[${blockIndex}]`;
    if (!isRecord(block)) return [issue("BLOCK_OBJECT_REQUIRED", blockPath, "bloco deve ser um objeto")];
    if (Array.isArray(block.conditions)) {
      return block.conditions.flatMap((condition, conditionIndex) =>
        validateCondition(condition, "block_rules", `${blockPath}.conditions[${conditionIndex}]`));
    }
    if (block.indicator || block.field || block.left || block.right) {
      return validateCondition(block, "block_rules", blockPath);
    }
    return [issue("CONDITIONS_ARRAY_REQUIRED", `${blockPath}.conditions`, "conditions deve ser um array")];
  });
}

export function validateExecutionSections(
  profile: Record<string, unknown>,
  path = "profile",
  required = false,
): ImportPreflightIssue[] {
  return [
    ...validateConditionArray(profile.filters, "filters", `${path}.filters`, required),
    ...validateConditionArray(profile.signals, "signals", `${path}.signals`, required),
    ...validateBlocks(profile.block_rules, `${path}.block_rules`, required),
    ...validateConditionArray(profile.entry_triggers, "entry_triggers", `${path}.entry_triggers`, required),
  ];
}

export function validateProfileImport(
  profileValue: unknown,
  index: number,
  updateIndicatorsOnly = false,
): ImportPreflightResult {
  const path = `profiles[${index}]`;
  if (!isRecord(profileValue)) {
    const issues = [issue("PROFILE_OBJECT_REQUIRED", path, "profile deve ser um objeto")];
    return { valid: false, issues };
  }
  const profile = profileValue;
  const issues: ImportPreflightIssue[] = [];
  if (updateIndicatorsOnly) {
    const profileId = profile.profile_id || profile.id;
    if (!profileId) issues.push(issue("PROFILE_ID_REQUIRED", `${path}.profile_id`, "profile_id é obrigatório"));
    else if (!UUID_RE.test(String(profileId))) issues.push(issue("PROFILE_ID_INVALID", `${path}.profile_id`, "profile_id deve ser UUID"));

    if (!profile.expected_profile_version_id) {
      issues.push(issue("EXPECTED_PROFILE_VERSION_REQUIRED", `${path}.expected_profile_version_id`, "expected_profile_version_id é obrigatório"));
    } else if (!UUID_RE.test(String(profile.expected_profile_version_id))) {
      issues.push(issue("EXPECTED_PROFILE_VERSION_INVALID", `${path}.expected_profile_version_id`, "expected_profile_version_id deve ser UUID"));
    }
    if (!profile.expected_profile_config_hash) {
      issues.push(issue("EXPECTED_PROFILE_CONFIG_HASH_REQUIRED", `${path}.expected_profile_config_hash`, "expected_profile_config_hash é obrigatório"));
    } else if (!HASH_RE.test(String(profile.expected_profile_config_hash))) {
      issues.push(issue("EXPECTED_PROFILE_CONFIG_HASH_INVALID", `${path}.expected_profile_config_hash`, "expected_profile_config_hash deve ter 64 caracteres hexadecimais"));
    }
  } else if (typeof profile.name !== "string" || !profile.name.trim()) {
    issues.push(issue("PROFILE_NAME_REQUIRED", `${path}.name`, "name é obrigatório"));
  }

  if (profile.default_timeframe !== undefined && !VALID_TIMEFRAMES.has(String(profile.default_timeframe))) {
    issues.push(issue("TIMEFRAME_INVALID", `${path}.default_timeframe`, `timeframe inválido: ${String(profile.default_timeframe)}`));
  }
  if (profile.funnel_role !== undefined && !VALID_FUNNEL_ROLES.has(String(profile.funnel_role))) {
    issues.push(issue("FUNNEL_ROLE_INVALID", `${path}.funnel_role`, `funnel_role inválido: ${String(profile.funnel_role)}`));
  }
  issues.push(...validateExecutionSections(profile, path, updateIndicatorsOnly));
  return { valid: issues.length === 0, issues };
}

export function formatPreflightIssue(value: ImportPreflightIssue): string {
  return `${value.code} · ${value.path}: ${value.message}`;
}

export function profilesEligibleForSubmission<T extends { valid: boolean }>(
  profiles: T[],
  updateIndicatorsOnly: boolean,
): T[] {
  if (updateIndicatorsOnly && profiles.some((profile) => !profile.valid)) return [];
  return profiles.filter((profile) => profile.valid);
}
