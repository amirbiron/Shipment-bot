import { useEffect, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/auth";
import { requestOtp, verifyOtp } from "@/api/auth";
import { Truck } from "lucide-react";
import axios from "axios";

export default function LoginPage() {
  const { isAuthenticated, login } = useAuthStore();
  const navigate = useNavigate();
  const { toast } = useToast();

  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);

  // הצגת הודעה כשפג תוקף הטוקן (הדגל נשמר ב-sessionStorage לפני redirect)
  useEffect(() => {
    if (sessionStorage.getItem("session-expired")) {
      sessionStorage.removeItem("session-expired");
      toast({ title: "פג תוקף הכניסה", description: "יש להתחבר מחדש", variant: "destructive" });
    }
  }, []);

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim()) return;

    setLoading(true);
    try {
      await requestOtp(phone);
      toast({ title: "קוד נשלח", description: "בדוק את הבוט שלך (Telegram/WhatsApp)" });
      setStep("otp");
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        const detail = err.response?.data?.detail;
        if (status === 429) {
          toast({ title: "נא להמתין", description: "יש להמתין דקה בין בקשות קוד", variant: "destructive" });
        } else {
          toast({ title: "שגיאה", description: detail || "אירעה שגיאה, נסה שוב", variant: "destructive" });
        }
      }
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!otp.trim()) return;

    setLoading(true);
    try {
      const result = await verifyOtp(phone, otp);
      login(result.access_token, result.station_id, result.station_name);
      navigate("/", { replace: true });
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        const detail = err.response?.data?.detail;
        if (status === 401) {
          toast({ title: "קוד שגוי", description: "הקוד שהוזן אינו נכון או שפג תוקפו", variant: "destructive" });
        } else {
          toast({ title: "שגיאה", description: detail || "אירעה שגיאה, נסה שוב", variant: "destructive" });
        }
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
            <Truck className="h-7 w-7 text-primary" />
          </div>
          <CardTitle className="text-2xl">כניסה לפאנל תחנה</CardTitle>
        </CardHeader>
        <CardContent>
          {step === "phone" ? (
            <form onSubmit={handleRequestOtp} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="phone">מספר טלפון</Label>
                <Input
                  id="phone"
                  type="tel"
                  placeholder="050-1234567"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  dir="ltr"
                  className="text-start"
                  disabled={loading}
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading || !phone.trim()}>
                {loading ? "שולח..." : "שלח קוד כניסה"}
              </Button>
            </form>
          ) : (
            <form onSubmit={handleVerifyOtp} className="space-y-4">
              <p className="text-sm text-muted-foreground text-center mb-4">
                קוד אימות נשלח אליך דרך הבוט
              </p>
              <div className="space-y-2">
                <Label htmlFor="otp">קוד אימות</Label>
                <Input
                  id="otp"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="000000"
                  value={otp}
                  onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                  dir="ltr"
                  className="text-center text-2xl tracking-widest"
                  disabled={loading}
                  autoFocus
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading || otp.length !== 6}>
                {loading ? "מאמת..." : "כניסה"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                onClick={() => {
                  setStep("phone");
                  setOtp("");
                }}
              >
                חזרה
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
