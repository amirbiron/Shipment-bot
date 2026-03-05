import apiClient from "./client";

export interface AuditLogItem {
  id: number;
  action: string;
  action_label: string;
  actor_user_id: number;
  actor_name: string;
  target_user_id: number | null;
  target_name: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface PaginatedAuditLog {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AuditActionType {
  value: string;
  label: string;
}

export interface AuditParams {
  action?: string;
  actor_user_id?: number;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export const getAuditLog = (
  params: AuditParams = {}
): Promise<PaginatedAuditLog> =>
  apiClient
    .get("/audit", {
      params: {
        action: params.action || undefined,
        actor_user_id: params.actor_user_id || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
      },
    })
    .then((r) => r.data);

export const getAuditActionTypes = (): Promise<AuditActionType[]> =>
  apiClient.get("/audit/actions").then((r) => r.data);
