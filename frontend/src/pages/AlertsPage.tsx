import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getAlertHistory,
  getWalletThreshold,
  updateWalletThreshold,
  createAlertStream,
  type AlertItem,
} from "@/api/alerts";
import { useAuthStore } from "@/store/auth";
import { useToast } from "@/components/ui/use-toast";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";
import StatusBadge from "@/components/shared/StatusBadge";
import {
  Bell,
  BellRing,
  Package,
  Truck,
  CheckCircle,
  XCircle,
  Wallet,
  Clock,
  Wifi,
  WifiOff,
} from "lucide-react";

// מיפוי סוגי התראות לאייקון, צבע ותווית
const ALERT_TYPE_CONFIG: Record<
  string,
  { icon: typeof Bell; variant: "default" | "secondary" | "destructive" | "success" | "warning" | "info"; label: string }
> = {
  delivery_created: { icon: Package, variant: "info", label: "משלוח חדש" },
  delivery_captured: { icon: Truck, variant: "default", label: "משלוח נתפס" },
  delivery_delivered: { icon: CheckCircle, variant: "success", label: "משלוח נמסר" },
  delivery_cancelled: { icon: XCircle, variant: "destructive", label: "משלוח בוטל" },
  wallet_threshold: { icon: Wallet, variant: "warning", label: "סף ארנק" },
  uncollected_shipment: { icon: Clock, variant: "warning", label: "לא נאסף" },
};

// מיפוי סוג התראה לסטטוס משלוח — לחישוב הסטטוס העדכני
const ALERT_TYPE_TO_STATUS: Record<string, string> = {
  delivery_created: "open",
  delivery_captured: "captured",
  delivery_delivered: "delivered",
  delivery_cancelled: "cancelled",
  uncollected_shipment: "open",
};

// סדר עדיפות סטטוסים — ערך גבוה יותר = סטטוס מתקדם יותר
const STATUS_PRIORITY: Record<string, number> = {
  open: 0,
  pending_approval: 1,
  captured: 2,
  in_progress: 3,
  delivered: 4,
  cancelled: 5,
};

function AlertCard({ alert, currentStatus }: { alert: AlertItem; currentStatus?: string }) {
  const config = ALERT_TYPE_CONFIG[alert.type] || {
    icon: Bell,
    variant: "secondary" as const,
    label: alert.type,
  };
  const Icon = config.icon;

  // הצגת סטטוס עדכני רק אם שונה מהסטטוס שההתראה מייצגת
  const alertImpliedStatus = ALERT_TYPE_TO_STATUS[alert.type];
  const showCurrentStatus = currentStatus && alertImpliedStatus && currentStatus !== alertImpliedStatus;

  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-border bg-white">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Icon className="h-5 w-5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={config.variant}>{config.label}</Badge>
          {showCurrentStatus && <StatusBadge status={currentStatus} />}
          <span className="text-xs text-muted-foreground">
            {formatDateTime(alert.timestamp)}
          </span>
        </div>
        <p className="text-sm mt-1">{alert.title}</p>
      </div>
    </div>
  );
}

