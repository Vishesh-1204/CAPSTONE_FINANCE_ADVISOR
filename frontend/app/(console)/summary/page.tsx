// frontend/app/(console)/summary/page.tsx
//
// Purpose: /summary — one month at a glance (GET /api/transactions/summary?month=):
// totals by transaction type, the savings + investment rate, and two charts
// (expenses by category; income vs expenses vs investments). Synthetic data only.
//
// Only txn_type='expense' counts as spending. Investments, redemptions and
// own-account transfers are shown separately so they never inflate "expenses"
// (the same rule the budget and forecast agents use).

"use client";

import { useCallback, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { MonthPicker } from "@/components/month-picker";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { ExpenseByCategoryChart, MoneyFlowChart } from "@/components/summary-charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { currentMonth, formatMoney, formatPercent } from "@/lib/format";
import type { MonthlySummary } from "@/lib/types";

function Totals({ summary }: { summary: MonthlySummary }) {
  const uncategorized = summary.by_category.find((row) => row.category === "Uncategorized");
  return (
    <StatGrid>
      <StatTile label="Income" value={formatMoney(summary.income_total)} hint="salary, interest… (refunds excluded)" tone="good" />
      <StatTile label="Expenses" value={formatMoney(summary.expense_total)} hint="spending only" />
      <StatTile
        label="Invested"
        value={formatMoney(summary.investment_out_total)}
        hint={`net of redemptions ${formatMoney(summary.net_investment_flow)}`}
        tone="good"
      />
      <StatTile
        label="Savings + investment rate"
        value={formatPercent(summary.savings_investment_rate)}
        hint="(income − expenses) / income"
        tone={summary.savings_investment_rate != null && summary.savings_investment_rate < 0 ? "bad" : "default"}
      />
      <StatTile label="Redemptions" value={formatMoney(summary.redemption_total)} hint="money back, not income" tone="muted" />
      <StatTile label="Own-account transfers" value={formatMoney(summary.transfer_total)} hint="neither in nor out" tone="muted" />
      <StatTile label="Transactions" value={summary.transaction_count} />
      <StatTile
        label="Uncategorized spending"
        value={formatMoney(uncategorized?.total ?? 0)}
        hint={uncategorized ? `${uncategorized.count} row(s): run the categorizer` : "all categorized"}
        tone={uncategorized ? "warn" : "muted"}
      />
    </StatGrid>
  );
}

export default function SummaryPage() {
  const log = useApiLog();
  const [month, setMonth] = useState(currentMonth);

  const loadSummary = useCallback(() => api.getSummary(month), [month]);
  const summary = useApiData(loadSummary, log.track);
  // keep the previous month's data off screen while the new month loads or fails
  const shown = summary.data?.month === month ? summary.data : null;

  return (
    <>
      <PageHeader
        title="Monthly summary"
        description="Totals by transaction type: only expenses count as spending; investments are saved money, shown separately."
        actions={<MonthPicker id="summary-month" value={month} onChange={setMonth} />}
      />

      <ApiErrorAlert error={summary.error} />

      {!shown ? (
        <p className="text-sm text-muted-foreground">{summary.loading ? "Loading…" : "Could not load the summary."}</p>
      ) : shown.transaction_count === 0 ? (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No transactions in {month}. Pick another month, or upload a statement that covers it.
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>{month} totals</CardTitle>
              <CardDescription>{shown.transaction_count} transactions this month.</CardDescription>
            </CardHeader>
            <CardContent>
              <Totals summary={shown} />
            </CardContent>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Expenses by category</CardTitle>
                <CardDescription>Largest first. Amber = uncategorized (run the categorizer or review).</CardDescription>
              </CardHeader>
              <CardContent>
                {shown.by_category.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No spending this month.</p>
                ) : (
                  <ExpenseByCategoryChart summary={shown} />
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Income vs expenses vs investments</CardTitle>
                <CardDescription>Where the month’s money went. Investments are saved, not spent.</CardDescription>
              </CardHeader>
              <CardContent>
                <MoneyFlowChart summary={shown} />
              </CardContent>
            </Card>
          </div>
        </>
      )}

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
