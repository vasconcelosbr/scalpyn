import type { Bootstrap, Candle, ConfigSchema, MTFConfig } from '../components/mtf/types';

export function targetLabel(pct: number) { return `Target${String(pct).replace('.', ',')}%`; }
export function targetPoints(candles: Candle[], pct?: number) {
  if (pct == null || !Number.isFinite(pct) || pct <= 0) return [];
  return candles.filter(c => Number.isFinite(c.close) && c.close > 0)
    .map(c => ({time: c.time, value: c.close * (1 + pct / 100)})).filter(p => Number.isFinite(p.value));
}

export function settingsDraft(config: MTFConfig | null, boot: Bootstrap): MTFConfig {
  const next = structuredClone(config ?? boot.preset);
  next.presentation = {...(next.presentation ?? boot.presentation_preset),
    target_pct: next.presentation?.target_pct ?? boot.presentation_preset.target_pct};
  if (next.presentation.fear_greed == null && boot.presentation_preset.fear_greed)
    next.presentation.fear_greed = structuredClone(boot.presentation_preset.fear_greed);
  if (next.pump_flow == null && boot.pump_flow_preset) next.pump_flow = structuredClone(boot.pump_flow_preset);
  next.force_patterns ??= boot.preset.force_patterns;
  next.rejection_patterns ??= boot.preset.rejection_patterns;
  if (next.veto_filters == null && boot.preset.veto_filters != null) next.veto_filters = structuredClone(boot.preset.veto_filters);
  return next;
}

export function configValue(config: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((value, key) => (value as Record<string, unknown> | null)?.[key], config);
}
export function withConfigValue(config: MTFConfig, path: string, value: unknown): MTFConfig {
  const next = structuredClone(config);
  const keys = path.split('.');
  let obj = next as Record<string, unknown>;
  for (const key of keys.slice(0, -1)) { obj[key] ??= {}; obj = obj[key] as Record<string, unknown>; }
  obj[keys.at(-1)!] = value;
  return next;
}

export function settingsForSave(draft: MTFConfig, current: MTFConfig | null, boot: Bootstrap): MTFConfig {
  const next = structuredClone(draft);
  const baseline = settingsDraft(current, boot);
  const canonical = (value: unknown): string => JSON.stringify(value, (_key, v) => v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))) : v);
  const engine = (c: MTFConfig) => Object.fromEntries(Object.entries(c).filter(([key])=>key!=='presentation' && key!=='pump_flow'));
  // Visual-only/no-op saves must preserve the engine identity. The first rule
  // edit freezes the inherited ATR; editing ATR itself always creates an override.
  if (next.atr_period == null && (!current || canonical(engine(next)) !== canonical(engine(baseline))))
    next.atr_period = boot.inherited_atr?.enabled ? boot.inherited_atr.period ?? '' : '';
  return next;
}

export function resolveSchema(schema: ConfigSchema, root: ConfigSchema): ConfigSchema {
  if (schema.$ref) return resolveSchema(root.$defs?.[schema.$ref.split('/').at(-1)!] ?? {}, root);
  return schema;
}
export function schemaAt(root: ConfigSchema, path: string): ConfigSchema {
  let schema = root;
  for (const key of path.split('.')) {
    schema = resolveSchema(schema, root);
    if (schema.anyOf) schema = resolveSchema(schema.anyOf.find(s => s.type !== 'null') ?? {}, root);
    schema = schema.properties?.[key] ?? (typeof schema.additionalProperties === 'object' ? schema.additionalProperties : {});
  }
  schema = resolveSchema(schema, root);
  return schema.anyOf ? resolveSchema(schema.anyOf.find(s => s.type !== 'null') ?? {}, root) : schema;
}

