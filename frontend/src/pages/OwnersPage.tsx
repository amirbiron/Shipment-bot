import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import {
  getOwners,
  addOwner,
  removeOwner,
  type Owner,
} from "@/api/owners";
import DataTable from "@/components/shared/DataTable";
import ConfirmDialog from "@/components/shared/ConfirmDialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { formatDate } from "@/lib/format";
import { Trash2, UserPlus } from "lucide-react";

export default function OwnersPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [phone, setPhone] = useState("");
  const [removeTarget, setRemoveTarget] = useState<Owner | null>(null);

  const { data: owners, isLoading } = useQuery({
    queryKey: ["owners"],
    queryFn: getOwners,
  });

  const addMutation = useMutation({
    mutationFn: (phoneNumber: string) => addOwner(phoneNumber),
    onSuccess: (result) => {
      toast({
        title: result.success ? "הבעלים נוסף בהצלחה" : "שגיאה",
        description: result.message,
        variant: result.success ? "default" : "destructive",
      });
      if (result.success) {
        queryClient.invalidateQueries({ queryKey: ["owners"] });
        setPhone("");
      }
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה, נסה שוב", variant: "destructive" });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (userId: number) => removeOwner(userId),
    onSuccess: (result) => {
      toast({
        title: result.success ? "הבעלים הוסר" : "שגיאה",
        description: result.message,
        variant: result.success ? "default" : "destructive",
      });
      if (result.success) {
        queryClient.invalidateQueries({ queryKey: ["owners"] });
      }
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה בהסרת הבעלים", variant: "destructive" });
    },
  });

  const columns = useMemo<ColumnDef<Owner, unknown>[]>(() => [
    { accessorKey: "name", header: "שם" },
    {
      accessorKey: "phone_masked",
      header: "טלפון",
      cell: ({ row }) => <span dir="ltr">{row.original.phone_masked}</span>,
    },
    {
      accessorKey: "created_at",
      header: "מאז",
      cell: ({ row }) => formatDate(row.original.created_at),
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
      <h2 className="text-2xl font-bold">ניהול בעלים</h2>

      {/* רשימת בעלים */}
      <DataTable
        columns={columns}
        data={owners ?? []}
        isLoading={isLoading}
        emptyMessage="אין בעלים רשומים"
      />

      {/* הוספת בעלים */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <UserPlus className="h-5 w-5" />
            הוספת בעלים
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (phone.trim()) addMutation.mutate(phone);
            }}
            className="flex gap-2"
          >
            <div className="flex-1">
              <Label htmlFor="add-owner-phone" className="sr-only">מספר טלפון</Label>
              <Input
                id="add-owner-phone"
                placeholder="050-1234567"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                dir="ltr"
                disabled={addMutation.isPending}
              />
            </div>
            <Button type="submit" disabled={addMutation.isPending || !phone.trim()}>
              הוסף
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* דיאלוג אישור הסרה */}
      <ConfirmDialog
        open={!!removeTarget}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
        title="הסרת בעלים"
        description={`האם להסיר את ${removeTarget?.name || "הבעלים"}?`}
        confirmLabel="הסר"
        destructive
        onConfirm={() => {
          if (removeTarget) removeMutation.mutate(removeTarget.user_id);
        }}
      />
    </div>
  );
}
