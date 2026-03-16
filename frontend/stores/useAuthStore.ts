import { create } from 'zustand';
import { setToken, clearToken } from '@/lib/auth';

interface AuthUser {
  id: string;
  email: string;
  name: string;
}

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  setUser: (user: AuthUser, token: string) => void;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? '/api';

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  token: null,
  isAuthenticated: false,

  setUser: (user, token) => {
    setToken(token);
    set({ user, token, isAuthenticated: true });
  },

  login: async (email, password) => {
    const res = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail ?? 'Login failed');
    }

    const data = await res.json();
    get().setUser(data.user, data.access_token);
  },

  logout: () => {
    clearToken();
    set({ user: null, token: null, isAuthenticated: false });
  },
}));
