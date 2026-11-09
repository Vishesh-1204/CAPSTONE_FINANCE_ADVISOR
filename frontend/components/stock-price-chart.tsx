// frontend/components/stock-price-chart.tsx
//
// Purpose: daily closing prices of one symbol with its 50- and 200-day simple moving averages
// (GET /api/stocks/{symbol}/prices). The backend computes the averages with 300 extra days of warm-up,
// so both lines start on the first bar shown. A gap in an average line means "not enough history yet".
// Index symbols (^NSEI) are in points, stocks in rupees.

"use client";

import { CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, type ChartConfig } from "@/components/ui/chart";
import { formatDay, formatMoneyCompact } from "@/lib/format";
import { formatPrice, isIndex } from "@/lib/stocks";
import type { PriceBarPoint } from "@/lib/types";

// The console's neutral theme has grey chart tokens only, so identity is carried by line style as well as
// lightness: close = thick solid, 50-day = thin solid, 200-day = thin dashed (plus the legend and the
// tooltip). Steps chosen with the dataviz palette validator per theme (adjacent separation >= 16 and
// contrast >= 3:1 against the surface in BOTH modes); --chart-1 is too light for a white background.
const chartConfig = {
  close: { label: "Close", theme: { light: "var(--chart-5)", dark: "var(--chart-1)" } },
  sma_50: { label: "50-day average", color: "var(--chart-2)" },
  sma_200: { label: "200-day average", theme: { light: "var(--chart-4)", dark: "var(--muted-foreground)" } },
} satisfies ChartConfig;

const points = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

function PriceTooltip({
  active,
  payload,
  symbol,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: PriceBarPoint }>;
  symbol: string;
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const lines: [string, number | null][] = [
    ["Close", row.close],
    ["50-day average", row.sma_50],
    ["200-day average", row.sma_200],
  ];
  return (
    <div className="rounded-lg border bg-background px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{formatDay(row.bar_date, true)}</div>
      {lines.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4">
          <span className="text-muted-foreground">{label}</span>
          <span className="font-mono tabular-nums">{formatPrice(value, symbol)}</span>
        </div>
      ))}
    </div>
  );
}

export function StockPriceChart({ bars, symbol }: { bars: PriceBarPoint[]; symbol: string }) {
  const axis = isIndex(symbol) ? (value: number) => points.format(value) : formatMoneyCompact;
  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-80 w-full">
      <ComposedChart data={bars} margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="bar_date"
          tickFormatter={(value: string) => formatDay(value)}
          tickLine={false}
          axisLine={false}
          minTickGap={32}
        />
        <YAxis width={70} tickFormatter={axis} tickLine={false} axisLine={false} domain={["auto", "auto"]} />
        <ChartTooltip content={<PriceTooltip symbol={symbol} />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Line dataKey="close" type="monotone" stroke="var(--color-close)" strokeWidth={2} dot={false} />
        <Line
          dataKey="sma_50"
          type="monotone"
          stroke="var(--color-sma_50)"
          strokeWidth={1.5}
          dot={false}
          connectNulls={false}
        />
        <Line
          dataKey="sma_200"
          type="monotone"
          stroke="var(--color-sma_200)"
          strokeWidth={1.5}
          strokeDasharray="5 3"
          dot={false}
          connectNulls={false}
        />
      </ComposedChart>
    </ChartContainer>
  );
}
