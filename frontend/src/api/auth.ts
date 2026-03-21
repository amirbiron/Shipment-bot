import apiClient from "./client";
import type { ActionResponse } from "./types";

export type { ActionResponse };

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

// Telegram Login Widget

export interface TelegramBotInfoResponse {
  bot_username: string;
  enabled: boolean;
}

export interface TelegramAuthData {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

export const getTelegramBotInfo = (): Promise<TelegramBotInfoResponse> =>
  apiClient.get("/auth/telegram-bot-info").then((r) => r.data);

export const telegramLogin = (authData: TelegramAuthData): Promise<TokenResponse> =>
  apiClient.post("/auth/telegram-login", authData).then((r) => r.data);

export const telegramLoginSelectStation = (
  authData: TelegramAuthData,
  stationId: number
): Promise<TokenResponse> =>
  apiClient
    .post(`/auth/telegram-login-select-station?station_id=${stationId}`, authData)
    .then((r) => r.data);
