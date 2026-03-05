import { useState, useEffect } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getSenderDetail,
  getSenderDeliveries,
  type SenderDeliveryItem,
} from "@/api/senders";
import DataTable from "@/components/shared/DataTable";
import Pagination from "@/components/shared/Pagination";
import StatusBadge from "@/components/shared/StatusBadge";
import StatCard from "@/components/shared/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatCurrency } from "@/lib/format";
import {
  ArrowRight,
  Package,
  CheckCircle,
  XCircle,
  TrendingUp,
  Clock,
  User,
} from "lucide-react";

const STATUS_OPTIONS = [
  { value: "open", label: "פתוח" },
  { value: "captured", label: "נתפס" },
  { value: "in_progress", label: "בדרך" },
  { value: "delivered", label: "נמסר" },
  { value: "cancelled", label: "בוטל" },
];

const deliveryColumns: ColumnDef<SenderDeliveryItem, unknown>[] = [
  { accessorKey: "id", header: "#", cell: ({ row }) => row.original.id },
  {
    accessorKey: "pickup_address",
    header: "מ",
    cell: ({ row }) => (
      <span className="max-w-[150px] truncate block">
        {row.original.pickup_address}
      </span>
    ),
  },
  {
    accessorKey: "dropoff_address",
    header: "אל",
    cell: ({ row }) => (
      <span className="max-w-[150px] truncate block">
        {row.original.dropoff_address}
      </span>
    ),
  },
  {
    accessorKey: "status",
    header: "סטטוס",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    accessorKey: "fee",
    header: "עמלה",
    cell: ({ row }) => formatCurrency(row.original.fee),
  },
  {
    accessorKey: "courier_name",
    header: "שליח",
    cell: ({ row }) => row.original.courier_name || "-",
  },
  {
    accessorKey: "created_at",
    header: "תאריך",
    cell: ({ row }) => formatDate(row.original.created_at),
  },
];

export default function SenderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const page = Number(searchParams.get("page") || "1");
  const statusFilter = searchParams.get("status") || "";
  const [localStatus, setLocalStatus] = useState(statusFilter);

  useEffect(() => {
    setLocalStatus(statusFilter);
  }, [statusFilter]);

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

  const { data: sender, isLoading: senderLoading } = useQuery({
    queryKey: ["sender", id],
    queryFn: () => getSenderDetail(Number(id)),
    enabled: !!id,
  });

  const { data: deliveries, isLoading: deliveriesLoading } = useQuery({
    queryKey: ["sender", id, "deliveries", page, statusFilter],
    queryFn: () =>
      getSenderDeliveries(Number(id), {
        page,
        status_filter: statusFilter || undefined,
      }),
    enabled: !!id,
  });

  if (senderLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!sender) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        שולח לא נמצא
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* כותרת */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate("/senders")}>
          <ArrowRight className="h-4 w-4 me-1" />
          חזרה
        </Button>
        <h2 className="text-2xl font-bold">{sender.name}</h2>
      </div>

      {/* פרטי שולח */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <User className="h-5 w-5" />
            פרטי שולח
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-muted-foreground">טלפון</span>
            <p className="font-medium" dir="ltr">
              {sender.phone_masked}
            </p>
          </div>
          <div>
            <span className="text-muted-foreground">פלטפורמה</span>
            <p className="font-medium">{sender.platform || "-"}</p>
          </div>
          <div>
            <span className="text-muted-foreground">הצטרף</span>
            <p className="font-medium">{formatDate(sender.created_at)}</p>
          </div>
          <div>
            <span className="text-muted-foreground">עמלה ממוצעת</span>
            <p className="font-medium">{formatCurrency(sender.avg_fee)}</p>
          </div>
        </CardContent>
      </Card>

      {/* סטטיסטיקות */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          icon={Package}
          label="סה״כ משלוחים"
          value={sender.deliveries_count}
        />
        <StatCard
          icon={CheckCircle}
          label="נמסרו"
          value={sender.delivered_count}
        />
        <StatCard
          icon={XCircle}
          label="בוטלו"
          value={sender.cancelled_count}
        />
        <StatCard
          icon={TrendingUp}
          label="מחזור"
          value={formatCurrency(sender.total_volume)}
        />
      </div>

      {/* תאריכים */}
      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Clock className="h-5 w-5 text-muted-foreground" />
            <div>
              <p className="text-sm text-muted-foreground">משלוח ראשון</p>
              <p className="font-medium">
                {formatDate(sender.first_delivery_at)}
              </p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <Clock className="h-5 w-5 text-muted-foreground" />
            <div>
              <p className="text-sm text-muted-foreground">משלוח אחרון</p>
              <p className="font-medium">
                {formatDate(sender.last_delivery_at)}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* משלוחים */}
      <div className="space-y-4">
        <h3 className="text-xl font-bold">משלוחים</h3>

        <div className="flex flex-wrap items-end gap-4 p-4 bg-white rounded-lg border border-border">
          <div className="space-y-1">
            <Label className="text-muted-foreground">סטטוס</Label>
            <Select
              value={localStatus || undefined}
              onValueChange={(v) => {
                const val = v === "__all__" ? "" : v;
                setLocalStatus(val);
                updateParams({ status: val });
              }}
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
        </div>

        <DataTable
          columns={deliveryColumns}
          data={deliveries?.items ?? []}
          isLoading={deliveriesLoading}
          emptyMessage="אין משלוחים"
          onRowClick={(row) => navigate(`/deliveries/${row.id}`)}
        />
        {deliveries && (
          <Pagination
            page={deliveries.page}
            totalPages={deliveries.total_pages}
            onPageChange={(p) => updateParams({ page: String(p) })}
          />
        )}
      </div>
    </div>
  );
}
