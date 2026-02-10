import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState } from "react";

interface ExportButtonProps {
  onExport: () => Promise<void>;
  label?: string;
}

export default function ExportButton({
  onExport,
  label = "ייצוא CSV",
}: ExportButtonProps) {
  const [loading, setLoading] = useState(false);

  const handleClick = async () => {
    setLoading(true);
    try {
      await onExport();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={handleClick}
      disabled={loading}
    >
      <Download className="h-4 w-4 me-2" />
      {loading ? "מייצא..." : label}
    </Button>
  );
}
