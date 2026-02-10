import apiClient from "./client";

export interface DashboardData {
  station_name: string;
  active_deliveries_count: number;
  today_deliveries_count: number;
  today_delivered_count: number;
  wallet_balance: number;
  commission_rate: number;
  today_revenue: number;
  active_dispatchers_count: number;
  blacklisted_count: number;
}

export const getDashboard = (): Promise<DashboardData> =>
  apiClient.get("/dashboard").then((r) => r.data);
