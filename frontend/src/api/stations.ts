import apiClient from "./client";

export interface StationSummary {
  station_id: number;
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

export interface MultiStationTotals {
  total_active_deliveries: number;
  total_today_deliveries: number;
  total_today_delivered: number;
  total_wallet_balance: number;
  total_today_revenue: number;
  total_active_dispatchers: number;
  total_blacklisted: number;
}

export interface MultiStationDashboard {
  current_station_id: number;
  stations: StationSummary[];
  totals: MultiStationTotals;
}

export const getMultiStationDashboard = (): Promise<MultiStationDashboard> =>
  apiClient.get("/stations/dashboard").then((r) => r.data);
