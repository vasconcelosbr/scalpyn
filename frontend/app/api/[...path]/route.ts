/**
 * Next.js catch-all API Route Proxy
 * Forwards all /api/* requests to the FastAPI backend (Cloud Run).
 *
 * This replaces next.config.ts rewrites because rewrites have quirks with
 * trailing slashes, path encoding, and Vercel CDN redirect behavior that
 * caused persistent 404/Mixed Content errors.
 */
import { NextRequest, NextResponse } from 'next/server';

// BACKEND_URL must include /api prefix, e.g. https://...run.app/api
const BACKEND_URL = (process.env.BACKEND_URL ?? 'http://localhost:8000/api').replace(/\/$/, '');

async function proxyRequest(req: NextRequest, params: { path: string[] }): Promise<NextResponse> {
  // Reconstruct the path preserving trailing slash
  const pathSegments = params.path ?? [];
  const rawPath = req.nextUrl.pathname.replace(/^\/api/, '') || '/';
  const search = req.nextUrl.search ?? '';
  const targetUrl = `${BACKEND_URL}${rawPath}${search}`;

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
    redirect: 'follow', // follow backend redirects server-side (avoids Mixed Content)
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

export async function GET(req: NextRequest, context: { params: { path: string[] } }) {
  return proxyRequest(req, context.params);
}
export async function POST(req: NextRequest, context: { params: { path: string[] } }) {
  return proxyRequest(req, context.params);
}
export async function PUT(req: NextRequest, context: { params: { path: string[] } }) {
  return proxyRequest(req, context.params);
}
export async function PATCH(req: NextRequest, context: { params: { path: string[] } }) {
  return proxyRequest(req, context.params);
}
export async function DELETE(req: NextRequest, context: { params: { path: string[] } }) {
  return proxyRequest(req, context.params);
}
export async function OPTIONS(req: NextRequest, context: { params: { path: string[] } }) {
  return proxyRequest(req, context.params);
}
