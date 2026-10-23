// frontend/components/allocation-chart.tsx
//
// Purpose: target vs current allocation of one investment plan, as grouped bars
// (one group per asset class, % of the portfolio). The rebalance table next to it
// gives the exact numbers and the rupee changes, so the chart is the overview and
// the table is the accessible "table view".
//
// Colours: the console's grayscale chart tokens. "Current" is the mid gray; "Target"
// is the high-contrast step, darkest in light mode and lightest in dark mode
// (checked with the dataviz palette validator: the pair is clearly separable in
// both modes, including for colour-blind readers). A legend always names them.

"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { formatPercent } from "@/lib/format";
import { ASSET_CLASSES, ASSET_LABELS } from "@/lib/investing";
import type { AssetClass } from "@/lib/types";

const chartConfig = {
  current: { label: "Current", color: "var(--chart-2)" },
  target: { label: "Target", theme: { light: "var(--chart-5)", dark: "var(--chart-1)" } },
} satisfies ChartConfig;

interface Row {
  label: string;
  current: number;
  target: number;
}

function AllocationTooltip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: Row }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border bg-background px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{row.label}</div>
      {(["target", "current"] as const).map((key) => (
        <div key={key} className="flex justify-between gap-4">
          <span className="text-muted-foreground">{chartConfig[key].label}</span>
          <span className="font-mono tabular-nums">{formatPercent(row[key])}</span>
        </div>
      ))}
    </div>
  );
}

export function AllocationChart({
  target,
  current,
}: {
  target: Partial<Record<AssetClass, number>>;
  current: Partial<Record<AssetClass, number>>;
}) {
  const data: Row[] = ASSET_CLASSES.map((assetClass) => ({
    label: ASSET_LABELS[assetClass],
    current: current[assetClass] ?? 0,
    target: target[assetClass] ?? 0,
  }));

  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
      <BarChart data={data} margin={{ left: 0, right: 8, top: 8 }} barGap={2}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} />
        <YAxis
          width={44}
          domain={[0, 1]}
          tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
          tickLine={false}
          axisLine={false}
        />
        <ChartTooltip cursor={{ fillOpacity: 0.3 }} content={<AllocationTooltip />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="target" fill="var(--color-target)" radius={[4, 4, 0, 0]} maxBarSize={36} />
        <Bar dataKey="current" fill="var(--color-current)" radius={[4, 4, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ChartContainer>
  );
}
