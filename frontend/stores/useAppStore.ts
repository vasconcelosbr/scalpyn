import { create } from 'zustand';

interface AppState {
  user: any | null;
  mode: 'paper' | 'live';
  activePoolId: string | null;
  mobileNavOpen: boolean;
  setMode: (mode: 'paper' | 'live') => void;
  setActivePool: (poolId: string | null) => void;
  setUser: (user: any) => void;
  openMobileNav: () => void;
  closeMobileNav: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  user: null,
  mode: 'paper',
  activePoolId: null,
  mobileNavOpen: false,
  setMode: (mode) => set({ mode }),
  setActivePool: (poolId) => set({ activePoolId: poolId }),
  setUser: (user) => set({ user }),
  openMobileNav: () => set({ mobileNavOpen: true }),
  closeMobileNav: () => set({ mobileNavOpen: false }),
}));
