export interface WatchlistDecisionIdentity {
  symbol: string;
  status: string;
  stage?: string | null;
  profile_id?: string | null;
}

export function watchlistDecisionRowKey(item: WatchlistDecisionIdentity): string {
  return JSON.stringify([
    item.profile_id ?? null,
    item.stage ?? null,
    item.status,
    item.symbol,
  ]);
}
