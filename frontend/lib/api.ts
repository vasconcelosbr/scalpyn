/**
 * API client with JWT interceptor for Scalpyn backend.
 */

// Always use relative /api path so Next.js rewrites proxy the request server-side.
// This avoids CORS issues — the browser never calls Cloud Run directly.
const API_URL = '/api';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('token');
}

/**
 * Resolve a caller-supplied endpoint into the absolute browser path that will
 * be requested. Two conventions exist in this codebase historically:
 *   1. `/profiles`              → fetched at `/api/profiles`
 *   2. `/api/spot-engine/start` → fetched at `/api/spot-engine/start`
 *
 * Without normalization, convention (2) double-prefixes to `/api/api/...`,
 * which the FastAPI backend rejects with `404 {"detail":"Not Found"}` — this
 * was the root cause of the Start Engine "Not Found" alert in production.
 *
 * Strategy: strip a single leading `/api` (or `api`) segment if present, then
 * prefix exactly once with `API_URL`. Idempotent under both conventions.
 */
function resolvePath(endpoint: string): string {
  let cleaned = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  if (cleaned === '/api' || cleaned.startsWith('/api/')) {
    cleaned = cleaned.slice(4) || '/';
  }
  return `${API_URL}${cleaned}`;
}

/**
 * Structured error thrown by apiFetch on non-OK responses.
 *
 * Keeps `message` backward-compatible (uses backend `detail` when present),
 * but exposes status / path / endpoint / method / detail / rawBody so callers
 * and UI surfaces can render rich diagnostics (especially for proxy 404s
 * where the body is just `{"detail":"Not Found"}`).
 */
export class ApiError extends Error {
  status: number;
  /** Logical endpoint string passed by the caller (e.g. `/spot-engine/start`). */
  endpoint: string;
  /** Actual browser path requested after normalization (e.g. `/api/spot-engine/start`). */
  path: string;
  method: string;
  detail: string | null;
  rawBody: string | null;

  constructor(opts: {
    status: number;
    endpoint: string;
    path: string;
    method: string;
    detail: string | null;
    rawBody: string | null;
  }) {
    const baseMessage = opts.detail ?? `API error: ${opts.status}`;
    super(baseMessage);
    this.name = 'ApiError';
    this.status = opts.status;
    this.endpoint = opts.endpoint;
    this.path = opts.path;
    this.method = opts.method;
    this.detail = opts.detail;
    this.rawBody = opts.rawBody;
  }

  /** Human-readable summary including HTTP status + actual fetched path. */
  toDescriptiveString(): string {
    const detailPart = this.detail ?? '(no detail)';
    return `${detailPart} — HTTP ${this.status} on ${this.method} ${this.path}`;
  }
}

export async function apiFetch<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const method = (options.method ?? 'GET').toUpperCase();
  const path = resolvePath(endpoint);
  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    // Token expired — redirect to login
    if (typeof window !== 'undefined') {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    }
    throw new ApiError({
      status: 401,
      endpoint,
      path,
      method,
      detail: 'Unauthorized',
      rawBody: null,
    });
  }

  if (!res.ok) {
    const rawBody = await res.text().catch(() => '');
    let detail: string | null = null;
    if (rawBody) {
      try {
        const parsed = JSON.parse(rawBody);
        if (typeof parsed?.detail === 'string') {
          detail = parsed.detail;
        } else if (typeof parsed?.message === 'string') {
          detail = parsed.message;
        }
      } catch {
        // Non-JSON body (e.g. HTML error page from edge proxy). Keep rawBody only.
      }
    }
    throw new ApiError({
      status: res.status,
      endpoint,
      path,
      method,
      detail,
      rawBody: rawBody || null,
    });
  }

  // Some endpoints return 204 / empty bodies — guard against JSON parse error.
  if (res.status === 204) return undefined as unknown as T;
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

export function apiGet<T = any>(endpoint: string) {
  return apiFetch<T>(endpoint, { method: 'GET' });
}

export function apiPost<T = any>(endpoint: string, body?: any) {
  return apiFetch<T>(endpoint, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function apiPut<T = any>(endpoint: string, body?: any) {
  return apiFetch<T>(endpoint, {
    method: 'PUT',
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function apiDelete<T = any>(endpoint: string) {
  return apiFetch<T>(endpoint, { method: 'DELETE' });
}
