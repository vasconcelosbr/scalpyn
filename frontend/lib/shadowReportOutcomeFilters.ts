export const SHADOW_REPORT_OUTCOMES = [
  "TP_HIT",
  "SL_HIT",
  "TRAILING_STOP",
  "TIMEOUT",
  "OPEN",
] as const;

export type ShadowReportOutcome = (typeof SHADOW_REPORT_OUTCOMES)[number];

export function toggleShadowReportOutcome(
  selected: ShadowReportOutcome[],
  outcome: ShadowReportOutcome,
): ShadowReportOutcome[] {
  const next = selected.includes(outcome)
    ? selected.filter((item) => item !== outcome)
    : [...selected, outcome];

  return SHADOW_REPORT_OUTCOMES.filter((item) => next.includes(item));
}

export function shadowReportSelectionKey({
  sources,
  outcomes,
  dateFrom,
  dateTo,
  watchlistIds,
  profileIds,
  includeLegacy,
}: {
  sources: string[];
  outcomes: ShadowReportOutcome[];
  dateFrom: string;
  dateTo: string;
  watchlistIds: string[];
  profileIds: string[];
  includeLegacy: boolean;
}): string {
  return JSON.stringify({
    sources: [...sources].sort(),
    outcomes: [...outcomes].sort(),
    date_from: dateFrom,
    date_to: dateTo,
    watchlist_ids: [...watchlistIds].sort(),
    profile_ids: [...profileIds].sort(),
    include_legacy_watchlist: includeLegacy,
  });
}
