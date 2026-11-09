// frontend/components/stock-analysis-section.tsx
//
// Purpose: sections 4-6 of /stocks for the selected symbol, from one saved analysis
// (POST /{symbol}/analysis to make a new one, GET /{symbol}/analysis/latest on load; 404 = none yet).
//   4. the analysis card: trend, RSI with its CONVENTION label, volatility, largest fall, VaR, beta
//   5. the sentiment card: aggregate score, the headlines used, "not enough headlines" when unreliable
//   6. the explanation, with an "AI-written" or "Template" badge
// Every value is a description of the past; null means "not enough history" and is shown as "—".

"use client";

import { ExternalLink, Sparkles } from "lucide-react";
import { useCallback, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { ExplanationBadge } from "@/components/investment-plan-result";
import { StaleWarnings } from "@/components/stock-data-card";
import { StatTile } from "@/components/stat-tile";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import type { ApiLog } from "@/hooks/use-api-log";
import { ApiError, api } from "@/lib/api";
import { formatDateTime, formatDay, formatPercent, humanize } from "@/lib/format";
import {
  PROVIDER_LABELS,
  RSI_TEXT,
  TREND_TEXT,
  formatDecimal,
  formatPrice,
  formatSignedDecimal,
  formatSignedPercent,
  warningText,
} from "@/lib/stocks";
import type { StockAnalysis, StockSentiment } from "@/lib/types";

const STALE_CODES = new Set(["data_stale", "benchmark_stale", "news_stale"]);

function IndicatorsCard({ analysis }: { analysis: StockAnalysis }) {
  const ind = analysis.indicators;
  const symbol = analysis.symbol;
  const other = analysis.warnings.filter((code) => !STALE_CODES.has(code));
  return (
    <Card>
      <CardHeader>
        <CardTitle>4. Analysis · {symbol}</CardTitle>
        <CardDescription>
          Data as of {formatDay(analysis.data_as_of, true)} · {ind.bars_used} trading days ·{" "}
          {analysis.created_at ? `made ${formatDateTime(analysis.created_at)}` : ""} · prices from{" "}
          {PROVIDER_LABELS[analysis.sources.prices] ?? analysis.sources.prices} ({analysis.sources.prices_cache}).
          Risk figures cover the last 252 trading days.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <StaleWarnings codes={analysis.warnings.filter((code) => STALE_CODES.has(code))} />
        {other.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {other.map((code) => (
              <li key={code}>{warningText(code)}</li>
            ))}
          </ul>
        )}
        <div className="rounded-lg border bg-muted/30 p-3 text-sm">
          <span className="font-medium">Trend: </span>
          {TREND_TEXT[ind.trend]}
          {ind.sma_50 != null && ind.sma_200 != null && (
            <span className="text-muted-foreground">
              {" "}
              (close {formatPrice(ind.last_close, symbol)}; 50-day {formatPrice(ind.sma_50, symbol)}; 200-day{" "}
              {formatPrice(ind.sma_200, symbol)})
            </span>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatTile label="Last close" value={formatPrice(ind.last_close, symbol)} />
          <StatTile label="Change, 1 month" value={formatSignedPercent(ind.change_1m)} />
          <StatTile label="Change, 1 year" value={formatSignedPercent(ind.change_1y)} />
          <StatTile
            label="RSI (14 days)"
            value={formatDecimal(ind.rsi_14, 1)}
            hint={ind.rsi_label ? RSI_TEXT[ind.rsi_label] : "not enough history"}
          />
          <StatTile label="Volatility (a year)" value={formatPercent(ind.annualized_volatility)} />
          <StatTile
            label="Largest fall from a peak"
            value={ind.max_drawdown == null ? "—" : formatPercent(Math.abs(ind.max_drawdown))}
          />
          <StatTile
            label="One-day loss (95% VaR)"
            value={formatPercent(ind.var_95_one_day)}
            hint="on 95% of past days the loss was smaller"
          />
          <StatTile
            label={`Beta vs ${ind.benchmark ?? "benchmark"}`}
            value={formatDecimal(ind.beta)}
            hint={ind.correlation == null ? "no benchmark data" : `correlation ${formatDecimal(ind.correlation)}`}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          RSI above 70 or below 30 is a common convention, not a signal; none of these figures predicts a future price.
        </p>
      </CardContent>
    </Card>
  );
}

function SentimentCard({ sentiment, warnings }: { sentiment: StockSentiment | null; warnings: string[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>5. News sentiment</CardTitle>
        <CardDescription>
          Each headline is scored positive / negative / neutral; the score is positive minus negative (-1 to +1), with
          newer headlines counting more (3-day half-life). Headlines are never sent to the LLM.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!sentiment ? (
          <p className="text-sm text-muted-foreground">
            {warnings.includes("news_unavailable") ? warningText("news_unavailable") : "No headlines for this analysis."}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <StatTile
                label="Aggregate score"
                value={formatSignedDecimal(sentiment.score)}
                hint={`${humanize(sentiment.label)} · ${sentiment.n_items} headlines · ${sentiment.scorer}`}
              />
              {!sentiment.reliable && (
                <Alert className="max-w-md border-amber-500/50">
                  <AlertTitle>Not enough headlines</AlertTitle>
                  <AlertDescription>
                    Fewer than 3 headlines: this reading is not reliable. One headline should never drive the story.
                  </AlertDescription>
                </Alert>
              )}
            </div>
            <ul className="divide-y rounded-md border text-sm">
              {sentiment.headlines.map((item) => {
                const score =
                  item.sentiment_positive != null && item.sentiment_negative != null
                    ? item.sentiment_positive - item.sentiment_negative
                    : null;
                return (
                  <li key={item.url} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="inline-flex items-start gap-1 hover:underline"
                      >
                        <span>{item.headline}</span>
                        <ExternalLink className="mt-0.5 size-3 shrink-0" />
                      </a>
                      <div className="text-xs text-muted-foreground">
                        {item.publisher ?? "unknown publisher"} · {formatDateTime(item.published_at)}
                      </div>
                    </div>
                    <Badge variant="outline" className="font-mono tabular-nums">
                      {formatSignedDecimal(score)}
                    </Badge>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ExplanationCard({ analysis }: { analysis: StockAnalysis }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>6. Explanation</CardTitle>
          <ExplanationBadge plan={analysis} />
        </div>
        <CardDescription>
          Written from the figures above only. An AI text is shown only if every number in it is one of those figures
          and it gives no advice or prediction; otherwise a fixed template is used. Engine {analysis.engine_version}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="whitespace-pre-line text-sm leading-relaxed">{analysis.explanation ?? "—"}</p>
      </CardContent>
    </Card>
  );
}

export function StockAnalysisSection({
  symbol,
  track,
  dataVersion,
}: {
  symbol: string;
  track: ApiLog["track"];
  dataVersion: number; // bumps after a refresh or an upload (the analysis does not reload, a new one is needed)
}) {
  const loadLatest = useCallback(() => api.getLatestStockAnalysis(symbol), [symbol]);
  const latest = useApiData(loadLatest, track);
  const [made, setMade] = useState<StockAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const analysis = made ?? latest.data;
  const latestError = (latest.error instanceof ApiError && latest.error.status === 404) || latest.data ? null : latest.error;

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      const { data } = await track(api.analyzeStock(symbol));
      setMade(data);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Generate an analysis</CardTitle>
          <CardDescription>
            Python computes every figure from the cached prices and headlines; one LLM call explains them. Each analysis
            is saved.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={analyze} disabled={busy} className="gap-2">
              <Sparkles className="size-4" />
              {busy ? "Analysing…" : `Analyse ${symbol}`}
            </Button>
            {dataVersion > 0 && analysis && !made && (
              <span className="text-sm text-muted-foreground">The data changed: analyse again to use it.</span>
            )}
          </div>
          <ApiErrorAlert error={error} />
          <ApiErrorAlert error={latestError} />
          {!analysis && (
            <p className="text-sm text-muted-foreground">
              {latest.loading ? "Loading the latest analysis…" : `No analysis of ${symbol} yet. Press Analyse.`}
            </p>
          )}
        </CardContent>
      </Card>
      {analysis && (
        <>
          <IndicatorsCard analysis={analysis} />
          <SentimentCard sentiment={analysis.sentiment} warnings={analysis.warnings} />
          <ExplanationCard analysis={analysis} />
        </>
      )}
    </>
  );
}
