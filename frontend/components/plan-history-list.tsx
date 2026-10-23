// frontend/components/plan-history-list.tsx
//
// Purpose: the user's saved investment plans (GET /api/investing/plans), newest
// first. Clicking a row asks the page to open that plan in full
// (GET /api/investing/plans/{id}); the plan being shown is highlighted.
// Display only: the page owns the data and the API calls.

import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatMoney, humanize } from "@/lib/format";
import type { PlanHistoryItem } from "@/lib/types";

export function PlanHistoryList({
  plans,
  loading,
  shownId,
  openingId,
  onOpen,
}: {
  plans: PlanHistoryItem[] | null;
  loading: boolean;
  shownId: number | null; // the plan on screen
  openingId: number | null; // the plan being fetched
  onOpen: (id: number) => void;
}) {
  if (!plans || plans.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {loading ? "Loading…" : "No saved plans yet. Generate one in section 3."}
      </p>
    );
  }
  return (
    <ul className="divide-y rounded-lg border">
      {plans.map((plan) => {
        const shown = plan.id === shownId;
        return (
          <li key={plan.id}>
            <button
              type="button"
              onClick={() => onOpen(plan.id)}
              aria-current={shown ? "true" : undefined}
              className={`flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/60 ${
                shown ? "bg-muted font-medium" : ""
              }`}
            >
              <span className="tabular-nums">#{plan.id}</span>
              <span className="text-muted-foreground">
                {plan.created_at ? formatDateTime(plan.created_at) : "—"}
              </span>
              <Badge variant="outline">{humanize(plan.category)}</Badge>
              <span className="tabular-nums">SIP {formatMoney(plan.recommended_sip)}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                {openingId === plan.id
                  ? "opening…"
                  : shown
                    ? "shown above"
                    : plan.explanation_source === "llm"
                      ? "AI-written"
                      : "Template"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