/** Mirror the server's published bounds, not a second set of business thresholds. */
export function validateSettings(config: MTFConfig, root: ConfigSchema): Record<string, string> {
  const errors: Record<string, string> = {};
  const visit = (value: unknown, raw: ConfigSchema, path: string) => {
    const s = resolveSchema(raw, root);
    if (s.anyOf) {
      if (value == null && s.anyOf.some(x => x.type === 'null')) return;
      return visit(value, s.anyOf.find(x => x.type !== 'null') ?? {}, path);
    }
    if (s.type === 'number' || s.type === 'integer') {
      if (typeof value !== 'number' || !Number.isFinite(value)) errors[path] = 'Informe um número válido.';
      else if (s.type === 'integer' && !Number.isInteger(value)) errors[path] = 'Informe um número inteiro.';
      else if (s.minimum != null && value < s.minimum) errors[path] = `Mínimo: ${s.minimum}.`;
      else if (s.maximum != null && value > s.maximum) errors[path] = `Máximo: ${s.maximum}.`;
      else if (s.exclusiveMinimum != null && value <= s.exclusiveMinimum) errors[path] = `Deve ser maior que ${s.exclusiveMinimum}.`;
      else if (s.exclusiveMaximum != null && value >= s.exclusiveMaximum) errors[path] = `Deve ser menor que ${s.exclusiveMaximum}.`;
    } else if (s.type === 'object') {
      if (!value || typeof value !== 'object' || Array.isArray(value)) { errors[path] = 'Objeto obrigatório.'; return; }
      for (const key of s.required ?? []) if (!(key in value)) errors[path ? `${path}.${key}` : key] = 'Campo obrigatório.';
      for (const [key, v] of Object.entries(value)) {
        const child = s.properties?.[key] ?? (typeof s.additionalProperties === 'object' ? s.additionalProperties : undefined);
        if (child) visit(v, child, path ? `${path}.${key}` : key);
        else if (s.additionalProperties === false) errors[path ? `${path}.${key}` : key] = 'Campo não reconhecido.';
      }
    } else if (s.type === 'array') {
      if (!Array.isArray(value)) errors[path] = 'Selecione uma lista válida.';
      else {
        if (s.minItems != null && value.length < s.minItems) errors[path] = 'Selecione pelo menos um padrão.';
        value.forEach((v, i) => visit(v, s.items ?? {}, `${path}.${i}`));
      }
    } else if (s.type === 'boolean' && typeof value !== 'boolean') errors[path] = 'Informe sim ou não.';
    else if (s.type === 'string' && typeof value !== 'string') errors[path] = 'Informe um texto.';
    if (s.enum && !s.enum.includes(value)) errors[path] = 'Selecione uma opção válida.';
  };
  visit(config, root, '');
  if (Object.keys(errors).length) return errors;
  const weights = config.score_weights as Record<string, number>;
  if (weights && Number(config.min_score) > Object.values(weights).reduce((s, v) => s + v, 0)) errors.min_score = 'O score mínimo excede a soma dos pesos.';
  if (Number(config.rr_bonus_threshold) < Number(config.min_rr_net)) errors.rr_bonus_threshold = 'O bônus deve ser igual ou maior que o retorno/risco mínimo.';
  if (config.atr_period != null && Number(config.atr_period) >= Math.min(...Object.values(config.candle_limits))) errors.atr_period = 'O período deve ser menor que o histórico de cada timeframe.';
  if (!(config.trigger_patterns as unknown[])?.length) errors.trigger_patterns = 'Selecione pelo menos um padrão.';
  if (config.presentation && config.presentation.retry_initial_seconds > config.presentation.retry_max_seconds) errors['presentation.retry_max_seconds'] = 'Deve ser igual ou maior que o intervalo inicial.';
  if (config.presentation?.fear_greed && config.presentation.fear_greed.max_age_seconds < config.presentation.fear_greed.refresh_seconds)
    errors['presentation.fear_greed.max_age_seconds'] = 'Deve ser igual ou maior que o intervalo de consulta.';
  if (config.schema_version === 'mtf_chart_v2') for (const tf of ['4h', '1h', '5m']) {
    const n = (config.zone_lookback_candles as Record<string, number> | null)?.[tf];
    if (n == null || n < 1 || n > config.candle_limits[tf as keyof typeof config.candle_limits]) errors[`zone_lookback_candles.${tf}`] = 'Informe uma janela dentro do histórico disponível.';
  }
  return errors;
}
