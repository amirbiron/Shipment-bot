import apiClient from "./client";
import type { ActionResponse, BulkResultItem } from "./types";

export type { ActionResponse, BulkResultItem };

export interface BlacklistItem {
  courier_id: number;
  name: string;
  phone_masked: string;
  reason: string;
  blocked_at: string;
}

export interface BulkBlacklistResponse {
  results: BulkResultItem[];
  total: number;
  success_count: number;
}

export const getBlacklist = (): Promise<BlacklistItem[]> =>
  apiClient.get("/blacklist").then((r) => r.data);

export const addToBlacklist = (
  phoneNumber: string,
  reason = ""
): Promise<ActionResponse> =>
  apiClient
    .post("/blacklist", { phone_number: phoneNumber, reason })
    .then((r) => r.data);

export const addToBlacklistBulk = (
  entries: Array<{ phone_number: string; reason: string }>
): Promise<BulkBlacklistResponse> =>
  apiClient.post("/blacklist/bulk", { entries }).then((r) => r.data);

export const removeFromBlacklist = (
  courierId: number
): Promise<ActionResponse> =>
  apiClient.delete(`/blacklist/${courierId}`).then((r) => r.data);
