import apiClient from "./client";

export interface DeliveryItem {
  id: number;
  pickup_address: string;
  dropoff_address: string;
  status: string;
  fee: number;
  courier_name: string | null;
  sender_name: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface PaginatedDeliveries {
  items: DeliveryItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface DeliveryDetail {
  id: number;
  pickup_address: string;
  pickup_contact_name: string | null;
  pickup_contact_phone: string | null;
  dropoff_address: string;
  dropoff_contact_name: string | null;
  dropoff_contact_phone: string | null;
  status: string;
  fee: number;
  courier_name: string | null;
  sender_name: string | null;
  created_at: string;
  captured_at: string | null;
  delivered_at: string | null;
}

export interface DeliveryHistoryParams {
  page?: number;
  page_size?: number;
  status_filter?: string;
  date_from?: string;
  date_to?: string;
}

export const getActiveDeliveries = (
  page = 1,
  pageSize = 20
): Promise<PaginatedDeliveries> =>
  apiClient
    .get("/deliveries/active", { params: { page, page_size: pageSize } })
    .then((r) => r.data);

export const getDeliveryHistory = (
  params: DeliveryHistoryParams = {}
): Promise<PaginatedDeliveries> =>
  apiClient
    .get("/deliveries/history", {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        status_filter: params.status_filter || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    })
    .then((r) => r.data);

export const getDeliveryDetail = (id: number): Promise<DeliveryDetail> =>
  apiClient.get(`/deliveries/${id}`).then((r) => r.data);
