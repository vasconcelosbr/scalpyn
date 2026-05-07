const API_URL = '/api';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('token');
}

function resolvePath(endpoint: string): string {
  let cleaned = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  if (cleaned === '/api' || cleaned.startsWith('/api/')) {
    cleaned = cleaned.slice(4) || '/';
  }
  return `${API_URL}${cleaned}`;
}

function buildErrorMessage(status: number, detail: string | null): string {
  return detail ? `${status} ${detail}` : `${status} API error`;
}

export class ApiError extends Error {
  status: number;
  endpoint: string;
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
    super(buildErrorMessage(opts.status, opts.detail));
    this.name = 'ApiError';
    this.status = opts.status;
    this.endpoint = opts.endpoint;
    this.path = opts.path;
    this.method = opts.method;
    this.detail = opts.detail;
    this.rawBody = opts.rawBody;
  }

  toDescriptiveString(): string {
    return `${this.message} on ${this.method} ${this.path}`;
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
        // Non-JSON body — keep rawBody only.
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
