import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getMultiStationDashboard,
  type StationSummary,
} from "@/api/stations";
import { useAuthStore } from "@/store/auth";
import DataTable from "@/components/shared/DataTable";
import StatCard from "@/components/shared/StatCard";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCurrency } from "@/lib/format";
import {
  Building2,
  Truck,
  Package,
  CheckCircle,
  TrendingUp,
  Wallet,
  Users,
  Ban,
} from "lucide-react";

const columns: ColumnDef<StationSummary, unknown>[] = [
  {
    accessorKey: "station_name",
    header: "תחנה",
    cell: ({ row }) => (
      <span className="font-medium">{row.original.station_name}</span>
    ),
  },
  {
    accessorKey: "active_deliveries_count",
    header: "פעילים",
    cell: ({ row }) => row.original.active_deliveries_count,
  },
  {
    accessorKey: "today_deliveries_count",
    header: "היום",
    cell: ({ row }) => row.original.today_deliveries_count,
  },
  {
    accessorKey: "today_delivered_count",
    header: "נמסרו",
    cell: ({ row }) => row.original.today_delivered_count,
  },
  {
    accessorKey: "today_revenue",
    header: "הכנסות היום",
    cell: ({ row }) => formatCurrency(row.original.today_revenue),
  },
  {
    accessorKey: "wallet_balance",
    header: "יתרת ארנק",
    cell: ({ row }) => formatCurrency(row.original.wallet_balance),
  },
  {
    accessorKey: "commission_rate",
    header: "עמלה",
    cell: ({ row }) =>
      `${(row.original.commission_rate * 100).toFixed(0)}%`,
  },
  {
    accessorKey: "active_dispatchers_count",
    header: "סדרנים",
    cell: ({ row }) => row.original.active_dispatchers_count,
  },
  {
    accessorKey: "blacklisted_count",
    header: "חסומים",
    cell: ({ row }) => row.original.blacklisted_count,
  },
];

export default function MultiStationPage() {
  const { stationId } = useAuthStore();

  const { data, isLoading } = useQuery({
    queryKey: ["stations", "dashboard"],
    queryFn: getMultiStationDashboard,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">דשבורד מולטי-תחנה</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!data) return null;

  const { stations, totals } = data;

  // אם יש תחנה אחת בלבד — הצגת הודעה מתאימה
  if (stations.length <= 1) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Building2 className="h-6 w-6" />
          דשבורד מולטי-תחנה
        </h2>
        <div className="text-center py-12 text-muted-foreground">
          <Building2 className="h-10 w-10 mx-auto mb-2 opacity-40" />
          <p>דשבורד זה מציג השוואה בין מספר תחנות.</p>
          <p>{stations.length === 0 ? "לא נמצאו תחנות." : "כרגע יש לך תחנה אחת בלבד."}</p>
        </div>
      </div>
    );
  }

  const summaryStats = [
    {
      icon: Truck,
      label: "משלוחים פעילים (סה״כ)",
      value: totals.total_active_deliveries,
    },
    {
      icon: Package,
      label: "משלוחים היום (סה״כ)",
      value: totals.total_today_deliveries,
    },
    {
      icon: CheckCircle,
      label: "נמסרו היום (סה״כ)",
      value: totals.total_today_delivered,
    },
    {
      icon: TrendingUp,
      label: "הכנסות היום (סה״כ)",
      value: formatCurrency(totals.total_today_revenue),
    },
    {
      icon: Wallet,
      label: "יתרת ארנק (סה״כ)",
      value: formatCurrency(totals.total_wallet_balance),
    },
    {
      icon: Users,
      label: "סדרנים פעילים (סה״כ)",
      value: totals.total_active_dispatchers,
    },
    {
      icon: Ban,
      label: "חסומים (סה״כ)",
      value: totals.total_blacklisted,
    },
  ];

  // הוספת badge לתחנה הנוכחית
  const stationsWithCurrent = columns.map((col) => {
    if ("accessorKey" in col && col.accessorKey === "station_name") {
      return {
        ...col,
        cell: ({ row }: { row: { original: StationSummary } }) => (
          <span className="font-medium flex items-center gap-2">
            {row.original.station_name}
            {row.original.station_id === stationId && (
              <Badge variant="secondary">נוכחית</Badge>
            )}
          </span>
        ),
      } as ColumnDef<StationSummary, unknown>;
    }
    return col;
  });

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold flex items-center gap-2">
        <Building2 className="h-6 w-6" />
        דשבורד מולטי-תחנה
        <Badge variant="secondary">{stations.length} תחנות</Badge>
      </h2>

      {/* סטטיסטיקות מצטברות */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {summaryStats.map((stat) => (
          <StatCard
            key={stat.label}
            icon={stat.icon}
            label={stat.label}
            value={stat.value}
          />
        ))}
      </div>

      {/* טבלת השוואה */}
      <DataTable
        columns={stationsWithCurrent}
        data={stations}
        isLoading={false}
        emptyMessage="אין תחנות להצגה"
      />
    </div>
  );
}
