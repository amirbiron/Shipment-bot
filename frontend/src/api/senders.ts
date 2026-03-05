import apiClient from "./client";

export interface Sender {
  user_id: number;
  name: string;
  phone_masked: string;
  platform: string;
  is_active: boolean;
  created_at: string;
  deliveries_count: number;
  delivered_count: number;
  active_deliveries_count: number;
  total_volume: number;
  last_delivery_at: string | null;
}

export interface PaginatedSenders {
  items: Sender[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface SenderDetail {
  user_id: number;
  name: string;
  phone_masked: string;
  platform: string;
  is_active: boolean;
  created_at: string;
  deliveries_count: number;
  delivered_count: number;
  cancelled_count: number;
  active_deliveries_count: number;
  total_volume: number;
  avg_fee: number;
  first_delivery_at: string | null;
  last_delivery_at: string | null;
}

export interface TopSender {
  user_id: number;
  name: string;
  phone_masked: string;
  deliveries_count: number;
  delivered_count: number;
  total_volume: number;
  last_delivery_at: string | null;
}

export interface SenderDeliveryItem {
  id: number;
  pickup_address: string;
  dropoff_address: string;
  status: string;
  fee: number;
  courier_name: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface PaginatedSenderDeliveries {
  items: SenderDeliveryItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface SendersParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}

export interface SenderDeliveriesParams {
  page?: number;
  page_size?: number;
  status_filter?: string;
}

export const getSenders = (
  params: SendersParams = {}
): Promise<PaginatedSenders> =>
  apiClient
    .get("/senders", {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        search: params.search || undefined,
        sort_by: params.sort_by || undefined,
        sort_order: params.sort_order || undefined,
      },
    })
    .then((r) => r.data);

export const getTopSenders = (limit = 10): Promise<TopSender[]> =>
  apiClient
    .get("/senders/top", { params: { limit } })
    .then((r) => r.data);

export const getSenderDetail = (senderId: number): Promise<SenderDetail> =>
  apiClient.get(`/senders/${senderId}`).then((r) => r.data);

export const getSenderDeliveries = (
  senderId: number,
  params: SenderDeliveriesParams = {}
): Promise<PaginatedSenderDeliveries> =>
  apiClient
    .get(`/senders/${senderId}/deliveries`, {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        status_filter: params.status_filter || undefined,
      },
    })
    .then((r) => r.data);