export default function AlertsPage() {
  const { token } = useAuthStore();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // התראות חיות מ-SSE
  const [liveAlerts, setLiveAlerts] = useState<AlertItem[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // הגדרת סף ארנק
  const [thresholdInput, setThresholdInput] = useState("");
  const [thresholdDirty, setThresholdDirty] = useState(false);

  // היסטוריית התראות — refetchInterval מרענן כל דקה כדי שהתראות
  // uncollected_shipment ייעלמו אוטומטית אחרי שהמשלוח בוטל/נתפס ב-DB
  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["alerts", "history"],
    queryFn: () => getAlertHistory(50),
    refetchInterval: 60_000,
  });

  // סף ארנק
  const { data: thresholdData, isLoading: thresholdLoading } = useQuery({
    queryKey: ["alerts", "threshold"],
    queryFn: getWalletThreshold,
  });

  // סנכרון ערך סף לאינפוט
  useEffect(() => {
    if (thresholdData && !thresholdDirty) {
      setThresholdInput(
        thresholdData.threshold === 0 ? "" : String(thresholdData.threshold)
      );
    }
  }, [thresholdData, thresholdDirty]);

  // עדכון סף
  const thresholdMutation = useMutation({
    mutationFn: (threshold: number) => updateWalletThreshold(threshold),
    onSuccess: (data) => {
      toast({ title: data.message });
      setThresholdDirty(false);
      queryClient.invalidateQueries({ queryKey: ["alerts", "threshold"] });
    },
    onError: () => {
      toast({ title: "שגיאה בעדכון סף ארנק", variant: "destructive" });
    },
  });

  // חיבור SSE
  const handleNewAlert = useCallback(
    (alert: AlertItem) => {
      // התראות uncollected_shipment לא נשמרות ב-liveAlerts — הסינון שלהן
      // מול DB (האם המשלוח עדיין פתוח) מתבצע רק בבקאנד בנקודת /history.
      // invalidateQueries מרענן את ההיסטוריה המסוננת, וזה מספיק.
      if (alert.type !== "uncollected_shipment") {
        setLiveAlerts((prev) => [alert, ...prev].slice(0, 50));
      }
      // רענון היסטוריה — גם עבור uncollected, כדי שיופיע מההיסטוריה אם רלוונטי
      queryClient.invalidateQueries({ queryKey: ["alerts", "history"] });
    },
    [queryClient]
  );

  useEffect(() => {
    if (!token) return;

    const es = createAlertStream(
      token,
      (alert) => {
        setConnected(true);
        handleNewAlert(alert);
      },
      () => {
        setConnected(false);
      }
    );

    es.onopen = () => setConnected(true);
    esRef.current = es;

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [token, handleNewAlert]);

  const handleThresholdSubmit = () => {
    const val = thresholdInput.trim() === "" ? 0 : parseFloat(thresholdInput);
    if (isNaN(val) || val < 0) {
      toast({ title: "ערך לא תקין — יש להזין מספר חיובי או 0", variant: "destructive" });
      return;
    }
    thresholdMutation.mutate(val);
  };

  // שילוב התראות חיות + היסטוריה (מנקה כפילויות לפי timestamp+type)
  // + חישוב סטטוס עדכני לכל משלוח
  const { allAlerts, deliveryStatusMap } = (() => {
    const historyAlerts = history?.alerts ?? [];
    const combined = [...liveAlerts, ...historyAlerts];
    const seen = new Set<string>();
    const statusMap = new Map<number, string>();

    const deduped = combined
      .filter((a) => {
        const key = `${a.station_id}-${a.type}-${a.timestamp}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    // חישוב הסטטוס העדכני ביותר לכל משלוח
    for (const alert of deduped) {
      const deliveryId = alert.data?.delivery_id as number | undefined;
      const status = ALERT_TYPE_TO_STATUS[alert.type];
      if (deliveryId == null || !status) continue;

      const existing = statusMap.get(deliveryId);
      if (!existing || (STATUS_PRIORITY[status] ?? 0) > (STATUS_PRIORITY[existing] ?? 0)) {
        statusMap.set(deliveryId, status);
      }
    }

    // סינון התראות uncollected_shipment למשלוחים שכבר לא פתוחים —
    // התראות SSE עוקפות את סינון הבקאנד, לכן מסננים גם בפרונט
    const filtered = deduped.filter((a) => {
      if (a.type !== "uncollected_shipment") return true;
      const deliveryId = a.data?.delivery_id as number | undefined;
      if (deliveryId == null) return true;
      const current = statusMap.get(deliveryId);
      return !current || current === "open";
    });

    return { allAlerts: filtered, deliveryStatusMap: statusMap };
  })();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <BellRing className="h-6 w-6" />
          התראות בזמן אמת
        </h2>
        <div className="flex items-center gap-2 text-sm">
          {connected ? (
            <>
              <Wifi className="h-4 w-4 text-green-600" />
              <span className="text-green-600">מחובר</span>
            </>
          ) : (
            <>
              <WifiOff className="h-4 w-4 text-muted-foreground" />
              <span className="text-muted-foreground">לא מחובר</span>
            </>
          )}
        </div>
      </div>

      {/* הגדרת סף ארנק */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Wallet className="h-5 w-5" />
            סף התראת ארנק
          </CardTitle>
        </CardHeader>
        <CardContent>
          {thresholdLoading ? (
            <Skeleton className="h-10 w-64" />
          ) : (
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label className="text-muted-foreground">
                  סף מינימלי (0 = מבוטל)
                </Label>
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    min="0"
                    step="1"
                    className="w-40"
                    dir="ltr"
                    placeholder="0"
                    value={thresholdInput}
                    onChange={(e) => {
                      setThresholdInput(e.target.value);
                      setThresholdDirty(true);
                    }}
                  />
                  <span className="text-muted-foreground">&#8362;</span>
                </div>
              </div>
              <Button
                onClick={handleThresholdSubmit}
                disabled={thresholdMutation.isPending}
              >
                {thresholdMutation.isPending ? "שומר..." : "שמור"}
              </Button>
              {thresholdData && thresholdData.threshold > 0 && (
                <span className="text-sm text-muted-foreground">
                  סף נוכחי: {thresholdData.threshold.toFixed(2)}&#8362;
                </span>
              )}
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-2">
            כשיתרת הארנק יורדת מתחת לסף שנקבע, תתקבל התראה בזמן אמת.
          </p>
        </CardContent>
      </Card>

      {/* רשימת התראות */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">
            התראות ({allAlerts.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          ) : allAlerts.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Bell className="h-10 w-10 mx-auto mb-2 opacity-40" />
              <p>אין התראות</p>
            </div>
          ) : (
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {allAlerts.map((alert, i) => {
                const deliveryId = alert.data?.delivery_id as number | undefined;
                const currentStatus = deliveryId != null ? deliveryStatusMap.get(deliveryId) : undefined;
                return (
                  <AlertCard
                    key={`${alert.type}-${alert.timestamp}-${i}`}
                    alert={alert}
                    currentStatus={currentStatus}
                  />
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
