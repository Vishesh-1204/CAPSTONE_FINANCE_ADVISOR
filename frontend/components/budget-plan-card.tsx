// frontend/components/budget-plan-card.tsx
//
// Purpose: shows what ONE "Generate budget" call returned (BudgetPlan): which
// method the planner used (history vs baseline), the explanation, whether the
// plan is feasible, and the research signals: was the LLM used, and did its text
// pass the faithfulness guard (no invented rupee amounts)? It also shows the
// data quality behind the plan, because a plan built on uncategorized or
// unreviewed transactions is a weak plan.
// The backend does not store the summary, so this card only exists right after Generate.

import { CircleAlert } from "lucide-react";

import { StatGrid, StatTile } from "@/components/stat-tile";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { formatMoney } from "@/lib/format";
import type { BudgetPlan } from "@/lib/types";

function GuardBadge({ plan }: { plan: BudgetPlan }) {
  // null = no LLM text to check (template used from the start)
  if (plan.explanation_faithful === null) return <Badge variant="outline">guard: n/a</Badge>;
  return plan.explanation_faithful ? (
    <Badge variant="secondary">guard: passed</Badge>
  ) : (
    <Badge variant="destructive">guard: LLM text replaced by template</Badge>
  );
}

export function BudgetPlanCard({ plan }: { plan: BudgetPlan }) {
  const quality = plan.data_quality;

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Plan for {plan.month}</span>
        <Badge>{plan.method === "history" ? "history (your past spending)" : "baseline (no history yet)"}</Badge>
        <Badge variant="outline">{plan.llm_used ? "explanation: LLM" : "explanation: template"}</Badge>
        <GuardBadge plan={plan} />
      </div>

      <p className="text-sm leading-relaxed">{plan.summary}</p>

      {!plan.feasible && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Not feasible: short by {formatMoney(plan.shortfall)}</AlertTitle>
          <AlertDescription>
            Your essential spending alone is more than your spendable income, so discretionary limits were set to
            zero. Lower the savings target or check the income figure.
          </AlertDescription>
        </Alert>
      )}

      <StatGrid>
        <StatTile label="Spendable income" value={formatMoney(plan.spendable_income)} />
        <StatTile label="Savings target" value={formatMoney(plan.savings_target_amount)} tone="good" />
        <StatTile
          label="Months of history"
          value={quality.months_used}
          tone={quality.months_used ? "default" : "warn"}
          hint="months before this one"
        />
        <StatTile
          label="Transfers in history"
          value={formatMoney(quality.transfers_total)}
          tone={quality.transfers_total ? "warn" : "muted"}
          hint="may hide rent etc."
        />
        <StatTile
          label="Uncategorized"
          value={quality.uncategorized_count}
          tone={quality.uncategorized_count ? "warn" : "muted"}
          hint="not counted in the plan"
        />
        <StatTile
          label="Needs review"
          value={quality.needs_review_count}
          tone={quality.needs_review_count ? "warn" : "muted"}
          hint="the categorizer was unsure"
        />
      </StatGrid>
    </div>
  );
}
