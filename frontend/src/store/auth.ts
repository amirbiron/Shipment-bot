import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  stationId: number | null;
  stationName: string | null;
  isAuthenticated: boolean;
  login: (token: string, refreshToken: string, stationId: number, stationName: string) => void;
  setTokens: (token: string, refreshToken: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      stationId: null,
      stationName: null,
      isAuthenticated: false,
      login: (token, refreshToken, stationId, stationName) =>
        set({ token, refreshToken, stationId, stationName, isAuthenticated: true }),
      setTokens: (token, refreshToken) =>
        set({ token, refreshToken }),
      logout: () =>
        set({
          token: null,
          refreshToken: null,
          stationId: null,
          stationName: null,
          isAuthenticated: false,
        }),
    }),
    { name: "station-panel-auth", storage: createJSONStorage(() => localStorage) }
  )
);
