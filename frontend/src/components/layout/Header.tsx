import { Menu } from "lucide-react";
import { useAuthStore } from "@/store/auth";

interface HeaderProps {
  onMenuClick: () => void;
}

export default function Header({ onMenuClick }: HeaderProps) {
  const { stationName } = useAuthStore();

  return (
    <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-border bg-white px-4 py-3 lg:px-6">
      <button
        onClick={onMenuClick}
        className="lg:hidden p-2 rounded-md hover:bg-accent"
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex-1">
        <h1 className="text-lg font-semibold text-foreground lg:hidden">
          {stationName || "פאנל תחנה"}
        </h1>
      </div>
    </header>
  );
}
