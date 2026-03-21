import axios from "axios";
import { useAuthStore } from "@/store/auth";

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api/panel",
  headers: { "Content-Type": "application/json" },
});

// נתיבי auth שלא דורשים redirect ב-401
const AUTH_PATHS = ["/auth/request-otp", "/auth/verify-otp", "/auth/telegram-login", "/auth/telegram-login-select-station", "/auth/refresh"];

// דגל למניעת ריפרוש מקבילי
let isRefreshing = false;
let refreshPromise: Promise<string | null> | null = null;

// ריפרוש טוקן — מחזיר access token חדש או null אם נכשל
async function tryRefreshToken(): Promise<string | null> {
  const { refreshToken } = useAuthStore.getState();
  if (!refreshToken) return null;

  try {
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
  }
}

// הוספת טוקן לכל בקשה
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
    if (error.response?.status === 401) {
      const requestUrl = error.config?.url || "";
      const isAuthRoute = AUTH_PATHS.some((path) => requestUrl.includes(path));

      // אם זה נתיב auth (כמו verify-otp) — לא מנסים ריפרוש
      if (isAuthRoute) {
        return Promise.reject(error);
      }

      // מניעת ריפרוש מקבילי — כולם ממתינים לאותו promise
      if (!isRefreshing) {
        isRefreshing = true;
        refreshPromise = tryRefreshToken().finally(() => {
          isRefreshing = false;
          refreshPromise = null;
        });
      }

      const newToken = await refreshPromise;

      if (newToken) {
        // ניסיון חוזר עם הטוקן החדש
        error.config.headers.Authorization = `Bearer ${newToken}`;
        return apiClient.request(error.config);
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
