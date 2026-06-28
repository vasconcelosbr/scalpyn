/**
 * Next.js catch-all API Route Proxy
 * Forwards all /api/* requests to the FastAPI backend (Cloud Run).
 *
 * Compatible with Next.js 15 where route handler params are async (Promise).
 */
import { NextRequest, NextResponse } from 'next/server';

// BACKEND_URL is the base URL of the backend service.
// It may or may not include a trailing /api — both forms are handled correctly.
// Example: https://scalpyn-api-xxx.a.run.app  OR  https://scalpyn-api-xxx.a.run.app/api
const BACKEND_ROOT = (process.env.BACKEND_URL ?? 'http://localhost:8000')
  .replace(/\/$/, '')    // strip trailing slash
  .replace(/\/api$/, ''); // strip /api suffix if present so we never double it

type RouteContext = { params: Promise<{ path: string[] }> };

// Routes that require trailing slash for FastAPI
const COLLECTION_ROUTES = ['/api/pools', '/api/trades', '/api/orders', '/api/exchanges', '/api/watchlist', '/api/watchlists', '/api/profiles', '/api/custom-watchlists'];

async function proxyRequest(req: NextRequest, context: RouteContext): Promise<NextResponse> {
    // Await params (required in Next.js 15+)
  await context.params;

  // Forward the full /api/... path
  let rawPath = req.nextUrl.pathname;

  // Add trailing slash for collection routes (FastAPI requires it)
  if (COLLECTION_ROUTES.includes(rawPath) && !rawPath.endsWith('/')) {
    rawPath = rawPath + '/';
  }

  const search = req.nextUrl.search ?? '';
  const targetUrl = `${BACKEND_ROOT}${rawPath}${search}`;

  // Forward all headers except host and connection (which confuse the backend).
  // accept-encoding is intentionally kept so the backend can return gzip responses;
  // content-encoding is stripped from the *response* below to let Next.js handle it.
  const forwardHeaders: Record<string, string> = {};
  req.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (lower !== 'host' && lower !== 'connection') {
      forwardHeaders[key] = value;
    }
  });
  // Ensure we always advertise gzip support upstream, even if the client didn't.
  if (!forwardHeaders['accept-encoding']) {
    forwardHeaders['accept-encoding'] = 'gzip, deflate, br';
  }

  const hasBody = req.method !== 'GET' && req.method !== 'HEAD';
  const body = hasBody ? await req.arrayBuffer() : undefined;

  const startedAt = Date.now();

  let backendRes: Response;
  try {
    backendRes = await fetch(targetUrl, {
      method: req.method,
      headers: forwardHeaders,
      body: body ? Buffer.from(body) : undefined,
      redirect: 'follow',
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    // eslint-disable-next-line no-console
    console.error(
      `[api-proxy] upstream fetch failed method=${req.method} path=${rawPath} elapsed=${Date.now() - startedAt}ms err=${message}`,
    );
    return NextResponse.json(
      { detail: 'Upstream API unreachable' },
      { status: 502 },
    );
  }

  if (!backendRes.ok) {
    // eslint-disable-next-line no-console
    console.warn(
      `[api-proxy] upstream non-2xx method=${req.method} path=${rawPath} status=${backendRes.status} elapsed=${Date.now() - startedAt}ms`,
    );
  }

  // Forward response headers back to client.
  // Strip content-encoding so Next.js / the browser re-handles decompression
  // correctly (Next.js fetch already decompresses gzip from the backend).
  // Strip transfer-encoding as it does not apply to HTTP/2 responses.
  const resHeaders = new Headers();
  backendRes.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (lower !== 'transfer-encoding' && lower !== 'content-encoding') {
      resHeaders.set(key, value);
    }
  });

  // Add private cache hints for GET responses that don't already have them.
  // stale-while-revalidate=5 allows the browser to serve a cached response
  // while a background refresh runs, eliminating perceived latency on polling.
  if (req.method === 'GET' && backendRes.ok && !resHeaders.has('cache-control')) {
    resHeaders.set('cache-control', 'private, max-age=0, stale-while-revalidate=5');
  }

  if (
    backendRes.headers.get("content-type")?.includes("text/event-stream")
  ) {
    return new NextResponse(backendRes.body, {
      status: backendRes.status,
      headers: resHeaders,
    });
  }

  const responseBody = await backendRes.arrayBuffer();

  return new NextResponse(responseBody, {
    status: backendRes.status,
    headers: resHeaders,
  });
}

export async function GET(req: NextRequest, context: RouteContext) {
    return proxyRequest(req, context);
}
export async function POST(req: NextRequest, context: RouteContext) {
    return proxyRequest(req, context);
}
export async function PUT(req: NextRequest, context: RouteContext) {
    return proxyRequest(req, context);
}
export async function PATCH(req: NextRequest, context: RouteContext) {
    return proxyRequest(req, context);
}
export async function DELETE(req: NextRequest, context: RouteContext) {
    return proxyRequest(req, context);
}
export async function OPTIONS(req: NextRequest, context: RouteContext) {
    return proxyRequest(req, context);
}
