import axios, { type InternalAxiosRequestConfig } from "axios";
import { useAuthStore } from "@/store/auth";

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api/panel",
  headers: { "Content-Type": "application/json" },
});

// נתיבי auth שלא דורשים redirect ב-401
const AUTH_PATHS = ["/auth/request-otp", "/auth/verify-otp", "/auth/telegram-login", "/auth/telegram-login-select-station", "/auth/refresh"];

// דגל למניעת ריפרוש מקבילי באותו טאב
let isRefreshing = false;
let refreshPromise: Promise<string | null> | null = null;

// מפתחות localStorage לנעילה חוצת-טאבים
const REFRESH_LOCK_KEY = "station-panel-refresh-lock";
const REFRESH_LOCK_TTL_MS = 10_000; // נעילה פגה אחרי 10 שניות (הגנה מפני טאב שקרס)

/**
 * נעילה חוצת-טאבים למניעת ריפרוש מקבילי בין טאבים.
 * משתמשים ב-localStorage כ-mutex — רק טאב אחד מבצע refresh,
 * השאר ממתינים ל-storage event שיעדכן את הטוקנים.
 */
function acquireRefreshLock(): boolean {
  const now = Date.now();
  const existing = localStorage.getItem(REFRESH_LOCK_KEY);
  if (existing) {
    const lockTime = parseInt(existing, 10);
    // נעילה קיימת ותקפה — טאב אחר מטפל
    if (now - lockTime < REFRESH_LOCK_TTL_MS) {
      return false;
    }
    // נעילה ישנה (טאב קרס) — דורסים
  }
  localStorage.setItem(REFRESH_LOCK_KEY, now.toString());
  return true;
}

function releaseRefreshLock(): void {
  localStorage.removeItem(REFRESH_LOCK_KEY);
}

/**
 * ממתין לטוקן חדש מטאב אחר שמבצע ריפרוש.
 * מאזין ל-storage event על ה-auth store, עם timeout.
 */
function waitForCrossTabRefresh(expiredToken: string): Promise<string | null> {
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      window.removeEventListener("storage", handler);
      // ייתכן שטוקן התעדכן בינתיים (storage event הגיע ממש לפני ה-timeout)
      const current = useAuthStore.getState().token;
      resolve(current && current !== expiredToken ? current : null);
    }, REFRESH_LOCK_TTL_MS);

    function handler(event: StorageEvent): void {
      if (event.key === "station-panel-auth" && event.newValue) {
        try {
          const parsed = JSON.parse(event.newValue);
          const newToken = parsed?.state?.token;
          if (newToken && newToken !== expiredToken) {
            clearTimeout(timeout);
            window.removeEventListener("storage", handler);
            // מעדכנים את ה-store המקומי
            useAuthStore.setState({
              token: parsed.state.token,
              refreshToken: parsed.state.refreshToken,
            });
            resolve(newToken);
          }
        } catch {
          // JSON פגום — ממשיכים לחכות
        }
      }
    }

    window.addEventListener("storage", handler);
  });
}

// ריפרוש טוקן עם נעילה חוצת-טאבים — מחזיר access token חדש או null אם נכשל
async function tryRefreshToken(expiredToken: string): Promise<string | null> {
  // ניסיון לתפוס את הנעילה
  const gotLock = acquireRefreshLock();

  if (!gotLock) {
    // טאב אחר כבר מבצע ריפרוש — ממתינים לתוצאה
    return waitForCrossTabRefresh(expiredToken);
  }

  try {
    const { refreshToken } = useAuthStore.getState();
    if (!refreshToken) return null;

    // קריאה ישירה ל-axios כדי לא להיכנס ל-interceptor שלנו
    const response = await axios.post(
      `${apiClient.defaults.baseURL}/auth/refresh`,
      { refresh_token: refreshToken },
      { headers: { "Content-Type": "application/json" } }
    );
    const { access_token, refresh_token } = response.data;
    useAuthStore.getState().setTokens(access_token, refresh_token);
    return access_token;
  } catch {
    return null;
  } finally {
    releaseRefreshLock();
  }
}

// הוספת טוקן לכל בקשה — קריאה טרייה מה-store (תומך בעדכונים חוצי-טאבים)
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// טיפול ב-401 — ניסיון ריפרוש לפני מעבר לדף כניסה
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (error.response?.status === 401) {
      const requestUrl = config?.url || "";
      const isAuthRoute = AUTH_PATHS.some((path) => requestUrl.includes(path));

      // אם זה נתיב auth (כמו verify-otp) — לא מנסים ריפרוש
      if (isAuthRoute) {
        return Promise.reject(error);
      }

      // מניעת לולאת retry אינסופית — ניסיון חוזר פעם אחת בלבד
      if (config._retry) {
        useAuthStore.getState().logout();
        sessionStorage.setItem("session-expired", "1");
        window.location.href = "/panel/login";
        return Promise.reject(error);
      }
      config._retry = true;

      // הטוקן שנכשל — משמש להשוואה עם טוקן חדש מטאב אחר
      const failedToken = config.headers.Authorization?.toString().replace("Bearer ", "") || "";

      // בדיקה מיידית: אולי טאב אחר כבר ריפרש לפני שהגענו לכאן
      const freshStoreToken = useAuthStore.getState().token;
      if (freshStoreToken && freshStoreToken !== failedToken) {
        config.headers.Authorization = `Bearer ${freshStoreToken}`;
        return apiClient.request(config);
      }

      // מניעת ריפרוש מקבילי באותו טאב — כולם ממתינים לאותו promise
      if (!isRefreshing) {
        isRefreshing = true;
        refreshPromise = tryRefreshToken(failedToken).finally(() => {
          isRefreshing = false;
          refreshPromise = null;
        });
      }

      const newToken = await refreshPromise;

      if (newToken) {
        // ניסיון חוזר עם הטוקן החדש
        config.headers.Authorization = `Bearer ${newToken}`;
        return apiClient.request(config);
      }

      // ריפרוש נכשל — logout
      useAuthStore.getState().logout();
      sessionStorage.setItem("session-expired", "1");
      window.location.href = "/panel/login";
    }
    return Promise.reject(error);
  }
);

export default apiClient;
