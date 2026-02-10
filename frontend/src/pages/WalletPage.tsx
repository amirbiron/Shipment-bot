import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { getWallet, getLedger, type LedgerItem } from "@/api/wallet";
import DataTable from "@/components/shared/DataTable";
import Pagination from "@/components/shared/Pagination";
import DateRangePicker from "@/components/shared/DateRangePicker";
import StatCard from "@/components/shared/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { formatDateTime, formatCurrency } from "@/lib/format";
import { Wallet, Percent } from "lucide-react";
import { cn } from "@/lib/utils";

const ENTRY_TYPE_MAP: Record<string, string> = {
  commission_credit: "עמלה",
  manual_charge: "חיוב ידני",
  withdrawal: "משיכה",
};

const ENTRY_TYPE_OPTIONS = [
  { value: "commission_credit", label: "עמלה" },
  { value: "manual_charge", label: "חיוב ידני" },
  { value: "withdrawal", label: "משיכה" },
];

const columns: ColumnDef<LedgerItem, unknown>[] = [
  {
    accessorKey: "created_at",
    header: "תאריך",
    cell: ({ row }) => formatDateTime(row.original.created_at),
  },
  {
    accessorKey: "entry_type",
    header: "סוג",
    cell: ({ row }) => ENTRY_TYPE_MAP[row.original.entry_type] || row.original.entry_type,
  },
  {
    accessorKey: "amount",
    header: "סכום",
    cell: ({ row }) => {
      const amount = row.original.amount;
      return (
        <span className={cn("font-medium", amount >= 0 ? "text-success" : "text-destructive")}>
          {amount >= 0 ? "+" : ""}{formatCurrency(amount)}
        </span>
      );
    },
  },
  {
    accessorKey: "balance_after",
    header: "יתרה",
    cell: ({ row }) => formatCurrency(row.original.balance_after),
  },
  {
    accessorKey: "description",
    header: "תיאור",
    cell: ({ row }) => row.original.description || "-",
  },
];

export default function WalletPage() {
  const [page, setPage] = useState(1);
  const [entryType, setEntryType] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const { data: wallet, isLoading: walletLoading } = useQuery({
    queryKey: ["wallet"],
    queryFn: getWallet,
  });

  const { data: ledger, isLoading: ledgerLoading } = useQuery({
    queryKey: ["ledger", page, entryType, dateFrom, dateTo],
    queryFn: () =>
      getLedger({ page, entry_type: entryType, date_from: dateFrom, date_to: dateTo }),
  });

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">ארנק תחנה</h2>

      {/* סיכום */}
      {walletLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : wallet ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <StatCard icon={Wallet} label="יתרה" value={formatCurrency(wallet.balance)} />
          <StatCard icon={Percent} label="עמלה" value={`${(wallet.commission_rate * 100).toFixed(0)}%`} />
        </div>
      ) : null}

      {/* פילטרים */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">תנועות</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <Label className="text-muted-foreground">סוג</Label>
              <Select
                value={entryType || undefined}
                onValueChange={(v) => {
                  setEntryType(v === "__all__" ? "" : v);
                  setPage(1);
                }}
              >
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="הכל" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">הכל</SelectItem>
                  {ENTRY_TYPE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DateRangePicker
              dateFrom={dateFrom}
              dateTo={dateTo}
              onDateFromChange={(v) => { setDateFrom(v); setPage(1); }}
              onDateToChange={(v) => { setDateTo(v); setPage(1); }}
            />
          </div>

          <DataTable
            columns={columns}
            data={ledger?.items ?? []}
            isLoading={ledgerLoading}
            emptyMessage="אין תנועות להצגה"
          />

          {/* סיכום תקופה */}
          {ledger?.summary && Object.keys(ledger.summary).length > 0 && (
            <div className="flex flex-wrap gap-4 text-sm border-t border-border pt-3 mt-3">
              {Object.entries(ledger.summary).map(([type, total]) => (
                <span key={type} className="text-muted-foreground">
                  {ENTRY_TYPE_MAP[type] || type}:{" "}
                  <span className={cn("font-medium", total >= 0 ? "text-success" : "text-destructive")}>
                    {formatCurrency(total)}
                  </span>
                </span>
              ))}
            </div>
          )}

          {ledger && (
            <Pagination
              page={ledger.page}
              totalPages={ledger.total_pages}
              onPageChange={setPage}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
