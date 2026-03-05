import { useEffect, useState, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getSenders,
  getTopSenders,
  type Sender,
  type TopSender,
} from "@/api/senders";
import DataTable from "@/components/shared/DataTable";
import Pagination from "@/components/shared/Pagination";
import StatCard from "@/components/shared/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatCurrency } from "@/lib/format";
import { Users, Trophy, Package, TrendingUp } from "lucide-react";

const SORT_OPTIONS = [
  { value: "deliveries_count", label: "מספר משלוחים" },
  { value: "total_volume", label: "מחזור" },
  { value: "last_delivery", label: "משלוח אחרון" },
  { value: "name", label: "שם" },
];

const senderColumns: ColumnDef<Sender, unknown>[] = [
  { accessorKey: "name", header: "שם" },
  {
    accessorKey: "phone_masked",
    header: "טלפון",
    cell: ({ row }) => <span dir="ltr">{row.original.phone_masked}</span>,
  },
  { accessorKey: "platform", header: "פלטפורמה" },
  {
    accessorKey: "deliveries_count",
    header: "משלוחים",
    cell: ({ row }) => row.original.deliveries_count,
  },
  {
    accessorKey: "delivered_count",
    header: "נמסרו",
    cell: ({ row }) => row.original.delivered_count,
  },
  {
    accessorKey: "active_deliveries_count",
    header: "פעילים",
    cell: ({ row }) => row.original.active_deliveries_count,
  },
  {
    accessorKey: "total_volume",
    header: "מחזור",
    cell: ({ row }) => formatCurrency(row.original.total_volume),
  },
  {
    accessorKey: "last_delivery_at",
    header: "משלוח אחרון",
    cell: ({ row }) => formatDate(row.original.last_delivery_at),
  },
];

export default function SendersPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const page = Number(searchParams.get("page") || "1");
  const search = searchParams.get("search") || "";
  const sortBy = searchParams.get("sort_by") || "deliveries_count";
  const sortOrder = searchParams.get("sort_order") || "desc";

  const [localSearch, setLocalSearch] = useState(search);

  useEffect(() => {
    setLocalSearch(search);
  }, [search]);

  const updateParams = (updates: Record<string, string>) => {
    const params = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(updates)) {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    }
    // איפוס עמוד בשינוי פילטרים
    if (!("page" in updates)) {
      params.set("page", "1");
    }
    setSearchParams(params);
  };

  const applySearch = () => {
    updateParams({ search: localSearch });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["senders", page, search, sortBy, sortOrder],
    queryFn: () =>
      getSenders({
        page,
        search: search || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
  });

  const { data: topSenders } = useQuery({
    queryKey: ["senders", "top"],
    queryFn: () => getTopSenders(5),
  });

  const topColumns = useMemo<ColumnDef<TopSender, unknown>[]>(
    () => [
      {
        id: "rank",
        header: "#",
        cell: ({ row }) => row.index + 1,
      },
      { accessorKey: "name", header: "שם" },
      {
        accessorKey: "phone_masked",
        header: "טלפון",
        cell: ({ row }) => <span dir="ltr">{row.original.phone_masked}</span>,
      },
      {
        accessorKey: "delivered_count",
        header: "נמסרו",
        cell: ({ row }) => row.original.delivered_count,
      },
      {
        accessorKey: "total_volume",
        header: "מחזור",
        cell: ({ row }) => formatCurrency(row.original.total_volume),
      },
    ],
    []
  );

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">שולחים</h2>

      {/* סטטיסטיקות */}
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard icon={Users} label="סה״כ שולחים" value={data.total} />
          <StatCard
            icon={Package}
            label="סה״כ משלוחים"
            value={data.total_deliveries}
          />
          <StatCard
            icon={TrendingUp}
            label="שולחים פעילים"
            value={data.active_senders_count}
          />
          <StatCard
            icon={Trophy}
            label="שולחים מובילים"
            value={topSenders?.length ?? 0}
          />
        </div>
      )}

      {/* חיפוש ומיון */}
      <div className="flex flex-wrap items-end gap-4 p-4 bg-white rounded-lg border border-border">
        <div className="space-y-1 flex-1 min-w-[200px]">
          <Label className="text-muted-foreground">חיפוש</Label>
          <div className="flex gap-2">
            <Input
              placeholder="חיפוש לפי שם..."
              value={localSearch}
              onChange={(e) => setLocalSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applySearch()}
            />
            <Button onClick={applySearch}>חפש</Button>
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-muted-foreground">מיון לפי</Label>
          <Select
            value={sortBy}
            onValueChange={(v) => updateParams({ sort_by: v })}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-muted-foreground">כיוון</Label>
          <Select
            value={sortOrder}
            onValueChange={(v) => updateParams({ sort_order: v })}
          >
            <SelectTrigger className="w-[120px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="desc">יורד</SelectItem>
              <SelectItem value="asc">עולה</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* טבלת שולחים */}
      <DataTable
        columns={senderColumns}
        data={data?.items ?? []}
        isLoading={isLoading}
        emptyMessage="לא נמצאו שולחים"
        onRowClick={(row) => navigate(`/senders/${row.user_id}`)}
      />
      {data && (
        <Pagination
          page={data.page}
          totalPages={data.total_pages}
          onPageChange={(p) => updateParams({ page: String(p) })}
        />
      )}

      {/* שולחים מובילים */}
      {topSenders && topSenders.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Trophy className="h-5 w-5 text-yellow-500" />
              שולחים מובילים
            </CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              columns={topColumns}
              data={topSenders}
              isLoading={false}
              emptyMessage="אין נתונים"
              onRowClick={(row) => navigate(`/senders/${row.user_id}`)}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
