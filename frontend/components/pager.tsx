// frontend/components/pager.tsx
//
// Purpose: "Showing 51–100 of 114" with Previous / Next, for any list the backend
// pages with limit/offset (GET /api/transactions/ returns `total`, so we know
// how many pages there are without loading them all).

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";

export function Pager({
  total,
  offset,
  limit,
  onOffsetChange,
  disabled,
}: {
  total: number;
  offset: number;
  limit: number;
  onOffsetChange: (offset: number) => void;
  disabled?: boolean;
}) {
  if (total === 0) return null;
  const last = Math.min(offset + limit, total);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
      <span className="tabular-nums">
        Showing {offset + 1}–{last} of {total}
      </span>
      <div className="flex gap-1">
        <Button
          variant="outline"
          size="sm"
          className="gap-1"
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          disabled={disabled || offset === 0}
        >
          <ChevronLeft className="size-3.5" /> Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="gap-1"
          onClick={() => onOffsetChange(offset + limit)}
          disabled={disabled || last >= total}
        >
          Next <ChevronRight className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
