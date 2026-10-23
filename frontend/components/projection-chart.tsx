// frontend/components/projection-chart.tsx
//
// Purpose: the Monte Carlo range of a monthly SIP as bars for three scenarios:
// bad case (10th percentile), typical (50th) and good case (90th). One bar group
// per scenario and one bar per SIP, so "your existing SIP" and "the recommended
// SIP" can be compared side by side. A dashed line marks the goal, if any.
// Used by the plan result (two SIPs) and the what-if simulator (one SIP).
//
// These are scenarios under the stated return and volatility ASSUMPTIONS, not
// predictions: the page always shows the assumptions next to this chart.
// Colours: same grayscale pair as allocation-chart.tsx (validated; a legend names
// both series; a single series needs no legend).

"use client";

import { Bar, BarChart, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { formatMoney, formatMoneyCompact } from "@/lib/format";
import type { Projection } from "@/lib/types";

export interface ProjectionSeries {
  label: string; // e.g. "Existing SIP ₹2,000"
  projection: Projection;
}

const SCENARIOS = [
  { key: "p10", label: "Bad case", hint: "10th percentile" },
  { key: "p50", label: "Typical", hint: "50th percentile" },
  { key: "p90", label: "Good case", hint: "90th percentile" },
] as const;

// Up to two series: the first is the comparison (mid gray), the second the main one (high contrast)
const COLORS = [
  { color: "var(--chart-2)" },
  { theme: { light: "var(--chart-5)", dark: "var(--chart-1)" } },
] as const;

type Row = { label: string; hint: string } & Record<string, number | string>;

function ProjectionTooltip({
  active,
  payload,
  series,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: Row }>;
  series: ProjectionSeries[];
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border bg-background px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">
        {row.label} <span className="font-normal text-muted-foreground">({row.hint})</span>
      </div>
      {series.map((item, index) => (
        <div key={item.label} className="flex justify-between gap-4">
          <span className="text-muted-foreground">{item.label}</span>
          <span className="font-mono tabular-nums">{formatMoney(row[`s${index}`] as number)}</span>
        </div>
      ))}
    </div>
  );
}

export function ProjectionChart({ series, goal }: { series: ProjectionSeries[]; goal: number | null }) {
  // series[0] -> s0, series[1] -> s1. With one series it takes the high-contrast colour.
  const colors = series.length === 1 ? [COLORS[1]] : COLORS;
  const chartConfig: ChartConfig = Object.fromEntries(
    series.map((item, index) => [`s${index}`, { label: item.label, ...colors[index] }]),
  );
  const data: Row[] = SCENARIOS.map((scenario) => ({
    label: scenario.label,
    hint: scenario.hint,
    ...Object.fromEntries(series.map((item, index) => [`s${index}`, item.projection[scenario.key]])),
  }));

  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-72 w-full">
      <BarChart data={data} margin={{ left: 8, right: 8, top: 16 }} barGap={2}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} />
        <YAxis
          width={70}
          tickFormatter={(value: number) => formatMoneyCompact(value)}
          tickLine={false}
          axisLine={false}
          // keep the goal line on the chart even when every scenario is below it
          domain={[0, (dataMax: number) => Math.max(dataMax, goal ?? 0) * 1.05]}
        />
        <ChartTooltip cursor={{ fillOpacity: 0.3 }} content={<ProjectionTooltip series={series} />} />
        {series.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {series.map((item, index) => (
          <Bar
            key={item.label}
            dataKey={`s${index}`}
            fill={`var(--color-s${index})`}
            radius={[4, 4, 0, 0]}
            maxBarSize={48}
          />
        ))}
        {goal != null && (
          <ReferenceLine
            y={goal}
            stroke="var(--foreground)"
            strokeDasharray="4 4"
            label={{
              value: `goal ${formatMoneyCompact(goal)}`,
              position: "insideTopRight",
              fill: "var(--muted-foreground)",
              fontSize: 11,
            }}
          />
        )}
      </BarChart>
    </ChartContainer>
  );
}
