import apiClient from "./client";

export interface Dispatcher {
  user_id: number;
  name: string;
  phone_masked: string;
  is_active: boolean;
  created_at: string;
}

export interface ActionResponse {
  success: boolean;
  message: string;
}

export interface BulkResultItem {
  phone_masked: string;
  success: boolean;
  message: string;
}

export interface BulkAddResponse {
  results: BulkResultItem[];
  total: number;
  success_count: number;
}

export const getDispatchers = (): Promise<Dispatcher[]> =>
  apiClient.get("/dispatchers").then((r) => r.data);

export const addDispatcher = (phoneNumber: string): Promise<ActionResponse> =>
  apiClient
    .post("/dispatchers", { phone_number: phoneNumber })
    .then((r) => r.data);

export const addDispatchersBulk = (
  phoneNumbers: string[]
): Promise<BulkAddResponse> =>
  apiClient
    .post("/dispatchers/bulk", { phone_numbers: phoneNumbers })
    .then((r) => r.data);

export const removeDispatcher = (userId: number): Promise<ActionResponse> =>
  apiClient.delete(`/dispatchers/${userId}`).then((r) => r.data);
