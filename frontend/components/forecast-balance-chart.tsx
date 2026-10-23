// frontend/components/forecast-balance-chart.tsx
//
// Purpose: the projected daily balance of one forecast (ForecastRun.trajectory)
// as a line, with its 80% range drawn as a shaded band around it.
//
// How the band is drawn: each point gets `band: [lower, upper]`. A Recharts
// <Area> whose value is a [low, high] pair is a RANGE area: it fills between the
// two numbers instead of down to zero. The band widens day by day because only
// everyday spending is uncertain, and that uncertainty adds up over time.
//
// A dashed line marks ₹0 when the band reaches below it, and a vertical line
// marks the cash-crunch date (the first day the LOWER band is below zero).

"use client";

import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { formatDay, formatMoney } from "@/lib/format";
import type { TrajectoryPoint } from "@/lib/types";

const chartConfig = {
  balance: { label: "Projected balance", color: "var(--chart-1)" },
  band: { label: "80% range", color: "var(--chart-1)" },
} satisfies ChartConfig;

const compactInr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  notation: "compact",
  maximumFractionDigits: 1,
});

interface Row {
  date: string;
  balance: number;
  lower: number;
  upper: number;
  band: [number, number];
}

/** Tooltip for one day: date, balance and its range (the default tooltip cannot show a [low, high] pair). */
function BalanceTooltip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: Row }> }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border bg-background px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{formatDay(row.date, true)}</div>
      <div className="flex justify-between gap-4">
        <span className="text-muted-foreground">Balance</span>
        <span className="font-mono tabular-nums">{formatMoney(row.balance)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-muted-foreground">80% range</span>
        <span className="font-mono tabular-nums">
          {formatMoney(row.lower)} – {formatMoney(row.upper)}
        </span>
      </div>
    </div>
  );
}

export function ForecastBalanceChart({
  trajectory,
  cashCrunchDate,
}: {
  trajectory: TrajectoryPoint[];
  cashCrunchDate: string | null;
}) {
  const data: Row[] = trajectory.map((point) => ({ ...point, band: [point.lower, point.upper] }));
  const reachesBelowZero = trajectory.some((point) => point.lower < 0);

  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-80 w-full">
      <ComposedChart data={data} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(value: string) => formatDay(value)}
          tickLine={false}
          axisLine={false}
          minTickGap={24}
        />
        <YAxis
          width={70}
          tickFormatter={(value: number) => compactInr.format(value)}
          tickLine={false}
          axisLine={false}
          domain={["auto", "auto"]}
        />
        <ChartTooltip content={<BalanceTooltip />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Area
          dataKey="band"
          type="monotone"
          stroke="none"
          fill="var(--color-band)"
          fillOpacity={0.15}
          isAnimationActive={false}
        />
        <Line dataKey="balance" type="monotone" stroke="var(--color-balance)" strokeWidth={2} dot={false} />
        {reachesBelowZero && <ReferenceLine y={0} stroke="var(--destructive)" strokeDasharray="4 4" />}
        {cashCrunchDate && (
          <ReferenceLine
            x={cashCrunchDate}
            stroke="var(--destructive)"
            label={{ value: "cash crunch", position: "insideTopLeft", fill: "var(--destructive)", fontSize: 11 }}
          />
        )}
      </ComposedChart>
    </ChartContainer>
  );
}
