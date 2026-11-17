const API_URL = '/api';
let refreshInFlight: {key:string; promise:Promise<string|null>} | null = null;

async function renewedToken(failedToken: string | null): Promise<string | null> {
  if (typeof window === 'undefined') return null;
  if (getToken() !== failedToken) return getToken();
  const refresh = localStorage.getItem('refresh_token');
  if (!refresh) return null;
  if (!refreshInFlight || refreshInFlight.key !== refresh) {
    const promise = (async () => {
      const response = await fetch(`${API_URL}/auth/refresh`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      // A logout/account switch during refresh must never resurrect the session.
      if (localStorage.getItem('refresh_token') !== refresh) throw new Error('Sessão alterada durante a renovação');
      if (getToken() !== failedToken) return getToken();
      if (response.status === 401 || response.status === 403) return null;
      if (!response.ok) throw new Error('Renovação de sessão temporariamente indisponível');
      const data = await response.json();
      if (typeof data.access_token !== 'string' || !data.access_token) throw new Error('Resposta de renovação inválida');
      if (localStorage.getItem('refresh_token') !== refresh || getToken() !== failedToken)
        throw new Error('Sessão alterada durante a renovação');
      localStorage.setItem('token', data.access_token);
      return data.access_token;
    })().finally(() => { if(refreshInFlight?.promise===promise) refreshInFlight = null; });
    refreshInFlight={key:refresh,promise};
  }
  return refreshInFlight.promise;
}

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
  options: RequestInit = {},
  responseType: 'json' | 'blob' = 'json'
): Promise<T> {
  const token = getToken();
  let requestToken = token;
  const sessionRefresh = typeof window==='undefined'?null:localStorage.getItem('refresh_token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const method = (options.method ?? 'GET').toUpperCase();
  const path = resolvePath(endpoint);
  let res = await fetch(path, { ...options, headers });

  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    if (typeof window!=='undefined' && localStorage.getItem('refresh_token')!==sessionRefresh)
      throw new ApiError({status:409,endpoint,path,method,detail:'Sessão alterada; recarregue a página',rawBody:null});
    let renewed: string | null;
    try { renewed = await renewedToken(token); }
    catch {
      throw new ApiError({ status: 503, endpoint, path, method,
        detail: 'Sessão não renovada: tente novamente', rawBody: null });
    }
    if (renewed) {
      requestToken = renewed;
      res = await fetch(path, { ...options, headers: { ...headers, Authorization: `Bearer ${renewed}` } });
    }
  }

  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      if (getToken() !== requestToken || localStorage.getItem('refresh_token') !== sessionRefresh)
        throw new ApiError({status:409,endpoint,path,method,detail:'Sessão alterada; recarregue a página',rawBody:null});
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      localStorage.removeItem('refresh_token');
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
        } else if (typeof parsed?.detail?.code === 'string') {
          detail = parsed.detail.code;
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
  if (responseType === 'blob') return await res.blob() as T;
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError({
      status: 200,
      endpoint,
      path,
      method,
      detail: 'Response is not valid JSON',
      rawBody: text.slice(0, 500),
    });
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

export function apiPatch<T = unknown>(endpoint: string, body?: unknown) {
  return apiFetch<T>(endpoint, {
    method: 'PATCH',
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function apiDelete<T = any>(endpoint: string) {
  return apiFetch<T>(endpoint, { method: 'DELETE' });
}
