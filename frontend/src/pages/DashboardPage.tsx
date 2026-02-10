import { useQuery } from "@tanstack/react-query";
import { getDashboard } from "@/api/dashboard";
import StatCard from "@/components/shared/StatCard";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCurrency } from "@/lib/format";
import {
  Truck,
  Package,
  CheckCircle,
  TrendingUp,
  Wallet,
  Percent,
  Users,
  Ban,
} from "lucide-react";

export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">דשבורד</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const stats = [
    { icon: Truck, label: "משלוחים פעילים", value: data.active_deliveries_count },
    { icon: Package, label: "משלוחים היום", value: data.today_deliveries_count },
    { icon: CheckCircle, label: "נמסרו היום", value: data.today_delivered_count },
    { icon: TrendingUp, label: "הכנסות היום", value: formatCurrency(data.today_revenue) },
    { icon: Wallet, label: "יתרת ארנק", value: formatCurrency(data.wallet_balance) },
    { icon: Percent, label: "עמלה", value: `${(data.commission_rate * 100).toFixed(0)}%` },
    { icon: Users, label: "סדרנים פעילים", value: data.active_dispatchers_count },
    { icon: Ban, label: "חסומים", value: data.blacklisted_count },
  ];

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">דשבורד — {data.station_name}</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => (
          <StatCard
            key={stat.label}
            icon={stat.icon}
            label={stat.label}
            value={stat.value}
          />
        ))}
      </div>
    </div>
  );
}
