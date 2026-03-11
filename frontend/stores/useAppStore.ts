import { create } from 'zustand';

interface AppState {
  user: any | null;
  mode: 'paper' | 'live';
  activePoolId: string | null;
  setMode: (mode: 'paper' | 'live') => void;
  setActivePool: (poolId: string | null) => void;
  setUser: (user: any) => void;
}

export const useAppStore = create<AppState>((set) => ({
  user: null,
  mode: 'paper',
  activePoolId: null,
  setMode: (mode) => set({ mode }),
  setActivePool: (poolId) => set({ activePoolId: poolId }),
  setUser: (user) => set({ user }),
}));
