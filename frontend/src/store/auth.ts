import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token: string | null;
  stationId: number | null;
  stationName: string | null;
  isAuthenticated: boolean;
  login: (token: string, stationId: number, stationName: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      stationId: null,
      stationName: null,
      isAuthenticated: false,
      login: (token, stationId, stationName) =>
        set({ token, stationId, stationName, isAuthenticated: true }),
      logout: () =>
        set({
          token: null,
          stationId: null,
          stationName: null,
          isAuthenticated: false,
        }),
    }),
    { name: "station-panel-auth" }
  )
);
