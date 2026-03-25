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
const COLLECTION_ROUTES = ['/api/pools', '/api/trades', '/api/orders', '/api/exchanges', '/api/watchlist', '/api/profiles', '/api/custom-watchlists'];

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

  // Forward all headers except host (which would confuse the backend)
  const forwardHeaders: Record<string, string> = {};
    req.headers.forEach((value, key) => {
          const lower = key.toLowerCase();
          if (lower !== 'host' && lower !== 'accept-encoding' && lower !== 'connection') {
                  forwardHeaders[key] = value;
          }
    });

  const hasBody = req.method !== 'GET' && req.method !== 'HEAD';
    const body = hasBody ? await req.arrayBuffer() : undefined;

  const backendRes = await fetch(targetUrl, {
        method: req.method,
        headers: forwardHeaders,
        body: body ? Buffer.from(body) : undefined,
        redirect: 'follow',
  });

  // Forward response headers back to client
  const resHeaders = new Headers();
    backendRes.headers.forEach((value, key) => {
          const lower = key.toLowerCase();
          if (lower !== 'transfer-encoding' && lower !== 'content-encoding') {
                  resHeaders.set(key, value);
          }
    });

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
