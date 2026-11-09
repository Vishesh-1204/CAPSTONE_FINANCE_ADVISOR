// frontend/components/stock-data-card.tsx
//
// Purpose: sections 2 and 3 of /stocks for the selected symbol.
//   2. Refresh: POST /{symbol}/refresh runs the backend's fallback chain (fresh cache -> provider ->
//      stale cache + "data_stale"). A freshness badge ALWAYS shows where the data came from and how
//      old it is; a stale result is explained in plain words, never hidden.
//   3. The price chart (GET /{symbol}/prices, cache only) with the 50- and 200-day averages.
// GET /prices answers 404 until the symbol has been refreshed once: an empty state, not an error.

"use client";

import { CircleAlert, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StockPriceChart } from "@/components/stock-price-chart";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import type { ApiLog } from "@/hooks/use-api-log";
import { ApiError, api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { CACHE_STATUS_TEXT, PROVIDER_LABELS, warningText } from "@/lib/stocks";
import type { RefreshResponse } from "@/lib/types";

const RANGES = [
  { days: 90, label: "3M" },
  { days: 365, label: "1Y" },
  { days: 1825, label: "5Y" },
];

/** Warnings about stale data, in plain words, in an amber box. */
export function StaleWarnings({ codes }: { codes: string[] }) {
  if (codes.length === 0) return null;
  return (
    <Alert className="border-amber-500/50">
      <CircleAlert className="text-amber-600" />
      <AlertTitle>Not the latest data</AlertTitle>
      <AlertDescription>
        <ul className="list-disc pl-4">
          {codes.map((code) => (
            <li key={code}>{warningText(code)}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

function FreshnessBadge({ source, asOf, stale, status }: {
  source: string | null;
  asOf: string | null;
  stale: boolean;
  status?: RefreshResponse["cache_status"];
}) {
  const where = source ? (PROVIDER_LABELS[source] ?? source) : "no source";
  const when = asOf ? `as of ${formatDay(asOf, true)}` : "no data yet";
  const label = status ? CACHE_STATUS_TEXT[status] : stale ? "Stale cache" : "Cached";
  return (
    <Badge
      variant={stale ? "outline" : "secondary"}
      className={stale ? "border-amber-500 text-amber-700 dark:text-amber-400" : undefined}
    >
      {label} · {where} · {when}
    </Badge>
  );
}

export function StockDataCard({
  symbol,
  track,
  onRefreshed,
}: {
  symbol: string;
  track: ApiLog["track"];
  onRefreshed: () => void;
}) {
  const [days, setDays] = useState(365);
  const loadPrices = useCallback(() => api.getStockPrices(symbol, days), [symbol, days]);
  const prices = useApiData(loadPrices, track);
  const notCached = prices.error instanceof ApiError && prices.error.status === 404 && !prices.data;

  const [refreshing, setRefreshing] = useState(false);
  const [refreshed, setRefreshed] = useState<RefreshResponse | null>(null);
  const [refreshError, setRefreshError] = useState<unknown>(null);
  const { reload: reloadPrices } = prices;

  async function refresh() {
    setRefreshing(true);
    setRefreshError(null);
    try {
      const { data } = await track(api.refreshStock(symbol));
      setRefreshed(data);
      reloadPrices();
      onRefreshed();
    } catch (err) {
      setRefreshError(err);
    } finally {
      setRefreshing(false);
    }
  }

  // The badge reflects the last refresh if there was one, else what the cache holds
  const shown = prices.data;
  const priceWarnings = refreshed?.warnings ?? shown?.warnings ?? [];
  const stale = priceWarnings.includes("data_stale");
  const source = refreshed?.price_source ?? shown?.sources[shown.sources.length - 1] ?? null;
  const asOf = refreshed?.data_as_of ?? shown?.data_as_of ?? null;
  const noProviderAnswer = refreshError instanceof ApiError && refreshError.status === 503;

  return (
    <Card>
      <CardHeader>
        <CardTitle>2. Data and freshness · {symbol}</CardTitle>
        <CardDescription>
          Refresh asks the provider only when the cache is out of date (at most once a day after the 16:00 IST close).
          If the provider fails, the most recent cached prices are used and marked as stale.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={refresh} disabled={refreshing} className="gap-2">
            <RefreshCw className={`size-4 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
          {(shown || refreshed) && <FreshnessBadge source={source} asOf={asOf} stale={stale}
                                                   status={refreshed?.cache_status} />}
          {refreshed && (
            <span className="text-xs text-muted-foreground">
              {refreshed.bars_saved} bars and {refreshed.headlines_saved} headlines saved
              {refreshed.news_source ? ` · news from ${PROVIDER_LABELS[refreshed.news_source]}` : ""}
            </span>
          )}
        </div>
        <StaleWarnings codes={priceWarnings} />
        <ApiErrorAlert error={refreshError} />
        {noProviderAnswer && (
          <p className="text-sm text-muted-foreground">
            The provider did not answer and nothing is cached for {symbol} yet. Try again later, or upload a CSV of daily
            prices in section 8.
          </p>
        )}

        <div className="space-y-2 border-t pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium">3. Price with the 50- and 200-day averages</h3>
            <div className="flex gap-1" role="group" aria-label="Chart range">
              {RANGES.map((range) => (
                <Button
                  key={range.days}
                  size="sm"
                  variant={days === range.days ? "secondary" : "ghost"}
                  onClick={() => setDays(range.days)}
                  aria-pressed={days === range.days}
                >
                  {range.label}
                </Button>
              ))}
            </div>
          </div>
          {!notCached && <ApiErrorAlert error={prices.data ? null : prices.error} />}
          {prices.loading && !prices.data ? (
            <p className="text-sm text-muted-foreground">Loading prices…</p>
          ) : notCached ? (
            <p className="text-sm text-muted-foreground">No prices cached for {symbol} yet. Press Refresh above.</p>
          ) : shown && shown.bars.length > 0 ? (
            <StockPriceChart bars={shown.bars} symbol={symbol} />
          ) : shown ? (
            <p className="text-sm text-muted-foreground">No bars in this range.</p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
