import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getGroupSettings, updateGroupSettings } from "@/api/groups";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { Settings } from "lucide-react";

const PLATFORM_OPTIONS = [
  { value: "telegram", label: "Telegram" },
  { value: "whatsapp", label: "WhatsApp" },
];

export default function GroupSettingsPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [publicChatId, setPublicChatId] = useState("");
  const [publicPlatform, setPublicPlatform] = useState("");
  const [privateChatId, setPrivateChatId] = useState("");
  const [privatePlatform, setPrivatePlatform] = useState("");
  const [isDirty, setIsDirty] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["groups"],
    queryFn: getGroupSettings,
  });

  // טעינת ערכים מהשרת — רק אם המשתמש לא ערך את הטופס
  useEffect(() => {
    if (data && !isDirty) {
      setPublicChatId(data.public_group_chat_id || "");
      setPublicPlatform(data.public_group_platform || "");
      setPrivateChatId(data.private_group_chat_id || "");
      setPrivatePlatform(data.private_group_platform || "");
    }
  }, [data, isDirty]);

  const markDirty = () => setIsDirty(true);

  const mutation = useMutation({
    mutationFn: () =>
      updateGroupSettings({
        public_group_chat_id: publicChatId || null,
        public_group_platform: publicPlatform || null,
        private_group_chat_id: privateChatId || null,
        private_group_platform: privatePlatform || null,
      }),
    onSuccess: (result) => {
      toast({
        title: result.success ? "ההגדרות עודכנו" : "שגיאה",
        description: result.message,
        variant: result.success ? "default" : "destructive",
      });
      setIsDirty(false);
      queryClient.invalidateQueries({ queryKey: ["groups"] });
    },
    onError: () => {
      toast({ title: "שגיאה", description: "אירעה שגיאה, נסה שוב", variant: "destructive" });
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">הגדרות קבוצות</h2>
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">הגדרות קבוצות</h2>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Settings className="h-5 w-5" />
            קבוצות תחנה
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
            {/* קבוצה ציבורית */}
            <div className="space-y-4 p-4 bg-gray-50 rounded-lg">
              <h3 className="font-medium">קבוצה ציבורית (שידור)</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="public-chat-id">Chat ID</Label>
                  <Input
                    id="public-chat-id"
                    placeholder="מזהה קבוצה"
                    value={publicChatId}
                    onChange={(e) => { setPublicChatId(e.target.value); markDirty(); }}
                    dir="ltr"
                  />
                </div>
                <div className="space-y-2">
                  <Label>פלטפורמה</Label>
                  <Select
                    value={publicPlatform || undefined}
                    onValueChange={(v) => { setPublicPlatform(v === "__none__" ? "" : v); markDirty(); }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="לא הוגדר" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">לא הוגדר</SelectItem>
                      {PLATFORM_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>

            {/* קבוצה פרטית */}
            <div className="space-y-4 p-4 bg-gray-50 rounded-lg">
              <h3 className="font-medium">קבוצה פרטית (כרטיסים סגורים)</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="private-chat-id">Chat ID</Label>
                  <Input
                    id="private-chat-id"
                    placeholder="מזהה קבוצה"
                    value={privateChatId}
                    onChange={(e) => { setPrivateChatId(e.target.value); markDirty(); }}
                    dir="ltr"
                  />
                </div>
                <div className="space-y-2">
                  <Label>פלטפורמה</Label>
                  <Select
                    value={privatePlatform || undefined}
                    onValueChange={(v) => { setPrivatePlatform(v === "__none__" ? "" : v); markDirty(); }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="לא הוגדר" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">לא הוגדר</SelectItem>
                      {PLATFORM_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>

            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "שומר..." : "שמור שינויים"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
