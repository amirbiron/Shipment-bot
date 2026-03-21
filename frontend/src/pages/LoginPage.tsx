import { useEffect, useRef, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/auth";
import {
  requestOtp,
  verifyOtp,
  getTelegramBotInfo,
  telegramLogin,
  telegramLoginSelectStation,
} from "@/api/auth";
import type { TelegramAuthData } from "@/api/auth";
import { Truck } from "lucide-react";
import axios from "axios";

declare global {
  interface Window {
    Telegram?: {
      Login: {
        auth: (
          options: { bot_id: string; request_access?: boolean },
          callback: (data: TelegramAuthData | false) => void
        ) => void;
      };
    };
  }
}

export default function LoginPage() {
  const { isAuthenticated, login } = useAuthStore();
  const navigate = useNavigate();
  const { toast } = useToast();

  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [telegramBotId, setTelegramBotId] = useState("");
  const [telegramEnabled, setTelegramEnabled] = useState(false);
  const [telegramLoading, setTelegramLoading] = useState(false);
  const telegramScriptLoaded = useRef(false);

  // הצגת הודעה כשפג תוקף הטוקן (הדגל נשמר ב-sessionStorage לפני redirect)
  useEffect(() => {
    if (sessionStorage.getItem("session-expired")) {
      sessionStorage.removeItem("session-expired");
      toast({ title: "פג תוקף הכניסה", description: "יש להתחבר מחדש", variant: "destructive" });
    }
  }, []);

  // טעינת מידע על הבוט + סקריפט Telegram Widget
  useEffect(() => {
    getTelegramBotInfo()
      .then((info) => {
        if (info.enabled && info.bot_id) {
          setTelegramBotId(info.bot_id);
          setTelegramEnabled(true);
          // טעינת סקריפט Telegram Login Widget
          if (!telegramScriptLoaded.current) {
            telegramScriptLoaded.current = true;
            const script = document.createElement("script");
            script.src = "https://telegram.org/js/telegram-widget.js?22";
            script.async = true;
            document.head.appendChild(script);
          }
        }
      })
      .catch(() => {
        // אם לא ניתן לקבל מידע על הבוט — פשוט לא מציגים את כפתור טלגרם
      });
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
      login(result.access_token, result.refresh_token, result.station_id, result.station_name);
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

  const handleTelegramLogin = () => {
    if (!window.Telegram?.Login) {
      toast({ title: "שגיאה", description: "סקריפט טלגרם לא נטען, נסה לרענן את הדף", variant: "destructive" });
      return;
    }

    setTelegramLoading(true);
    window.Telegram.Login.auth(
      { bot_id: telegramBotId, request_access: true },
      async (data: TelegramAuthData | false) => {
        if (!data) {
          setTelegramLoading(false);
          return;
        }

        try {
          const result = await telegramLogin(data);

          // בחירת תחנה אם יש כמה
          if ("choose_station" in result && result.choose_station) {
            const stations = (result as any).stations as Array<{
              station_id: number;
              station_name: string;
            }>;
            if (stations.length === 1) {
              const finalResult = await telegramLoginSelectStation(data, stations[0].station_id);
              login(finalResult.access_token, finalResult.refresh_token, finalResult.station_id, finalResult.station_name);
              navigate("/", { replace: true });
            } else {
              // TODO: הצגת בורר תחנות — כרגע בוחר את הראשונה
              const finalResult = await telegramLoginSelectStation(data, stations[0].station_id);
              login(finalResult.access_token, finalResult.refresh_token, finalResult.station_id, finalResult.station_name);
              navigate("/", { replace: true });
            }
          } else {
            login(result.access_token, result.refresh_token, result.station_id, result.station_name);
            navigate("/", { replace: true });
          }
        } catch (err) {
          if (axios.isAxiosError(err)) {
            const detail = err.response?.data?.detail;
            toast({
              title: "כניסה דרך טלגרם נכשלה",
              description: detail || "אירעה שגיאה, נסה שוב",
              variant: "destructive",
            });
          }
        } finally {
          setTelegramLoading(false);
        }
      }
    );
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

              {telegramEnabled && (
                <>
                  <div className="relative my-4">
                    <div className="absolute inset-0 flex items-center">
                      <span className="w-full border-t" />
                    </div>
                    <div className="relative flex justify-center text-xs uppercase">
                      <span className="bg-white px-2 text-muted-foreground">או</span>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full flex items-center justify-center gap-2"
                    onClick={handleTelegramLogin}
                    disabled={telegramLoading}
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="#229ED9"
                      className="h-5 w-5"
                    >
                      <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" />
                    </svg>
                    {telegramLoading ? "מתחבר..." : "כניסה דרך Telegram"}
                  </Button>
                </>
              )}
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
