import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getBlacklist,
  addToBlacklist,
  addToBlacklistBulk,
  removeFromBlacklist,
  type BlacklistItem,
  type BulkResultItem,
} from "@/api/blacklist";
import DataTable from "@/components/shared/DataTable";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { formatDate } from "@/lib/format";
import { Trash2, Ban, Users } from "lucide-react";

export default function BlacklistPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("");
  const [bulkText, setBulkText] = useState("");
  const [bulkReason, setBulkReason] = useState("");
  const [bulkResults, setBulkResults] = useState<BulkResultItem[] | null>(null);
  const [removeTarget, setRemoveTarget] = useState<BlacklistItem | null>(null);

  const { data: blacklist, isLoading } = useQuery({
    queryKey: ["blacklist"],
    queryFn: getBlacklist,
  });

  const addMutation = useMutation({
    mutationFn: ({ phoneNumber, rsn }: { phoneNumber: string; rsn: string }) =>
      addToBlacklist(phoneNumber, rsn),
    onSuccess: (result) => {
      toast({
        title: result.success ? "נוסף לרשימה השחורה" : "שגיאה",
        description: result.message,
        variant: result.success ? "default" : "destructive",
      });
      if (result.success) {
        queryClient.invalidateQueries({ queryKey: ["blacklist"] });
        setPhone("");
        setReason("");
      }
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה, נסה שוב", variant: "destructive" });
    },
  });

  const bulkMutation = useMutation({
    mutationFn: (entries: Array<{ phone_number: string; reason: string }>) =>
      addToBlacklistBulk(entries),
    onSuccess: (result) => {
      setBulkResults(result.results);
      toast({ title: `${result.success_count} מתוך ${result.total} נוספו בהצלחה` });
      queryClient.invalidateQueries({ queryKey: ["blacklist"] });
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה, נסה שוב", variant: "destructive" });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (courierId: number) => removeFromBlacklist(courierId),
    onSuccess: (result) => {
      toast({ title: result.success ? "הוסר מהרשימה השחורה" : "שגיאה", description: result.message });
      queryClient.invalidateQueries({ queryKey: ["blacklist"] });
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה בהסרה מהרשימה השחורה", variant: "destructive" });
    },
  });

  const handleBulkAdd = () => {
    const phones = bulkText
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    if (phones.length === 0) return;
    if (phones.length > 50) {
      toast({ title: "מקסימום 50 מספרים", variant: "destructive" });
      return;
    }
    const entries = phones.map((p) => ({ phone_number: p, reason: bulkReason }));
    bulkMutation.mutate(entries);
  };

  const columns = useMemo<ColumnDef<BlacklistItem, unknown>[]>(() => [
    { accessorKey: "name", header: "שם" },
    {
      accessorKey: "phone_masked",
      header: "טלפון",
      cell: ({ row }) => <span dir="ltr">{row.original.phone_masked}</span>,
    },
    { accessorKey: "reason", header: "סיבה", cell: ({ row }) => row.original.reason || "-" },
    {
      accessorKey: "blocked_at",
      header: "תאריך חסימה",
      cell: ({ row }) => formatDate(row.original.blocked_at),
    },
    {
      id: "actions",
      header: "פעולות",
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            setRemoveTarget(row.original);
          }}
          className="text-destructive hover:text-destructive"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      ),
    },
  ], []);

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">רשימה שחורה</h2>

      <DataTable
        columns={columns}
        data={blacklist ?? []}
        isLoading={isLoading}
        emptyMessage="הרשימה השחורה ריקה"
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* הוספה בודדת */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Ban className="h-5 w-5" />
              הוספה לרשימה שחורה
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (phone.trim()) addMutation.mutate({ phoneNumber: phone, rsn: reason });
              }}
              className="space-y-3"
            >
              <div>
                <Label htmlFor="bl-phone">מספר טלפון</Label>
                <Input
                  id="bl-phone"
                  placeholder="050-1234567"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  dir="ltr"
                  disabled={addMutation.isPending}
                />
              </div>
              <div>
                <Label htmlFor="bl-reason">סיבה (אופציונלי)</Label>
                <Input
                  id="bl-reason"
                  placeholder="סיבת החסימה"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  disabled={addMutation.isPending}
                />
              </div>
              <Button type="submit" disabled={addMutation.isPending || !phone.trim()}>
                הוסף
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* הוספה מרובה */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Users className="h-5 w-5" />
              הוספה מרובה
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <Label>מספרי טלפון (אחד בכל שורה, מקסימום 50)</Label>
              <Textarea
                value={bulkText}
                onChange={(e) => setBulkText(e.target.value)}
                dir="ltr"
                rows={4}
                className="resize-none"
              />
            </div>
            <div>
              <Label>סיבה משותפת (אופציונלי)</Label>
              <Input
                placeholder="סיבת חסימה"
                value={bulkReason}
                onChange={(e) => setBulkReason(e.target.value)}
              />
            </div>
            <Button
              onClick={handleBulkAdd}
              disabled={bulkMutation.isPending || !bulkText.trim()}
              className="w-full"
            >
              {bulkMutation.isPending ? "מוסיף..." : "הוסף הכל"}
            </Button>
            {bulkResults && (
              <div className="space-y-1 mt-2">
                {bulkResults.map((r, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <Badge variant={r.success ? "success" : "destructive"}>
                      {r.success ? "הצלחה" : "נכשל"}
                    </Badge>
                    <span dir="ltr">{r.phone_masked}</span>
                    <span className="text-muted-foreground">{r.message}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog
        open={!!removeTarget}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
        title="הסרה מהרשימה השחורה"
        description={`האם להסיר את ${removeTarget?.name || "השליח"} מהרשימה השחורה?`}
        confirmLabel="הסר"
        destructive
        onConfirm={() => {
          if (removeTarget) removeMutation.mutate(removeTarget.courier_id);
        }}
      />
    </div>
  );
}
