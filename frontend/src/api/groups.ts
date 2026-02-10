import apiClient from "./client";

export interface GroupSettings {
  public_group_chat_id: string | null;
  public_group_platform: string | null;
  private_group_chat_id: string | null;
  private_group_platform: string | null;
}

export interface ActionResponse {
  success: boolean;
  message: string;
}

export interface UpdateGroupSettingsData {
  public_group_chat_id?: string | null;
  public_group_platform?: string | null;
  private_group_chat_id?: string | null;
  private_group_platform?: string | null;
}

export const getGroupSettings = (): Promise<GroupSettings> =>
  apiClient.get("/groups").then((r) => r.data);

export const updateGroupSettings = (
  data: UpdateGroupSettingsData
): Promise<ActionResponse> =>
  apiClient.put("/groups", data).then((r) => r.data);
