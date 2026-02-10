import axios from "axios";
import { useAuthStore } from "@/store/auth";
import { toast } from "@/components/ui/use-toast";

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api/panel",
  headers: { "Content-Type": "application/json" },
});

// נתיבי auth שלא דורשים redirect ב-401
const AUTH_PATHS = ["/auth/request-otp", "/auth/verify-otp"];

// הוספת טוקן לכל בקשה
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// טיפול ב-401 — מעבר לדף כניסה (רק לנתיבים מוגנים, לא לזרימת OTP)
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const requestUrl = error.config?.url || "";
      const isAuthRoute = AUTH_PATHS.some((path) => requestUrl.includes(path));
      if (!isAuthRoute) {
        useAuthStore.getState().logout();
        toast({ title: "פג תוקף הכניסה", description: "יש להתחבר מחדש", variant: "destructive" });
        window.location.href = "/panel/login";
      }
    }
    return Promise.reject(error);
  }
);

export default apiClient;
