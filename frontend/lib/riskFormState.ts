export type RiskValues = Record<string, number | boolean | string>;
export type RiskDraft = { values: RiskValues; loaded: boolean; dirty: boolean };
export const emptyRiskDraft: RiskDraft = { values: {}, loaded: false, dirty: false };
export function sameRiskValues(a: RiskValues, b: RiskValues) {
  return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => a[k] === b[k]);
}
export function riskDraftReducer(state: RiskDraft, action:
  | { type: 'load'; values: RiskValues }
  | { type: 'edit'; key: string; value: RiskValues[string] }
  | { type: 'saved'; submitted: RiskValues; persisted: RiskValues }) : RiskDraft {
  if (action.type === 'edit') return { ...state, dirty: true, values: { ...state.values, [action.key]: action.value } };
  if (action.type === 'load') return state.dirty ? state : { values: action.values, loaded: true, dirty: false };
  if (!sameRiskValues(action.submitted, action.persisted) || !sameRiskValues(state.values, action.submitted)) return state;
  return { values: action.persisted, loaded: true, dirty: false };
}
