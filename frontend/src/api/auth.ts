import apiClient from "./client";

export interface ActionResponse {
  success: boolean;
  message: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  station_id: number;
  station_name: string;
}

export interface MeResponse {
  user_id: number;
  station_id: number;
  station_name: string;
  role: string;
}

export const requestOtp = (phoneNumber: string): Promise<ActionResponse> =>
  apiClient.post("/auth/request-otp", { phone_number: phoneNumber }).then((r) => r.data);

export const verifyOtp = (phoneNumber: string, otp: string): Promise<TokenResponse> =>
  apiClient.post("/auth/verify-otp", { phone_number: phoneNumber, otp }).then((r) => r.data);

export const getMe = (): Promise<MeResponse> =>
  apiClient.get("/auth/me").then((r) => r.data);
