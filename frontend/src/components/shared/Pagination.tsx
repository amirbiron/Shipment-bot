import { Button } from "@/components/ui/button";
import { ChevronRight, ChevronLeft } from "lucide-react";

interface PaginationProps {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}

export default function Pagination({
  page,
  totalPages,
  onPageChange,
}: PaginationProps) {
  if (totalPages <= 1) return null;

  // ב-RTL: כיוון "הבא" הוא שמאלה (ChevronLeft), "הקודם" הוא ימינה (ChevronRight)
  return (
    <div className="flex items-center justify-center gap-3 mt-4">
      <Button
        variant="outline"
        size="sm"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        aria-label="עמוד קודם"
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
      <span className="text-sm text-muted-foreground">
        עמוד {page} מתוך {totalPages}
      </span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        aria-label="עמוד הבא"
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
    </div>
  );
}
