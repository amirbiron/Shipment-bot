import apiClient from "./client";

export interface CollectionReportItem {
  driver_name: string;
  total_debt: number;
  charge_count: number;
}

export interface CollectionReport {
  items: CollectionReportItem[];
  total_debt: number;
  cycle_start: string;
  cycle_end: string;
}

export interface RevenueReport {
  total_commissions: number;
  total_manual_charges: number;
  total_withdrawals: number;
  net_total: number;
  date_from: string;
  date_to: string;
}

export const getCollectionReport = (
  cycleStart?: string
): Promise<CollectionReport> =>
  apiClient
    .get("/reports/collection", {
      params: cycleStart ? { cycle_start: cycleStart } : {},
    })
    .then((r) => r.data);

export const exportCollectionCsv = async (
  cycleStart?: string
): Promise<void> => {
  const response = await apiClient.get("/reports/collection/export", {
    params: cycleStart ? { cycle_start: cycleStart } : {},
    responseType: "blob",
  });
  const url = URL.createObjectURL(response.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `collection_report${cycleStart ? `_${cycleStart}` : ""}.csv`;
  a.click();
  URL.revokeObjectURL(url);
};

export const getRevenueReport = (
  dateFrom?: string,
  dateTo?: string
): Promise<RevenueReport> =>
  apiClient
    .get("/reports/revenue", {
      params: {
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      },
    })
    .then((r) => r.data);
