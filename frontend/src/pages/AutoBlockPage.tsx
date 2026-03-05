import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getAutoBlockSettings, updateAutoBlockSettings } from "@/api/autoBlock";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/use-toast";
import { ShieldBan } from "lucide-react";

export default function AutoBlockPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [enabled, setEnabled] = useState(false);
  const [graceMonths, setGraceMonths] = useState("3");
  const [minDebt, setMinDebt] = useState("500");
  const [isDirty, setIsDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["autoBlock"],
    queryFn: getAutoBlockSettings,
  });

  // טעינת ערכים מהשרת — רק אם המשתמש לא ערך את הטופס
  useEffect(() => {
    if (data && !isDirty) {
      setEnabled(data.auto_block_enabled);
      setGraceMonths(String(data.auto_block_grace_months));
      setMinDebt(String(data.auto_block_min_debt));
    }
  }, [data, isDirty]);

  const markDirty = () => setIsDirty(true);

  const mutation = useMutation({
    mutationFn: () =>
      updateAutoBlockSettings({
        auto_block_enabled: enabled,
        auto_block_grace_months: parseInt(graceMonths, 10),
        auto_block_min_debt: parseFloat(minDebt),
      }),
    onSuccess: (result) => {
      toast({
        title: result.success ? "ההגדרות עודכנו" : "שגיאה",
        description: result.message,
        variant: result.success ? "default" : "destructive",
      });
      if (result.success) {
        setIsDirty(false);
        queryClient.invalidateQueries({ queryKey: ["autoBlock"] });
      }
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה, נסה שוב", variant: "destructive" });
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">חסימה אוטומטית</h2>
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">חסימה אוטומטית</h2>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <ShieldBan className="h-5 w-5" />
            הגדרות חסימה אוטומטית
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              mutation.mutate();
            }}
            className="space-y-6"
          >
            {/* הפעלה/כיבוי */}
            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <p className="font-medium">חסימה אוטומטית</p>
                <p className="text-sm text-muted-foreground">
                  חסימת שליחים אוטומטית בעת חוב מצטבר
                </p>
              </div>
              <Switch
                checked={enabled}
                onCheckedChange={(checked) => {
                  setEnabled(checked);
                  markDirty();
                }}
              />
            </div>

            {/* הגדרות — מוצגות רק כשהחסימה מופעלת */}
            {enabled && (
              <div className="space-y-4 p-4 bg-gray-50 rounded-lg">
                <div className="space-y-2">
                  <Label htmlFor="grace-months">תקופת חסד (חודשים)</Label>
                  <Input
                    id="grace-months"
                    type="number"
                    min={1}
                    max={12}
                    value={graceMonths}
                    onChange={(e) => { setGraceMonths(e.target.value); markDirty(); }}
                    dir="ltr"
                    className="w-32"
                  />
                  <p className="text-xs text-muted-foreground">
                    מספר חודשים לפני הפעלת חסימה (1-12)
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="min-debt">סף חוב מינימלי (₪)</Label>
                  <Input
                    id="min-debt"
                    type="number"
                    min={0}
                    step={0.01}
                    value={minDebt}
                    onChange={(e) => { setMinDebt(e.target.value); markDirty(); }}
                    dir="ltr"
                    className="w-40"
                  />
                  <p className="text-xs text-muted-foreground">
                    סכום חוב מינימלי להפעלת חסימה
                  </p>
                </div>
              </div>
            )}

            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "שומר..." : "שמור שינויים"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
