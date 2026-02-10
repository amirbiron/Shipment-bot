import { format, parseISO } from "date-fns";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    return format(parseISO(iso), "dd/MM/yy");
  } catch {
    return "-";
  }
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    return format(parseISO(iso), "dd/MM/yy HH:mm");
  } catch {
    return "-";
  }
}

export function formatCurrency(amount: number): string {
  return `\u20AA${amount.toLocaleString("he-IL", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
