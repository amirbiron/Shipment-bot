import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { getDeliveryHistory, type DeliveryItem } from "@/api/deliveries";
import DataTable from "@/components/shared/DataTable";
import Pagination from "@/components/shared/Pagination";
import StatusBadge from "@/components/shared/StatusBadge";
import DateRangePicker from "@/components/shared/DateRangePicker";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatCurrency } from "@/lib/format";

const STATUS_OPTIONS = [
  { value: "open", label: "פתוח" },
  { value: "captured", label: "נתפס" },
  { value: "delivered", label: "נמסר" },
  { value: "cancelled", label: "בוטל" },
];

const columns: ColumnDef<DeliveryItem, unknown>[] = [
  { accessorKey: "id", header: "#", cell: ({ row }) => row.original.id },
  { accessorKey: "pickup_address", header: "מ", cell: ({ row }) => (
    <span className="max-w-[150px] truncate block">{row.original.pickup_address}</span>
  )},
  { accessorKey: "dropoff_address", header: "אל", cell: ({ row }) => (
    <span className="max-w-[150px] truncate block">{row.original.dropoff_address}</span>
  )},
  { accessorKey: "status", header: "סטטוס", cell: ({ row }) => (
    <StatusBadge status={row.original.status} />
  )},
  { accessorKey: "fee", header: "עמלה", cell: ({ row }) => formatCurrency(row.original.fee) },
  { accessorKey: "courier_name", header: "שליח", cell: ({ row }) => row.original.courier_name || "-" },
  { accessorKey: "created_at", header: "תאריך", cell: ({ row }) => formatDate(row.original.created_at) },
];

export default function DeliveryHistoryPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const page = Number(searchParams.get("page") || "1");
  const statusFilter = searchParams.get("status") || "";
  const dateFrom = searchParams.get("date_from") || "";
  const dateTo = searchParams.get("date_to") || "";

  const [localStatus, setLocalStatus] = useState(statusFilter);
  const [localDateFrom, setLocalDateFrom] = useState(dateFrom);
  const [localDateTo, setLocalDateTo] = useState(dateTo);

  const updateParams = (updates: Record<string, string>) => {
    const params = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(updates)) {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    }
    // אפס עמוד בשינוי פילטרים
    if (!("page" in updates)) {
      params.set("page", "1");
    }
    setSearchParams(params);
  };

  const applyFilters = () => {
    updateParams({
      status: localStatus,
      date_from: localDateFrom,
      date_to: localDateTo,
    });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["deliveries", "history", page, statusFilter, dateFrom, dateTo],
    queryFn: () =>
      getDeliveryHistory({
        page,
        status_filter: statusFilter,
        date_from: dateFrom,
        date_to: dateTo,
      }),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">היסטוריית משלוחים</h2>

      <div className="flex flex-wrap items-end gap-4 p-4 bg-white rounded-lg border border-border">
        <div className="space-y-1">
          <Label className="text-muted-foreground">סטטוס</Label>
          <Select
            value={localStatus || undefined}
            onValueChange={(v) => setLocalStatus(v === "__all__" ? "" : v)}
          >
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="הכל" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">הכל</SelectItem>
              {STATUS_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <DateRangePicker
          dateFrom={localDateFrom}
          dateTo={localDateTo}
          onDateFromChange={setLocalDateFrom}
          onDateToChange={setLocalDateTo}
        />
        <Button onClick={applyFilters}>
          סנן
        </Button>
      </div>

      <DataTable
        columns={columns}
        data={data?.items ?? []}
        isLoading={isLoading}
        emptyMessage="לא נמצאו משלוחים"
        onRowClick={(row) => navigate(`/deliveries/${row.id}`)}
      />
      {data && (
        <Pagination
          page={data.page}
          totalPages={data.total_pages}
          onPageChange={(p) => updateParams({ page: String(p) })}
        />
      )}
    </div>
  );
}
