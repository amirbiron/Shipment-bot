import apiClient from "./client";

export interface WalletData {
  balance: number;
  commission_rate: number;
}

export interface LedgerItem {
  id: number;
  entry_type: string;
  amount: number;
  balance_after: number;
  description: string | null;
  created_at: string;
}

export interface PaginatedLedger {
  items: LedgerItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  summary: Record<string, number>;
}

export interface LedgerParams {
  page?: number;
  page_size?: number;
  entry_type?: string;
  date_from?: string;
  date_to?: string;
}

export const getWallet = (): Promise<WalletData> =>
  apiClient.get("/wallet").then((r) => r.data);

export const getLedger = (params: LedgerParams = {}): Promise<PaginatedLedger> =>
  apiClient
    .get("/wallet/ledger", {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        entry_type: params.entry_type || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    })
    .then((r) => r.data);
