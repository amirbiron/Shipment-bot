import axios from "axios";
import { useAuthStore } from "@/store/auth";

const apiClient = axios.create({
  baseURL: "/api/panel",
  headers: { "Content-Type": "application/json" },
});

// הוספת טוקן לכל בקשה
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// טיפול ב-401 — מעבר לדף כניסה
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout();
      window.location.href = "/panel/login";
    }
    return Promise.reject(error);
  }
);

export default apiClient;
