/**
 * Scalpyn Service Worker — offline fallback + static asset caching.
 * Strategy: network-first for API calls, cache-first for static assets.
 */

const CACHE_NAME = 'scalpyn-v1';
const OFFLINE_URL = '/offline.html';

const PRECACHE_URLS = [
  '/',
  '/manifest.json',
  '/icon-192.svg',
  '/icon-512.svg',
];

// ── Install: precache static shell ───────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(PRECACHE_URLS).catch(() => {})
    ).then(() => self.skipWaiting())
  );
});

// ── Activate: clean old caches ───────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first, fall back to cache ─────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET, cross-origin, and API requests (always network-first)
  if (
    request.method !== 'GET' ||
    url.origin !== location.origin ||
    url.pathname.startsWith('/api/')
  ) {
    return;
  }

  event.respondWith(
    fetch(request)
      .then((response) => {
        // Cache successful navigation responses
        if (response.ok && request.mode === 'navigate') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return response;
      })
      .catch(async () => {
        // Offline: try cache, then show offline page
        const cached = await caches.match(request);
        if (cached) return cached;
        if (request.mode === 'navigate') {
          return (
            (await caches.match('/')) ||
            new Response(
              `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Scalpyn — Offline</title>
              <meta name="viewport" content="width=device-width,initial-scale=1">
              <style>body{font-family:system-ui;background:#06070A;color:#E8ECF4;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column;gap:16px}h1{font-size:20px;font-weight:700;margin:0}p{color:#8B92A5;font-size:14px;margin:0;text-align:center}</style>
              </head><body><h1>Scalpyn</h1><p>You're offline.<br>Please check your connection.</p></body></html>`,
              { headers: { 'Content-Type': 'text/html' } }
            )
          );
        }
        return new Response('', { status: 503 });
      })
  );
});
