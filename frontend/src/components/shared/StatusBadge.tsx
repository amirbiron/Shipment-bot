import { Badge } from "@/components/ui/badge";

const STATUS_MAP: Record<
  string,
  { label: string; variant: "default" | "secondary" | "destructive" | "success" | "warning" | "info" }
> = {
  open: { label: "פתוח", variant: "default" },
  pending_approval: { label: "ממתין לאישור", variant: "warning" },
  captured: { label: "נתפס", variant: "info" },
  in_progress: { label: "בדרך", variant: "info" },
  delivered: { label: "נמסר", variant: "success" },
  cancelled: { label: "בוטל", variant: "destructive" },
};

interface StatusBadgeProps {
  status: string;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const config = STATUS_MAP[status] ?? { label: status, variant: "secondary" as const };
  return <Badge variant={config.variant}>{config.label}</Badge>;
}
