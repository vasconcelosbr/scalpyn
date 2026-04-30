/**
 * Helpers for normalizing the `/api/{spot|futures}-engine/status` payload.
 *
 * Why this module exists
 * ----------------------
 * The backend returns the `positions` field as a dict (a summary object),
 * but the frontend originally treated it as an array and called `.filter` on
 * it during render — which crashed the whole page with a TypeError, surfacing
 * as the production "Application error: a client-side exception" overlay
 * (Task #127).
 *
 * The two real backend shapes today are:
 *   - Spot   `/status` → `positions = { total, active, underwater,
 *                                       unrealized_pnl_usdt }`
 *                        (no underlying list — counts only)
 *   - Futures `/status` → `positions = { open_count, positions: [...],
 *                                        total_unrealized_pnl }`
 *                         (the actual list lives at `.positions`)
 *
 * Plus three legacy / edge shapes we want to keep tolerating:
 *   - `[]` (empty array — current default before SWR returns)
 *   - `[ { ... } ]` (legacy "real array")
 *   - `{ error: "…" }` (downstream fetch failure inside the status endpoint)
 *   - `null` / `undefined` (fetch failed, status not loaded yet)
 *
 * Centralizing this normalization means EngineStatusBar can safely call
 * array methods on the result without ever crashing render.
 */

export type RawPositions = unknown;
export type PositionRecord = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Extract a positions array from any of the supported `positions` shapes.
 * Always returns an array — never throws.
 */
export function extractPositions(raw: RawPositions): PositionRecord[] {
  if (Array.isArray(raw)) return raw as PositionRecord[];
  if (isRecord(raw) && Array.isArray(raw.positions)) {
    return raw.positions as PositionRecord[];
  }
  return [];
}

/**
 * Extract the summary dict from a positions payload, if one exists.
 *
 * Returns the original dict when the payload is dict-shaped (so callers can
 * read precomputed fields like `total`, `active`, `underwater`, `open_count`,
 * `total_unrealized_pnl`). Returns `null` when the payload was already an
 * array (no summary to surface) or when it's missing.
 */
export function extractPositionsSummary(
  raw: RawPositions
): Record<string, unknown> | null {
  return isRecord(raw) ? raw : null;
}

function pickFiniteNumber(
  source: Record<string, unknown> | null,
  keys: readonly string[]
): number | null {
  if (!source) return null;
  for (const key of keys) {
    const value = source[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

/**
 * Pick the "active positions" count for display, preferring the
 * server-precomputed value over a fallback array length.
 */
export function pickActivePositionsCount(
  summary: Record<string, unknown> | null,
  positions: PositionRecord[]
): number {
  const fromSummary = pickFiniteNumber(summary, ['open_count', 'total', 'active']);
  if (fromSummary !== null) return fromSummary;
  return positions.length;
}

/**
 * Pick the "underwater" count for spot, preferring the server-precomputed
 * value over computing from the array.
 */
export function pickUnderwaterCount(
  summary: Record<string, unknown> | null,
  positions: PositionRecord[]
): number {
  const fromSummary = pickFiniteNumber(summary, ['underwater']);
  if (fromSummary !== null) return fromSummary;
  return positions.filter((p) => {
    const pnl = p?.unrealized_pnl;
    return typeof pnl === 'number' && pnl < 0;
  }).length;
}
