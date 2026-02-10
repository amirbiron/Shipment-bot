import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getCollectionReport,
  exportCollectionCsv,
  getRevenueReport,
  type CollectionReportItem,
} from "@/api/reports";
import DataTable from "@/components/shared/DataTable";
import DateRangePicker from "@/components/shared/DateRangePicker";
import ExportButton from "@/components/shared/ExportButton";
import StatCard from "@/components/shared/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { formatCurrency, formatDate } from "@/lib/format";
import { FileText, TrendingUp, CreditCard, ArrowDownCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type Tab = "collection" | "revenue";

const collectionColumns: ColumnDef<CollectionReportItem, unknown>[] = [
  { accessorKey: "driver_name", header: "שם נהג" },
  {
    accessorKey: "total_debt",
    header: 'סה"כ חוב',
    cell: ({ row }) => formatCurrency(row.original.total_debt),
  },
  { accessorKey: "charge_count", header: "מספר חיובים" },
];

export default function ReportsPage() {
  const { toast } = useToast();
  const [tab, setTab] = useState<Tab>("collection");
  const [cycleStart, setCycleStart] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const { data: collectionData, isLoading: collectionLoading } = useQuery({
    queryKey: ["reports", "collection", cycleStart],
    queryFn: () => getCollectionReport(cycleStart || undefined),
    enabled: tab === "collection",
  });

  const { data: revenueData, isLoading: revenueLoading } = useQuery({
    queryKey: ["reports", "revenue", dateFrom, dateTo],
    queryFn: () => getRevenueReport(dateFrom || undefined, dateTo || undefined),
    enabled: tab === "revenue",
  });

  const handleExportCsv = async () => {
    try {
      await exportCollectionCsv(cycleStart || undefined);
    } catch {
      toast({ title: "שגיאה בייצוא", variant: "destructive" });
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">דוחות</h2>

      {/* טאבים */}
      <div className="flex gap-2">
        <Button
          variant={tab === "collection" ? "default" : "outline"}
          onClick={() => setTab("collection")}
        >
          דוח גבייה
        </Button>
        <Button
          variant={tab === "revenue" ? "default" : "outline"}
          onClick={() => setTab("revenue")}
        >
          דוח הכנסות
        </Button>
      </div>

      {/* דוח גבייה */}
      {tab === "collection" && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <FileText className="h-5 w-5" />
              דוח גבייה
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-1">
                <label className="text-sm text-muted-foreground">תחילת מחזור</label>
                <input
                  type="date"
                  value={cycleStart}
                  onChange={(e) => setCycleStart(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <ExportButton onExport={handleExportCsv} />
            </div>

            {collectionData && (
              <p className="text-sm text-muted-foreground">
                מחזור: {formatDate(collectionData.cycle_start)} — {formatDate(collectionData.cycle_end)}
              </p>
            )}

            <DataTable
              columns={collectionColumns}
              data={collectionData?.items ?? []}
              isLoading={collectionLoading}
              emptyMessage="אין חובות לתקופה זו"
            />

            {collectionData && collectionData.items.length > 0 && (
              <div className="text-left border-t border-border pt-3 font-medium">
                סה&quot;כ חוב: {formatCurrency(collectionData.total_debt)}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* דוח הכנסות */}
      {tab === "revenue" && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <TrendingUp className="h-5 w-5" />
              דוח הכנסות
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <DateRangePicker
              dateFrom={dateFrom}
              dateTo={dateTo}
              onDateFromChange={setDateFrom}
              onDateToChange={setDateTo}
            />

            {revenueLoading ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="h-24 bg-muted animate-pulse rounded-lg" />
                ))}
              </div>
            ) : revenueData ? (
              <>
                {revenueData.date_from && (
                  <p className="text-sm text-muted-foreground">
                    תקופה: {formatDate(revenueData.date_from)} — {formatDate(revenueData.date_to)}
                  </p>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <StatCard
                    icon={CreditCard}
                    label="עמלות"
                    value={formatCurrency(revenueData.total_commissions)}
                  />
                  <StatCard
                    icon={FileText}
                    label="חיובים ידניים"
                    value={formatCurrency(revenueData.total_manual_charges)}
                  />
                  <StatCard
                    icon={ArrowDownCircle}
                    label="משיכות"
                    value={formatCurrency(revenueData.total_withdrawals)}
                  />
                  <StatCard
                    icon={TrendingUp}
                    label='סה"כ נטו'
                    value={
                      <span className={cn(revenueData.net_total >= 0 ? "text-success" : "text-destructive")}>
                        {formatCurrency(revenueData.net_total)}
                      </span>
                    }
                  />
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
