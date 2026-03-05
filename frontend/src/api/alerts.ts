import apiClient from "./client";
import type { ActionResponse } from "./types";

export interface AlertItem {
  type: string;
  title: string;
  data: Record<string, unknown>;
  station_id: number;
  timestamp: string;
}

export interface AlertHistoryResponse {
  alerts: AlertItem[];
  count: number;
}

export interface WalletThresholdResponse {
  station_id: number;
  threshold: number;
}

export const getAlertHistory = (
  limit: number = 50
): Promise<AlertHistoryResponse> =>
  apiClient
    .get("/alerts/history", { params: { limit } })
    .then((r) => r.data);

export const getWalletThreshold = (): Promise<WalletThresholdResponse> =>
  apiClient.get("/alerts/threshold").then((r) => r.data);

export const updateWalletThreshold = (
  threshold: number
): Promise<ActionResponse> =>
  apiClient
    .put("/alerts/threshold", { threshold })
    .then((r) => r.data);

/**
 * יוצר חיבור SSE לקבלת התראות בזמן אמת.
 * מחזיר את ה-EventSource כדי שהקומפוננטה תוכל לסגור אותו ב-cleanup.
 */
export function createAlertStream(
  token: string,
  onMessage: (alert: AlertItem) => void,
  onError?: (err: Event) => void
): EventSource {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || "/api/panel";
  const url = `${baseUrl}/alerts/stream?token=${encodeURIComponent(token)}`;
  const es = new EventSource(url);

  es.onmessage = (event) => {
    try {
      const alert: AlertItem = JSON.parse(event.data);
      onMessage(alert);
    } catch {
      // heartbeat או JSON לא תקין — מתעלם
    }
  };

  if (onError) {
    es.onerror = onError;
  }

  return es;
}
