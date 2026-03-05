import apiClient from "./client";
import type { ActionResponse } from "./types";

export type { ActionResponse };

export interface OperatingHoursDay {
  open: string;
  close: string;
}

export interface StationSettings {
  name: string;
  description: string | null;
  operating_hours: Record<string, OperatingHoursDay | null> | null;
  service_areas: string[] | null;
  logo_url: string | null;
}

export interface UpdateStationSettingsData {
  name?: string;
  description?: string | null;
  operating_hours?: Record<string, OperatingHoursDay | null> | null;
  service_areas?: string[] | null;
  logo_url?: string | null;
  clear_description?: boolean;
  clear_operating_hours?: boolean;
  clear_service_areas?: boolean;
  clear_logo_url?: boolean;
}

export const getStationSettings = (): Promise<StationSettings> =>
  apiClient.get("/settings").then((r) => r.data);

export const updateStationSettings = (
  data: UpdateStationSettingsData
): Promise<ActionResponse> =>
  apiClient.put("/settings", data).then((r) => r.data);
