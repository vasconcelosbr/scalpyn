/**
 * Helpers for normalizing the `/api/{spot|futures}-engine/status` payload.
 *
 * Why this module exists
 * ----------------------
 * The backend returns the `positions` field as a **dict** (a summary object),
 * but the frontend originally treated it as an array and called `.filter` on
 * it during render — which crashed the whole page with a TypeError, surfacing
 * as the production "Application error: a client-side exception" overlay
 * (Task #127).
 *
 * The two real backend shapes today are:
 *
 *   - Spot   `/status` → `positions = { total, active, underwater,
 *                                       unrealized_pnl_usdt }`
 *                        (no underlying list — counts only)
 *
 *   - Futures `/status` → `positions = { open_count, positions: [...],
 *                                        total_unrealized_pnl }`
 *                         (the actual list lives at `.positions`)
 *
 * Plus three legacy / edge shapes we want to keep tolerating:
 *
 *   - `[]` (empty array — current default before SWR returns)
 *   - `[ { ... } ]` (legacy "real array" if a future backend rewrites to that)
 *   - `{ error: "…" }` (downstream fetch failure inside the status endpoint)
 *   - `null` / `undefined` (fetch failed, status not loaded yet)
 *
 * Centralizing this normalization means EngineStatusBar can safely call
 * array methods on the result without ever crashing render.
 */

export type RawPositions = unknown;
export type PositionRecord = Record<string, any>;

/**
 * Extract a positions array from any of the supported `positions` shapes.
 * Always returns an array — never throws.
 */
export function extractPositions(raw: RawPositions): PositionRecord[] {
  if (Array.isArray(raw)) return raw as PositionRecord[];
  if (raw && typeof raw === 'object' && Array.isArray((raw as any).positions)) {
    return (raw as any).positions as PositionRecord[];
  }
  return [];
}

/**
 * Extract the summary dict from a positions payload, if one exists.
 *
 * Returns the original dict when the payload is dict-shaped (so callers can
 * read precomputed fields like `total`, `active`, `underwater`, `open_count`,
 * `total_unrealized_pnl`). Returns `null` when the payload was already an
 * array (no summary to surface) or when it's missing/empty.
 */
export function extractPositionsSummary(
  raw: RawPositions
): Record<string, any> | null {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return raw as Record<string, any>;
  }
  return null;
}

/**
 * Pick the "active positions" count for display, preferring the
 * server-precomputed value over a fallback array length.
 */
export function pickActivePositionsCount(
  summary: Record<string, any> | null,
  positions: PositionRecord[]
): number {
  if (summary) {
    const candidates = [
      summary.open_count,
      summary.total,
      summary.active,
    ];
    for (const c of candidates) {
      if (typeof c === 'number' && Number.isFinite(c)) return c;
    }
  }
  return positions.length;
}

/**
 * Pick the "underwater" count for spot, preferring the server-precomputed
 * value over computing from the array.
 */
export function pickUnderwaterCount(
  summary: Record<string, any> | null,
  positions: PositionRecord[]
): number {
  if (summary && typeof summary.underwater === 'number' && Number.isFinite(summary.underwater)) {
    return summary.underwater;
  }
  return positions.filter(
    (p) => p && p.unrealized_pnl != null && p.unrealized_pnl < 0
  ).length;
}

// ─── Dev-only sanity assertions ──────────────────────────────────────────────
// Keep these here so a future refactor can't silently break the contract.
// They run once at import time in development; production builds skip them.
if (process.env.NODE_ENV !== 'production') {
  const empty = extractPositions(undefined);
  if (!Array.isArray(empty) || empty.length !== 0) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositions(undefined) failed sanity check');
  }
  const arr = extractPositions([{ symbol: 'BTC' }]);
  if (!Array.isArray(arr) || arr.length !== 1) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositions(array) failed sanity check');
  }
  const futuresLike = extractPositions({
    open_count: 0,
    positions: [{ symbol: 'ETH' }],
    total_unrealized_pnl: 0,
  });
  if (!Array.isArray(futuresLike) || futuresLike.length !== 1) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositions(futures-dict) failed sanity check');
  }
  const spotLike = extractPositions({
    total: 3,
    active: 2,
    underwater: 1,
    unrealized_pnl_usdt: 0,
  });
  if (!Array.isArray(spotLike) || spotLike.length !== 0) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositions(spot-dict) failed sanity check');
  }
  const errLike = extractPositions({ error: 'boom' });
  if (!Array.isArray(errLike) || errLike.length !== 0) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositions(error-dict) failed sanity check');
  }
  const summary = extractPositionsSummary({
    total: 3,
    active: 2,
    underwater: 1,
    unrealized_pnl_usdt: 0,
  });
  if (!summary || summary.total !== 3 || summary.underwater !== 1) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositionsSummary(spot-dict) failed sanity check');
  }
  if (extractPositionsSummary([]) !== null || extractPositionsSummary(null) !== null) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] extractPositionsSummary(array|null) should return null');
  }
  if (pickActivePositionsCount({ total: 5 }, []) !== 5) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] pickActivePositionsCount summary preference failed');
  }
  if (pickActivePositionsCount(null, [{ a: 1 }, { b: 2 }]) !== 2) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] pickActivePositionsCount fallback length failed');
  }
  if (pickUnderwaterCount({ underwater: 7 }, []) !== 7) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] pickUnderwaterCount summary preference failed');
  }
  if (
    pickUnderwaterCount(null, [
      { unrealized_pnl: -1 },
      { unrealized_pnl: 5 },
      { unrealized_pnl: -0.1 },
    ]) !== 2
  ) {
    // eslint-disable-next-line no-console
    console.error('[engineStatus] pickUnderwaterCount fallback compute failed');
  }
}
