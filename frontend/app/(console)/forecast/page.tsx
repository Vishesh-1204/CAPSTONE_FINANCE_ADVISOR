// frontend/app/(console)/forecast/page.tsx
//
// Purpose: /forecast — exercise the Financial Forecast Agent (Phase 5) from a browser:
// 1. settings: horizon (30/60/90 days) and the opening balance, prefilled from
//    the latest balance printed on an imported statement; then Generate
// 2. the forecast: cards, daily balance chart with its 80% band, cash-crunch
//    banner, low-confidence notice, explanation (template / AI), budget warnings
// 3. the recurring payments the agent detected (salary, bills, SIPs)
// plus the Raw API response panel. Synthetic data only.
//
// The page owns the state and the three loads (latest run for the chosen
// horizon, recurring items, the statement balance); components only display.
//
// Opening balance rule: if you do NOT edit the prefilled value, the request
// omits opening_balance, so the backend reads the statement balance itself and
// the response says source "statement" with its date. Typing a value sends it
// (source "request"). The prefill uses GET /api/transactions/ (newest first, the
// same order the backend uses) and takes the first row that has a balance.

"use client";

import { RotateCcw, TrendingUp } from "lucide-react";
import { useCallback, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { ForecastResult } from "@/components/forecast-result";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { RecurringItemsTable } from "@/components/recurring-items-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { ApiError, api } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";
import type { ForecastHorizon } from "@/lib/types";

const HORIZONS: ForecastHorizon[] = [30, 60, 90];
// Same rule as the backend query parameter (Decimal, at most 2 decimals); negative = overdraft
const BALANCE_PATTERN = /^-?\d+(\.\d{1,2})?$/;
const BALANCE_SCAN_ROWS = 500; // the backend's maximum page size

export default function ForecastPage() {
  const log = useApiLog();
  const [horizon, setHorizon] = useState<ForecastHorizon>(30);

  // --- loads (each loader is wrapped in useCallback, see hooks/use-api-data.ts) ---
  const loadLatest = useCallback(() => api.getLatestForecast(horizon), [horizon]);
  const latest = useApiData(loadLatest, log.track);
  const loadRecurring = useCallback(() => api.getRecurringItems(), []);
  const recurring = useApiData(loadRecurring, log.track);
  const loadRecentRows = useCallback(() => api.listTransactions({ limit: BALANCE_SCAN_ROWS }), []);
  const recentRows = useApiData(loadRecentRows, log.track);

  // A 404 from /latest just means "no forecast for this horizon yet": not an error to show.
  const noRunYet = latest.error instanceof ApiError && latest.error.status === 404;
  const latestError = noRunYet ? null : latest.error;
  // useApiData keeps the previous data when a new request fails, so only show a run of THIS horizon
  const shownRun = latest.data?.horizon_days === horizon ? latest.data : null;

  // --- opening balance ---
  const statementRow = recentRows.data?.items.find((row) => row.balance != null) ?? null;
  const [typed, setTyped] = useState<string | null>(null); // null = not edited: use the statement balance
  const balanceText = typed ?? (statementRow?.balance != null ? statementRow.balance.toFixed(2) : "");

  // --- generate ---
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<unknown>(null);

  async function generate() {
    setGenerateError(null);
    const text = (typed ?? "").trim();
    // Not edited (or cleared) -> omit the parameter: the backend reads the statement (or answers 400)
    let openingBalance: number | undefined;
    if (text !== "") {
      if (!BALANCE_PATTERN.test(text)) {
        setGenerateError(new Error("Opening balance must be a number with at most 2 decimals, e.g. 45000 or 45000.50."));
        return;
      }
      openingBalance = Number(text);
    }
    setGenerating(true);
    try {
      const { data } = await log.track(api.generateForecast(horizon, openingBalance));
      latest.setData(() => data); // the new run replaces the shown one, no refetch needed
    } catch (err) {
      setGenerateError(err);
    } finally {
      setGenerating(false);
    }
  }

  let balanceHint: string;
  if (typed !== null) balanceHint = "Typed by you: sent with the request.";
  else if (statementRow) balanceHint = `From your statement of ${formatDate(statementRow.transaction_date)}.`;
  else if (recentRows.loading) balanceHint = "Looking for a statement balance…";
  else balanceHint = "No statement balance found: type your current balance.";

  return (
    <>
      <PageHeader
        title="Forecast"
        description="Python forecasts every number (recurring payments on their dates, a backtest-chosen model for everyday spending, an 80% range); the LLM only explains them, and a guard rejects any rupee amount it invents."
      />

      <Card>
        <CardHeader>
          <CardTitle>1. Forecast settings</CardTitle>
          <CardDescription>
            Pick how far ahead to look. The opening balance is your balance today: by default the latest balance printed
            on an imported statement. Type a different number to override it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-6">
            <div className="space-y-1.5">
              <Label>Horizon</Label>
              <Tabs value={String(horizon)} onValueChange={(value) => setHorizon(Number(value) as ForecastHorizon)}>
                <TabsList>
                  {HORIZONS.map((days) => (
                    <TabsTrigger key={days} value={String(days)}>
                      {days} days
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="opening-balance">Opening balance (₹)</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="opening-balance"
                  inputMode="decimal"
                  className="w-44"
                  placeholder="e.g. 45000"
                  value={balanceText}
                  onChange={(event) => setTyped(event.target.value)}
                />
                {typed !== null && statementRow?.balance != null && (
                  <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => setTyped(null)}>
                    <RotateCcw className="size-3.5" />
                    Use statement balance ({formatMoney(statementRow.balance)})
                  </Button>
                )}
              </div>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{balanceHint}</p>
          <Button onClick={generate} disabled={generating} className="gap-2">
            <TrendingUp className="size-4" />
            {generating ? "Forecasting…" : `Generate ${horizon}-day forecast`}
          </Button>
          <ApiErrorAlert error={generateError} />
          <ApiErrorAlert error={recentRows.error} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>2. Your {horizon}-day forecast</CardTitle>
          <CardDescription>
            The latest saved forecast for this horizon. Each Generate saves a new one; switching the horizon shows the
            latest of that horizon.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ApiErrorAlert error={latestError} />
          {shownRun ? (
            <ForecastResult run={shownRun} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {latest.loading ? "Loading…" : `No ${horizon}-day forecast yet. Generate one above.`}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>3. Recurring payments detected</CardTitle>
          <CardDescription>
            Salary, bills and SIPs found in your history. The forecast places each active one on its usual day of the
            month instead of guessing it; a payment counts after 3 similar ones about a month apart.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ApiErrorAlert error={recurring.error} />
          <RecurringItemsTable items={recurring.data?.items ?? null} loading={recurring.loading} />
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
