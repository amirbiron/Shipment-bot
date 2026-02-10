import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getDeliveryDetail } from "@/api/deliveries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import StatusBadge from "@/components/shared/StatusBadge";
import { formatDateTime, formatCurrency } from "@/lib/format";
import { ArrowRight, MapPin, User, Clock } from "lucide-react";

export default function DeliveryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data, isLoading } = useQuery({
    queryKey: ["delivery", id],
    queryFn: () => getDeliveryDetail(Number(id)),
    enabled: !!id,
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        משלוח לא נמצא
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
          <ArrowRight className="h-4 w-4 me-1" />
          חזרה
        </Button>
        <h2 className="text-2xl font-bold">משלוח #{data.id}</h2>
        <StatusBadge status={data.status} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* איסוף */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <MapPin className="h-5 w-5 text-primary" />
              נקודת איסוף
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="font-medium">{data.pickup_address}</p>
            {data.pickup_contact_name && (
              <p className="text-sm text-muted-foreground">
                איש קשר: {data.pickup_contact_name}
              </p>
            )}
            {data.pickup_contact_phone && (
              <p className="text-sm text-muted-foreground" dir="ltr">
                {data.pickup_contact_phone}
              </p>
            )}
          </CardContent>
        </Card>

        {/* יעד */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <MapPin className="h-5 w-5 text-destructive" />
              נקודת מסירה
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="font-medium">{data.dropoff_address}</p>
            {data.dropoff_contact_name && (
              <p className="text-sm text-muted-foreground">
                איש קשר: {data.dropoff_contact_name}
              </p>
            )}
            {data.dropoff_contact_phone && (
              <p className="text-sm text-muted-foreground" dir="ltr">
                {data.dropoff_contact_phone}
              </p>
            )}
          </CardContent>
        </Card>

        {/* פרטים */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <User className="h-5 w-5" />
              פרטים
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex justify-between">
              <span className="text-muted-foreground">שולח</span>
              <span>{data.sender_name || "-"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">שליח</span>
              <span>{data.courier_name || "-"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">עמלה</span>
              <span className="font-medium">{formatCurrency(data.fee)}</span>
            </div>
          </CardContent>
        </Card>

        {/* ציר זמן */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Clock className="h-5 w-5" />
              ציר זמן
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex justify-between">
              <span className="text-muted-foreground">נוצר</span>
              <span>{formatDateTime(data.created_at)}</span>
            </div>
            {data.captured_at && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">נתפס</span>
                <span>{formatDateTime(data.captured_at)}</span>
              </div>
            )}
            {data.delivered_at && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">נמסר</span>
                <span>{formatDateTime(data.delivered_at)}</span>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
