export type TrailingLevel = { price: number | null; pct: number | null; at: string | null };
export type TrailingRegime = { label: string | null; original: string | null; quality: string; source: string | null; timeframe: string | null; at: string | null };
export type ShadowTrailingView = {
  state: string; mode: string; origin: string | null; entry: TrailingLevel;
  activation: TrailingLevel; observed: TrailingLevel; maximum: TrailingLevel;
  floor: TrailingLevel; pending_floor: TrailingLevel; trigger: TrailingLevel;
  next_step: TrailingLevel; next_step_floor: TrailingLevel;
  remaining_pp: number | null; distance_to_floor_pp: number | null;
  pending_reason: string | null; quality: string; last_evaluated_at: string | null;
  policy_version: string | null; asset_regime: TrailingRegime; market_regime: TrailingRegime;
};
export const trailingStates: Record<string,string> = { WAITING:'Aguardando ativação', ACTIVE:'Trailing ativo', TIGHTENED:'Proteção apertada', EXIT_PENDING:'Saída pendente', OBSERVATION:'Observação', DISABLED:'Sem trailing', UNAVAILABLE:'Trailing não confirmado', CLOSED:'Encerrado' };
export const trailingReasons: Record<string,string> = { STALE_QUOTE:'Cotação desatualizada; distância ao gatilho indisponível', WAITING_TRIGGER:'Aguardando o gatilho de preço', WAITING_EVALUATION:'Aguardando avaliação', WAITING_FLOW_PRICE_CONFIRMATION:'Aguardando confirmação de fluxo e preço', DATA_UNAVAILABLE:'Aguardando dados válidos', LEGACY_STATE_NOT_RECORDED:'Contrato anterior sem registro do piso vigente', DATA_DEGRADED:'Dados degradados; proteção anterior preservada', CONTINUATION:'Continuação após TP', FLOW_STRUCTURE_EXIT:'Fluxo enfraquecido e perda de estrutura', NEW_FLOOR_BELOW_MARKET:'Saída aguardando preço executável', WAITING_TP:'Aguardando TP' };
export function trailingPercent(n: number | null | undefined, unit = '%') { return n == null ? 'Não disponível' : `${n>0?'+':''}${n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})}${unit}`; }
export function trailingSummary(v: ShadowTrailingView) {
  const title = trailingStates[v.state] ?? 'Não disponível';
  if (v.mode !== 'OBSERVE' && v.state !== 'CLOSED' && v.floor.pct != null) return `${title} · Piso ${trailingPercent(v.floor.pct)}`;
  if (v.remaining_pp != null && v.remaining_pp > 0) return `${title} · Faltam ${trailingPercent(v.remaining_pp,' p.p.')}`;
  return title;
}
