// frontend/components/budget-status-card.tsx
//
// Purpose: "how am I doing this month?" (GET /api/budgets/status?month=).
// Totals, one progress bar per budgeted category coloured by its status
// (ok / warning at >= 80 % / exceeded), the categories you spent in without a
// limit, and a Recharts bar chart of spent vs limit.
// Only EXPENSES count as spending: investments appear separately as
// "Invested this month" because money moved into an investment is saved, not spent.

"use client";

import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";

import { StatGrid, StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Progress } from "@/components/ui/progress";
import { formatMoney, formatPercent } from "@/lib/format";
import type { BudgetStatus, BudgetStatusItem, BudgetStatusValue } from "@/lib/types";

// Colour of the progress bar's inner indicator per status. The Progress
// component paints its indicator bg-primary; these classes override it.
const BAR_COLOURS: Record<BudgetStatusValue, string> = {
  ok: "[&_[data-slot=progress-indicator]]:bg-emerald-600",
  warning: "[&_[data-slot=progress-indicator]]:bg-amber-500",
  exceeded: "[&_[data-slot=progress-indicator]]:bg-destructive",
  unbudgeted: "",
};

const STATUS_BADGES: Record<BudgetStatusValue, { label: string; className: string }> = {
  ok: { label: "ok", className: "bg-emerald-600/10 text-emerald-700 dark:text-emerald-400" },
  warning: { label: "warning", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  exceeded: { label: "exceeded", className: "bg-destructive/10 text-destructive" },
  unbudgeted: { label: "no limit", className: "" },
};

const chartConfig = {
  limit: { label: "Limit", color: "var(--chart-2)" },
  spent: { label: "Spent", color: "var(--chart-4)" },
} satisfies ChartConfig;

/** Bar width in %. The backend's percent_used is 0-100 and null when the limit is 0. */
function barValue(item: BudgetStatusItem): number {
  if (item.percent_used == null) return item.spent > 0 ? 100 : 0;
  return Math.min(100, item.percent_used);
}

export function BudgetStatusCard({ status }: { status: BudgetStatus }) {
  const budgeted = status.items.filter((item) => item.limit != null);
  const unbudgeted = status.items.filter((item) => item.limit == null);

  return (
    <div className="space-y-6">
      <StatGrid>
        <StatTile label="Total limit" value={formatMoney(status.total_limit)} />
        <StatTile
          label="Spent"
          value={formatMoney(status.total_spent)}
          hint="expenses only"
          tone={status.total_spent > status.total_limit && budgeted.length > 0 ? "bad" : "default"}
        />
        <StatTile
          label="Remaining"
          value={formatMoney(status.total_remaining)}
          tone={status.total_remaining < 0 ? "bad" : "good"}
        />
        <StatTile
          label="Invested this month"
          value={formatMoney(status.invested_this_month)}
          hint={
            status.savings_target_amount != null
              ? `savings target ${formatMoney(status.savings_target_amount)}`
              : "no profile, no savings target"
          }
          tone="good"
        />
      </StatGrid>

      {budgeted.length === 0 ? (
        <p className="text-sm text-muted-foreground">No limits for this month yet, so there is nothing to compare.</p>
      ) : (
        <>
          <div className="space-y-3">
            {budgeted.map((item) => (
              <StatusRow key={item.category} item={item} />
            ))}
          </div>
          <SpentVsLimitChart items={budgeted} />
        </>
      )}

      {unbudgeted.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">Spending in categories without a limit</div>
          <div className="flex flex-wrap gap-2">
            {unbudgeted.map((item) => (
              <Badge key={item.category} variant="outline">
                {item.category}: {formatMoney(item.spent)}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {status.total_spent === 0 && budgeted.length > 0 && (
        <p className="text-sm text-muted-foreground">No spending recorded this month yet.</p>
      )}
    </div>
  );
}

function StatusRow({ item }: { item: BudgetStatusItem }) {
  const badge = STATUS_BADGES[item.status];
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm">
        <span className="flex items-center gap-2 font-medium">
          {item.category}
          <Badge variant="outline" className={badge.className}>
            {badge.label}
          </Badge>
        </span>
        <span className="text-muted-foreground tabular-nums">
          {formatMoney(item.spent)} of {formatMoney(item.limit)}
          {item.percent_used != null && ` · ${formatPercent(item.percent_used / 100)}`}
          {item.remaining != null && item.remaining < 0 && (
            <span className="text-destructive"> · over by {formatMoney(-item.remaining)}</span>
          )}
        </span>
      </div>
      <Progress value={barValue(item)} className={`h-2 ${BAR_COLOURS[item.status]}`} />
    </div>
  );
}

/** Horizontal grouped bars (one row per category), so long category names stay readable. */
function SpentVsLimitChart({ items }: { items: BudgetStatusItem[] }) {
  const data = items.map((item) => ({
    category: item.category,
    limit: item.limit ?? 0,
    spent: item.spent,
    exceeded: item.status === "exceeded",
  }));

  return (
    <div className="space-y-2">
      <div className="text-xs text-muted-foreground">Spent vs limit (red = over the limit)</div>
      <ChartContainer config={chartConfig} className="aspect-auto w-full" style={{ height: 48 * data.length + 60 }}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid horizontal={false} />
          <XAxis type="number" tickFormatter={(value: number) => formatMoney(value).replace(/\.00$/, "")} />
          <YAxis type="category" dataKey="category" width={130} tickLine={false} axisLine={false} />
          <ChartTooltip
            content={
              <ChartTooltipContent
                formatter={(value, name) => (
                  <div className="flex w-full justify-between gap-4">
                    <span className="text-muted-foreground">
                      {chartConfig[name as keyof typeof chartConfig]?.label ?? name}
                    </span>
                    <span className="font-mono tabular-nums">{formatMoney(Number(value))}</span>
                  </div>
                )}
              />
            }
          />
          <ChartLegend content={<ChartLegendContent />} />
          <Bar dataKey="limit" fill="var(--color-limit)" radius={3} />
          <Bar dataKey="spent" fill="var(--color-spent)" radius={3}>
            {data.map((entry) => (
              <Cell key={entry.category} fill={entry.exceeded ? "var(--destructive)" : "var(--color-spent)"} />
            ))}
          </Bar>
        </BarChart>
      </ChartContainer>
    </div>
  );
}
