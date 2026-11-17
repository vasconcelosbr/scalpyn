export type ApprovedOrigin = "all" | "L3" | "L3_CONSOLIDATED";
export function shadowEntrySignalLabel(signal?: string | null): string {
  if (signal === "ARMED") return "Armado";
  if (signal === "TRIGGERED") return "Gatilho";
  return "—";
}
export function shadowSourceLabel(source?: string | null): string {
  if (source === "L3_CONSOLIDATED") return "L3 Consolidado → Aprovados";
  if (source === "L3") return "L3 — origem anterior";
  return source ?? "Origem não registrada";
}
export function applyShadowSourceFilter(params: URLSearchParams, tab?: string, origin: ApprovedOrigin = "all") {
  if (tab === "L3" && origin === "all") params.set("sources", "L3,L3_CONSOLIDATED");
  else if (tab) params.set("source", tab === "L3" ? origin : tab);
}
