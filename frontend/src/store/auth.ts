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

// מיגרציה חד-פעמית מ-sessionStorage ל-localStorage (למשתמשים שמחוברים לפני העדכון)
function migrateFromSessionStorage(): void {
  const key = "station-panel-auth";
  const existing = localStorage.getItem(key);
  if (existing) return; // כבר קיים ב-localStorage — אין צורך במיגרציה

  const sessionData = sessionStorage.getItem(key);
  if (sessionData) {
    localStorage.setItem(key, sessionData);
    sessionStorage.removeItem(key);
  }
}

migrateFromSessionStorage();

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

// סנכרון חוצה-טאבים: כש-localStorage משתנה מטאב אחר (למשל אחרי ריפרוש), מעדכנים את ה-store
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === "station-panel-auth" && event.newValue) {
      try {
        const parsed = JSON.parse(event.newValue);
        const state = parsed?.state;
        if (state) {
          useAuthStore.setState({
            token: state.token ?? null,
            refreshToken: state.refreshToken ?? null,
            stationId: state.stationId ?? null,
            stationName: state.stationName ?? null,
            isAuthenticated: state.isAuthenticated ?? false,
          });
        }
      } catch {
        // JSON פגום — מתעלמים
      }
    }
  });
}
