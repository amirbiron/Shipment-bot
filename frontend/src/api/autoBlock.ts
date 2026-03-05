import apiClient from "./client";
import type { ActionResponse } from "./types";

export type { ActionResponse };

export interface AutoBlockSettings {
  auto_block_enabled: boolean;
  auto_block_grace_months: number;
  auto_block_min_debt: number;
}

export interface UpdateAutoBlockData {
  auto_block_enabled?: boolean;
  auto_block_grace_months?: number;
  auto_block_min_debt?: number;
}

export const getAutoBlockSettings = (): Promise<AutoBlockSettings> =>
  apiClient.get("/auto-block").then((r) => r.data);

export const updateAutoBlockSettings = (
  data: UpdateAutoBlockData
): Promise<ActionResponse> =>
  apiClient.put("/auto-block", data).then((r) => r.data);
