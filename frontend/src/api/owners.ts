import apiClient from "./client";
import type { ActionResponse } from "./types";

export type { ActionResponse };

export interface Owner {
  user_id: number;
  name: string;
  phone_masked: string;
  is_active: boolean;
  created_at: string;
}

export const getOwners = (): Promise<Owner[]> =>
  apiClient.get("/owners").then((r) => r.data);

export const addOwner = (phoneNumber: string): Promise<ActionResponse> =>
  apiClient
    .post("/owners", { phone_number: phoneNumber })
    .then((r) => r.data);

export const removeOwner = (userId: number): Promise<ActionResponse> =>
  apiClient.delete(`/owners/${userId}`).then((r) => r.data);
