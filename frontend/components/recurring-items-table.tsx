// frontend/components/recurring-items-table.tsx
//
// Purpose: the payments the forecast detected as repeating about monthly
// (GET /api/forecast/recurring): salary, rent/EMI/subscriptions, SIPs. These are
// NOT predicted statistically: each active item is placed on its usual day of the
// month, which is what makes the forecast explainable ("rent leaves on the 5th").
// Showing them lets the user check what the agent assumed. Read-only in Phase 5.
// "stopped" = not seen for a while, so it is listed but NOT projected.

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDay, formatMoney } from "@/lib/format";
import type { RecurringItem, RecurringKind } from "@/lib/types";

const KIND_BADGES: Record<RecurringKind, { label: string; className: string }> = {
  income: { label: "income", className: "bg-emerald-600/10 text-emerald-700 dark:text-emerald-400" },
  fixed_expense: { label: "bill", className: "" },
  investment: { label: "investment", className: "bg-sky-600/10 text-sky-700 dark:text-sky-400" },
};

export function RecurringItemsTable({ items, loading }: { items: RecurringItem[] | null; loading: boolean }) {
  if (!items) {
    return <p className="text-sm text-muted-foreground">{loading ? "Loading…" : "Could not load recurring items."}</p>;
  }
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nothing detected yet. A payment counts as recurring after at least 3 payments to the same payee, about a month
        apart, with similar amounts. Import more months of statements.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Kind</TableHead>
          <TableHead>Payee</TableHead>
          <TableHead>Category</TableHead>
          <TableHead className="text-right">Typical amount</TableHead>
          <TableHead className="text-right">Usual day</TableHead>
          <TableHead className="text-right">Times seen</TableHead>
          <TableHead>Last seen</TableHead>
          <TableHead>Next 30 days</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => {
          const kind = KIND_BADGES[item.kind];
          return (
            <TableRow key={`${item.kind}-${item.key}`} className={item.active ? "" : "text-muted-foreground"}>
              <TableCell>
                <Badge variant="outline" className={kind.className}>
                  {kind.label}
                </Badge>
              </TableCell>
              <TableCell className="max-w-56 truncate font-mono text-xs" title={item.key}>
                {item.key}
              </TableCell>
              <TableCell>{item.category ?? "—"}</TableCell>
              <TableCell className="text-right tabular-nums">{formatMoney(item.typical_amount)}</TableCell>
              <TableCell className="text-right tabular-nums">{item.typical_day}</TableCell>
              <TableCell className="text-right tabular-nums">{item.occurrences}</TableCell>
              <TableCell>{formatDay(item.last_seen, true)}</TableCell>
              <TableCell>
                {item.active ? (
                  item.projected_dates.length ? (
                    item.projected_dates.map((day) => formatDay(day)).join(", ")
                  ) : (
                    <span className="text-muted-foreground">none due</span>
                  )
                ) : (
                  <Badge variant="outline">stopped · not projected</Badge>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
