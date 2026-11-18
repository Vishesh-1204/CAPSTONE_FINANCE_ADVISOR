// frontend/components/stock-status-panel.tsx
//
// Purpose: section 9 of /stocks (GET /api/stocks/status): which market-data and news providers are in
// use, how many calls they got today and how the last one went (a rate limit or outage shows here), the
// active sentiment scorer and whether FinBERT is installed, and how old the cache is for each of your
// symbols plus the benchmark.

"use client";

import { RefreshCw } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatDay, humanize } from "@/lib/format";
import { PROVIDER_LABELS } from "@/lib/stocks";
import type { StockStatus } from "@/lib/types";

export function StockStatusPanel({
  status,
  loading,
  error,
  onReload,
}: {
  status: StockStatus | null;
  loading: boolean;
  error: unknown;
  onReload: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>9. Status</CardTitle>
          <Button variant="outline" size="sm" onClick={onReload} disabled={loading} className="gap-2">
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
            Reload
          </Button>
        </div>
        <CardDescription>
          Calls are counted since midnight IST. After a rate limit or an outage the provider is left alone for a while
          (15 minutes, doubling with each failure in a row) and the cache is served instead.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ApiErrorAlert error={error} />
        {loading && !status ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !status ? null : (
          <>
            <div className="flex flex-wrap gap-2 text-sm">
              <Badge variant="secondary">Prices: {PROVIDER_LABELS[status.market_data_provider]}</Badge>
              <Badge variant="secondary">News: {PROVIDER_LABELS[status.news_provider]}</Badge>
              <Badge variant="secondary">Scorer: {status.sentiment_scorer}</Badge>
              <Badge variant={status.finbert_available ? "secondary" : "outline"}>
                FinBERT {status.finbert_available ? "available" : "not installed"}
              </Badge>
            </div>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Provider</TableHead>
                    <TableHead className="text-right">Calls today</TableHead>
                    <TableHead>Daily limit</TableHead>
                    <TableHead>Last call</TableHead>
                    <TableHead>Last status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {status.providers.map((provider) => (
                    <TableRow key={provider.provider}>
                      <TableCell>{PROVIDER_LABELS[provider.provider] ?? provider.provider}</TableCell>
                      <TableCell className="text-right tabular-nums">{provider.calls_today}</TableCell>
                      <TableCell>{provider.daily_limit ?? "none published"}</TableCell>
                      <TableCell>{provider.last_call_at ? formatDateTime(provider.last_call_at) : "never"}</TableCell>
                      <TableCell>
                        {provider.last_status ? (
                          <Badge variant={provider.last_status === "ok" ? "secondary" : "destructive"}>
                            {humanize(provider.last_status)}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Newest bar</TableHead>
                    <TableHead>Fetched</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {status.cache.map((row) => (
                    <TableRow key={row.symbol}>
                      <TableCell className="font-mono">{row.symbol}</TableCell>
                      <TableCell>{row.latest_bar_date ? formatDay(row.latest_bar_date, true) : "not cached"}</TableCell>
                      <TableCell>{row.fetched_at ? formatDateTime(row.fetched_at) : "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
