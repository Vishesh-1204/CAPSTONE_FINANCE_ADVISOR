// frontend/components/summary-charts.tsx
//
// Purpose: the two charts of /summary (GET /api/transactions/summary?month=):
//   ExpenseByCategoryChart  horizontal bars, largest category first. Only
//                           txn_type='expense' rows count; "Uncategorized" is
//                           amber, a nudge to run the categorizer or review.
//   MoneyFlowChart          income vs expenses vs investments for the month.
//                           Investments are SAVED money, shown next to spending
//                           but never added to it.

"use client";

import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { formatMoney } from "@/lib/format";
import type { CategoryTotal, MonthlySummary } from "@/lib/types";

const compactInr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  notation: "compact",
  maximumFractionDigits: 1,
});

const UNCATEGORIZED = "Uncategorized";

const categoryConfig = { total: { label: "Spent", color: "var(--chart-2)" } } satisfies ChartConfig;

/** A small tooltip box: title plus label/value lines. */
function TooltipBox({ title, lines }: { title: string; lines: [string, string][] }) {
  return (
    <div className="rounded-lg border bg-background px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{title}</div>
      {lines.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4">
          <span className="text-muted-foreground">{label}</span>
          <span className="font-mono tabular-nums">{value}</span>
        </div>
      ))}
    </div>
  );
}

interface CategoryRow extends CategoryTotal {
  share: number; // of the month's expense total
}

function CategoryTooltip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: CategoryRow }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <TooltipBox
      title={row.category}
      lines={[
        ["Spent", formatMoney(row.total)],
        ["Share", `${(row.share * 100).toFixed(1)}%`],
        ["Transactions", String(row.count)],
      ]}
    />
  );
}

export function ExpenseByCategoryChart({ summary }: { summary: MonthlySummary }) {
  const data: CategoryRow[] = [...summary.by_category]
    .sort((a, b) => b.total - a.total)
    .map((row) => ({ ...row, share: summary.expense_total ? row.total / summary.expense_total : 0 }));

  return (
    <ChartContainer config={categoryConfig} className="aspect-auto w-full" style={{ height: 36 * data.length + 50 }}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" tickFormatter={(value: number) => compactInr.format(value)} />
        <YAxis type="category" dataKey="category" width={130} tickLine={false} axisLine={false} />
        <ChartTooltip content={<CategoryTooltip />} />
        <Bar dataKey="total" fill="var(--color-total)" radius={3}>
          {data.map((row) => (
            <Cell key={row.category} fill={row.category === UNCATEGORIZED ? "var(--chart-4)" : "var(--color-total)"} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}

const flowConfig = {
  income: { label: "Income", color: "var(--chart-1)" },
  expense: { label: "Expenses", color: "var(--chart-2)" },
  investment: { label: "Investments", color: "var(--chart-3)" },
} satisfies ChartConfig;

interface FlowRow {
  key: keyof typeof flowConfig;
  label: string;
  amount: number;
}

function FlowTooltip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: FlowRow }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return <TooltipBox title={row.label} lines={[["Amount", formatMoney(row.amount)]]} />;
}

export function MoneyFlowChart({ summary }: { summary: MonthlySummary }) {
  const data: FlowRow[] = [
    { key: "income", label: "Income", amount: summary.income_total },
    { key: "expense", label: "Expenses", amount: summary.expense_total },
    { key: "investment", label: "Investments", amount: summary.investment_out_total },
  ];

  return (
    <ChartContainer config={flowConfig} className="aspect-auto h-64 w-full">
      <BarChart data={data} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} />
        <YAxis width={70} tickFormatter={(value: number) => compactInr.format(value)} tickLine={false} axisLine={false} />
        <ChartTooltip content={<FlowTooltip />} />
        <Bar dataKey="amount" radius={4}>
          {data.map((row) => (
            <Cell key={row.key} fill={`var(--color-${row.key})`} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
