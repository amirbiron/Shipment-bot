import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getStationSettings,
  updateStationSettings,
  type OperatingHoursDay,
} from "@/api/settings";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { Settings, Clock, MapPin, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const DAYS_OF_WEEK = [
  { key: "sunday", label: "ראשון" },
  { key: "monday", label: "שני" },
  { key: "tuesday", label: "שלישי" },
  { key: "wednesday", label: "רביעי" },
  { key: "thursday", label: "חמישי" },
  { key: "friday", label: "שישי" },
  { key: "saturday", label: "שבת" },
] as const;

type DayKey = (typeof DAYS_OF_WEEK)[number]["key"];

interface DayHours {
  enabled: boolean;
  open: string;
  close: string;
}

function parseDayHours(
  hours: Record<string, OperatingHoursDay | null> | null
): Record<DayKey, DayHours> {
  const result = {} as Record<DayKey, DayHours>;
  for (const day of DAYS_OF_WEEK) {
    const val = hours?.[day.key];
    result[day.key] = val
      ? { enabled: true, open: val.open, close: val.close }
      : { enabled: false, open: "08:00", close: "18:00" };
  }
  return result;
}

function buildOperatingHours(
  days: Record<DayKey, DayHours>
): Record<string, OperatingHoursDay | null> {
  const result: Record<string, OperatingHoursDay | null> = {};
  for (const day of DAYS_OF_WEEK) {
    const d = days[day.key];
    result[day.key] = d.enabled ? { open: d.open, close: d.close } : null;
  }
  return result;
}

export default function StationSettingsPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [logoUrl, setLogoUrl] = useState("");
  const [days, setDays] = useState<Record<DayKey, DayHours>>(
    parseDayHours(null)
  );
  const [areas, setAreas] = useState<string[]>([]);
  const [newArea, setNewArea] = useState("");
  const [isDirty, setIsDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["stationSettings"],
    queryFn: getStationSettings,
  });

  // טעינת ערכים מהשרת — רק אם המשתמש לא ערך את הטופס
  useEffect(() => {
    if (data && !isDirty) {
      setName(data.name || "");
      setDescription(data.description || "");
      setLogoUrl(data.logo_url || "");
      setDays(parseDayHours(data.operating_hours));
      setAreas(data.service_areas || []);
    }
  }, [data, isDirty]);

  const markDirty = () => setIsDirty(true);

  const mutation = useMutation({
    mutationFn: () =>
      updateStationSettings({
        name: name || undefined,
        description: description || undefined,
        clear_description: !description && !!data?.description,
        operating_hours: buildOperatingHours(days),
        service_areas: areas.length > 0 ? areas : undefined,
        clear_service_areas: areas.length === 0 && (data?.service_areas?.length ?? 0) > 0,
        logo_url: logoUrl || undefined,
        clear_logo_url: !logoUrl && !!data?.logo_url,
      }),
    onSuccess: (result) => {
      toast({
        title: result.success ? "ההגדרות עודכנו" : "שגיאה",
        description: result.message,
        variant: result.success ? "default" : "destructive",
      });
      if (result.success) {
        setIsDirty(false);
        queryClient.invalidateQueries({ queryKey: ["stationSettings"] });
      }
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה, נסה שוב", variant: "destructive" });
    },
  });

  const updateDay = (key: DayKey, update: Partial<DayHours>) => {
    setDays((prev) => ({ ...prev, [key]: { ...prev[key], ...update } }));
    markDirty();
  };

  const addArea = () => {
    const trimmed = newArea.trim();
    if (trimmed && !areas.includes(trimmed)) {
      setAreas((prev) => [...prev, trimmed]);
      setNewArea("");
      markDirty();
    }
  };

  const removeArea = (index: number) => {
    setAreas((prev) => prev.filter((_, i) => i !== index));
    markDirty();
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">הגדרות תחנה</h2>
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">הגדרות תחנה</h2>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
        className="space-y-6"
      >
        {/* פרטי תחנה */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Settings className="h-5 w-5" />
              פרטי תחנה
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="station-name">שם התחנה</Label>
              <Input
                id="station-name"
                value={name}
                onChange={(e) => { setName(e.target.value); markDirty(); }}
                placeholder="שם התחנה"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="station-desc">תיאור</Label>
              <Textarea
                id="station-desc"
                value={description}
                onChange={(e) => { setDescription(e.target.value); markDirty(); }}
                placeholder="תיאור התחנה (אופציונלי)"
                rows={3}
                className="resize-none"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="station-logo">קישור ללוגו</Label>
              <Input
                id="station-logo"
                value={logoUrl}
                onChange={(e) => { setLogoUrl(e.target.value); markDirty(); }}
                placeholder="https://example.com/logo.png"
                dir="ltr"
              />
            </div>
          </CardContent>
        </Card>

        {/* שעות פעילות */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Clock className="h-5 w-5" />
              שעות פעילות
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {DAYS_OF_WEEK.map((day) => (
              <div key={day.key} className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2 w-24">
                  <Switch
                    checked={days[day.key].enabled}
                    onCheckedChange={(checked) =>
                      updateDay(day.key, { enabled: checked })
                    }
                  />
                  <span className="text-sm font-medium">{day.label}</span>
                </div>
                {days[day.key].enabled && (
                  <div className="flex items-center gap-2">
                    <Input
                      type="time"
                      value={days[day.key].open}
                      onChange={(e) =>
                        updateDay(day.key, { open: e.target.value })
                      }
                      className="w-28"
                      dir="ltr"
                    />
                    <span className="text-sm text-muted-foreground">עד</span>
                    <Input
                      type="time"
                      value={days[day.key].close}
                      onChange={(e) =>
                        updateDay(day.key, { close: e.target.value })
                      }
                      className="w-28"
                      dir="ltr"
                    />
                  </div>
                )}
                {!days[day.key].enabled && (
                  <span className="text-sm text-muted-foreground">סגור</span>
                )}
              </div>
            ))}
          </CardContent>
        </Card>

        {/* אזורי שירות */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <MapPin className="h-5 w-5" />
              אזורי שירות
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex gap-2">
              <Input
                value={newArea}
                onChange={(e) => setNewArea(e.target.value)}
                placeholder="הוסף אזור שירות"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addArea();
                  }
                }}
              />
              <Button type="button" variant="outline" onClick={addArea}>
                הוסף
              </Button>
            </div>
            {areas.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {areas.map((area, i) => (
                  <Badge key={i} variant="secondary" className="gap-1 text-sm">
                    {area}
                    <button
                      type="button"
                      onClick={() => removeArea(i)}
                      className="hover:text-destructive"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}
            {areas.length === 0 && (
              <p className="text-sm text-muted-foreground">
                לא הוגדרו אזורי שירות
              </p>
            )}
          </CardContent>
        </Card>

        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "שומר..." : "שמור שינויים"}
        </Button>
      </form>
    </div>
  );
}
