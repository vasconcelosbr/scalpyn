/**
 * API client with JWT interceptor for Scalpyn backend.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '/api';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('token');
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

  const res = await fetch(`${API_URL}${endpoint}`, { ...options, headers });

  if (res.status === 401) {
    // Token expired — redirect to login
    if (typeof window !== 'undefined') {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? `API error: ${res.status}`);
  }

  return res.json();
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
