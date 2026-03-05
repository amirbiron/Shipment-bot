import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getAuditLog,
  getAuditActionTypes,
  type AuditLogItem,
} from "@/api/audit";
import DataTable from "@/components/shared/DataTable";
import Pagination from "@/components/shared/Pagination";
import DateRangePicker from "@/components/shared/DateRangePicker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatDateTime } from "@/lib/format";

const columns: ColumnDef<AuditLogItem, unknown>[] = [
  {
    accessorKey: "created_at",
    header: "תאריך",
    cell: ({ row }) => formatDateTime(row.original.created_at),
  },
  {
    accessorKey: "action_label",
    header: "פעולה",
    cell: ({ row }) => (
      <Badge variant="secondary">{row.original.action_label}</Badge>
    ),
  },
  {
    accessorKey: "actor_name",
    header: "מבצע",
    cell: ({ row }) => row.original.actor_name,
  },
  {
    accessorKey: "target_name",
    header: "יעד",
    cell: ({ row }) => row.original.target_name || "-",
  },
];

export default function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const page = Number(searchParams.get("page") || "1");
  const actionFilter = searchParams.get("action") || "";
  const dateFrom = searchParams.get("date_from") || "";
  const dateTo = searchParams.get("date_to") || "";

  const [localAction, setLocalAction] = useState(actionFilter);
  const [localDateFrom, setLocalDateFrom] = useState(dateFrom);
  const [localDateTo, setLocalDateTo] = useState(dateTo);
  const [selectedEntry, setSelectedEntry] = useState<AuditLogItem | null>(null);

  useEffect(() => {
    setLocalAction(actionFilter);
    setLocalDateFrom(dateFrom);
    setLocalDateTo(dateTo);
  }, [actionFilter, dateFrom, dateTo]);

  const updateParams = (updates: Record<string, string>) => {
    const params = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(updates)) {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    }
    if (!("page" in updates)) {
      params.set("page", "1");
    }
    setSearchParams(params);
  };

  const applyFilters = () => {
    updateParams({
      action: localAction,
      date_from: localDateFrom,
      date_to: localDateTo,
    });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["audit", page, actionFilter, dateFrom, dateTo],
    queryFn: () =>
      getAuditLog({
        page,
        action: actionFilter || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      }),
  });

  const { data: actionTypes } = useQuery({
    queryKey: ["audit", "actions"],
    queryFn: getAuditActionTypes,
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">יומן ביקורת</h2>

      {/* פילטרים */}
      <div className="flex flex-wrap items-end gap-4 p-4 bg-white rounded-lg border border-border">
        <div className="space-y-1">
          <Label className="text-muted-foreground">סוג פעולה</Label>
          <Select
            value={localAction || undefined}
            onValueChange={(v) => setLocalAction(v === "__all__" ? "" : v)}
          >
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="הכל" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">הכל</SelectItem>
              {actionTypes?.map((at) => (
                <SelectItem key={at.value} value={at.value}>
                  {at.label}
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
        <Button onClick={applyFilters}>סנן</Button>
      </div>

      {/* טבלה */}
      <DataTable
        columns={columns}
        data={data?.items ?? []}
        isLoading={isLoading}
        emptyMessage="אין רשומות ביומן הביקורת"
        onRowClick={(row) => setSelectedEntry(row)}
      />
      {data && (
        <Pagination
          page={data.page}
          totalPages={data.total_pages}
          onPageChange={(p) => updateParams({ page: String(p) })}
        />
      )}

      {/* דיאלוג פרטים */}
      <Dialog
        open={!!selectedEntry}
        onOpenChange={(open) => !open && setSelectedEntry(null)}
      >
        <DialogContent className="max-w-lg" dir="rtl">
          <DialogHeader>
            <DialogTitle>פרטי רשומה</DialogTitle>
          </DialogHeader>
          {selectedEntry && (
            <div className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">תאריך</span>
                <span>{formatDateTime(selectedEntry.created_at)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">פעולה</span>
                <Badge variant="secondary">
                  {selectedEntry.action_label}
                </Badge>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">מבצע</span>
                <span>{selectedEntry.actor_name}</span>
              </div>
              {selectedEntry.target_name && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">יעד</span>
                  <span>{selectedEntry.target_name}</span>
                </div>
              )}
              {selectedEntry.details &&
                Object.keys(selectedEntry.details).length > 0 && (
                  <div className="space-y-1">
                    <span className="text-muted-foreground">פרטים נוספים</span>
                    <pre
                      className="bg-muted p-3 rounded-md text-xs overflow-auto max-h-48"
                      dir="ltr"
                    >
                      {JSON.stringify(selectedEntry.details, null, 2)}
                    </pre>
                  </div>
                )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
