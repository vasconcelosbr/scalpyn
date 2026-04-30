/**
 * Normalisers for the `/api/{spot|futures}-engine/status` payload.
 *
 * Backend returns `positions` as either a summary dict (spot, futures) or a
 * plain array; these helpers always yield an array so consumers never crash
 * on `.filter`. See `runEngineStatusAssertions` for the full shape matrix.
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

/**
 * Pure regression helper — returns failure messages (empty = all good).
 * Used by `lib/__tests__/engineStatus.test.ts`. Never invoked at import.
 */
export function runEngineStatusAssertions(): string[] {
  const failures: string[] = [];

  // Shape 1: missing
  const empty = extractPositions(undefined);
  if (!Array.isArray(empty) || empty.length !== 0) {
    failures.push('extractPositions(undefined) should return []');
  }

  // Shape 2: real array
  const arr = extractPositions([{ symbol: 'BTC' }]);
  if (!Array.isArray(arr) || arr.length !== 1) {
    failures.push('extractPositions(array) should return the array');
  }

  // Shape 3: futures dict (list nested at .positions)
  const futuresLike = extractPositions({
    open_count: 0,
    positions: [{ symbol: 'ETH' }],
    total_unrealized_pnl: 0,
  });
  if (!Array.isArray(futuresLike) || futuresLike.length !== 1) {
    failures.push('extractPositions(futures-dict) should return inner positions');
  }

  // Shape 4: spot summary dict (no list)
  const spotLike = extractPositions({
    total: 3,
    active: 2,
    underwater: 1,
    unrealized_pnl_usdt: 0,
  });
  if (!Array.isArray(spotLike) || spotLike.length !== 0) {
    failures.push('extractPositions(spot-dict) should return []');
  }

  // Shape 5: error dict
  const errLike = extractPositions({ error: 'boom' });
  if (!Array.isArray(errLike) || errLike.length !== 0) {
    failures.push('extractPositions(error-dict) should return []');
  }

  // extractPositionsSummary
  const summary = extractPositionsSummary({ total: 3, active: 2, underwater: 1 });
  if (!summary || summary.total !== 3 || summary.underwater !== 1) {
    failures.push('extractPositionsSummary(spot-dict) should preserve dict');
  }
  if (extractPositionsSummary([]) !== null || extractPositionsSummary(null) !== null) {
    failures.push('extractPositionsSummary(array|null) should return null');
  }

  // pickActivePositionsCount
  if (pickActivePositionsCount({ total: 5 }, []) !== 5) {
    failures.push('pickActivePositionsCount should prefer summary.total');
  }
  if (pickActivePositionsCount({ open_count: 4 }, []) !== 4) {
    failures.push('pickActivePositionsCount should prefer summary.open_count');
  }
  if (pickActivePositionsCount(null, [{ a: 1 }, { b: 2 }]) !== 2) {
    failures.push('pickActivePositionsCount should fall back to array length');
  }

  // pickUnderwaterCount
  if (pickUnderwaterCount({ underwater: 7 }, []) !== 7) {
    failures.push('pickUnderwaterCount should prefer summary.underwater');
  }
  const computed = pickUnderwaterCount(null, [
    { unrealized_pnl: -1 },
    { unrealized_pnl: 5 },
    { unrealized_pnl: -0.1 },
  ]);
  if (computed !== 2) {
    failures.push(`pickUnderwaterCount fallback should compute 2, got ${computed}`);
  }

  return failures;
}
