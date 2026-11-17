import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const page = readFileSync(new URL('../app/watchlist/page.tsx', import.meta.url), 'utf8');
const consolidatedCard = page.slice(
  page.indexOf('function L3ConsolidatedCard'),
  page.indexOf('function PipelineTab'),
);

test('L3 consolidated renders profile approvals separately from data and execution', () => {
  assert.match(page, /semantic: 'PROFILE_DISPLAY_DATA_SEPARATION'/);
  assert.match(consolidatedCard, /watchlists\/l3-consolidated\/profile-status/);
  assert.match(consolidatedCard, /Aprovado no perfil/);
  assert.match(consolidatedCard, /Aguardando dados/);
  assert.match(consolidatedCard, /data\?\.awaiting_data_items/);
  assert.match(consolidatedCard, /setData\(null\)/);
  assert.doesNotMatch(consolidatedCard, /authorization_status|shadow_status|expires_at|visible_until/);
  assert.doesNotMatch(consolidatedCard, /Aprovado para execução|Abrir trade/);
});

test('a successful watchlist read clears a transient refresh error', () => {
  const loadApproved = page.slice(
    page.indexOf('const loadApproved = useCallback'),
    page.indexOf('const loadRejected = useCallback'),
  );
  assert.match(loadApproved, /setApprovedItems/);
  assert.match(loadApproved, /setRefreshError\(null\)/);
});
