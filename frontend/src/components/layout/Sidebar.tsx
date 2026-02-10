import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Truck,
  History,
  Users,
  Wallet,
  Ban,
  BarChart3,
  Settings,
  LogOut,
  X,
} from "lucide-react";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/", icon: LayoutDashboard, label: "דשבורד", end: true },
  { to: "/deliveries/active", icon: Truck, label: "משלוחים פעילים" },
  { to: "/deliveries/history", icon: History, label: "היסטוריית משלוחים" },
  { to: "/dispatchers", icon: Users, label: "סדרנים" },
  { to: "/wallet", icon: Wallet, label: "ארנק" },
  { to: "/blacklist", icon: Ban, label: "רשימה שחורה" },
  { to: "/reports", icon: BarChart3, label: "דוחות" },
  { to: "/groups", icon: Settings, label: "הגדרות קבוצות" },
];

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export default function Sidebar({ open, onClose }: SidebarProps) {
  const { stationName, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <>
      {/* רקע כהה למובייל */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={cn(
          "fixed top-0 right-0 z-50 h-full w-64 bg-white border-e border-border flex flex-col transition-transform duration-200 lg:translate-x-0 lg:static lg:z-auto",
          open ? "translate-x-0" : "translate-x-full lg:translate-x-0"
        )}
      >
        {/* כותרת */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div>
            <h2 className="font-bold text-lg text-foreground">פאנל תחנה</h2>
            {stationName && (
              <p className="text-sm text-muted-foreground">{stationName}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="lg:hidden p-1 rounded-md hover:bg-accent"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* ניווט */}
        <nav className="flex-1 overflow-y-auto p-3 space-y-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onClose}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-foreground hover:bg-accent"
                )
              }
            >
              <item.icon className="h-5 w-5 shrink-0" />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        {/* התנתקות */}
        <div className="p-3 border-t border-border">
          <button
            onClick={handleLogout}
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-destructive hover:bg-red-50 w-full transition-colors"
          >
            <LogOut className="h-5 w-5 shrink-0" />
            <span>התנתקות</span>
          </button>
        </div>
      </aside>
    </>
  );
}
