/** ממשקים משותפים לכל ה-API modules */

export interface ActionResponse {
  success: boolean;
  message: string;
}

export interface BulkResultItem {
  phone_masked: string;
  success: boolean;
  message: string;
}
