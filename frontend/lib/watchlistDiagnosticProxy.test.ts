import assert from 'node:assert/strict';
import test from 'node:test';
import { NextRequest } from 'next/server';
import { GET } from '../app/api/[...path]/route';

test('large diagnostics stream through the proxy with private cache policy and authentication', async () => {
  const originalFetch = globalThis.fetch;
  const body = '{"large_evidence":"' + 'x'.repeat(5 * 1024 * 1024) + '"}';
  globalThis.fetch = async (_url, init) => {
    assert.equal((init?.headers as Record<string,string>).authorization, 'Bearer test-only');
    const response = new Response(body, {headers:{'content-type':'application/json','cache-control':'private, no-store','content-length':String(body.length)}});
    response.arrayBuffer = async () => { throw new Error('Must stream, not buffer diagnostics'); };
    return response;
  };
  try {
    const request = new NextRequest('https://example.test/api/watchlists/id/assets/BTC_USDT/diagnostic', {headers:{authorization:'Bearer test-only'}});
    const response = await GET(request, {params:Promise.resolve({path:['watchlists','id','assets','BTC_USDT','diagnostic']})});
    assert.equal(response.headers.get('cache-control'),'private, no-store');
    assert.equal(response.headers.get('content-length'),null);
    assert.equal(await response.text(),body);
  } finally { globalThis.fetch = originalFetch; }
});
