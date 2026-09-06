import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const page = readFileSync(new URL('../app/watchlist/page.tsx', import.meta.url), 'utf8');
const consolidatedCard = page.slice(
  page.indexOf('function L3ConsolidatedCard'),
  page.indexOf('function PipelineTab'),
);

test('L3 consolidated renders live opportunities instead of open Shadow trades', () => {
  assert.match(page, /semantic: 'LIVE_L3_CANDIDATES'/);
  assert.match(consolidatedCard, /Favorável agora/);
  assert.match(consolidatedCard, /Shadow Portfolio/);
  assert.doesNotMatch(consolidatedCard, /shadow_id/);
  assert.doesNotMatch(consolidatedCard, /dashboard\/shadow-portfolio\//);
  assert.doesNotMatch(consolidatedCard, /Abrir trade/);
});

test('a successful watchlist read clears a transient refresh error', () => {
  const loadApproved = page.slice(
    page.indexOf('const loadApproved = useCallback'),
    page.indexOf('const loadRejected = useCallback'),
  );
  assert.match(loadApproved, /setApprovedItems/);
  assert.match(loadApproved, /setRefreshError\(null\)/);
});
