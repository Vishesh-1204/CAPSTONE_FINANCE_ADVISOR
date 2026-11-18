// frontend/app/(console)/stocks/page.tsx
//
// Purpose: /stocks — exercise the Stock Intelligence Agent (Phase 7) from a browser:
//   1. watchlist (add / remove, date of the newest cached bar)
//   2. refresh with a freshness badge (source + "as of"), stale data explained in plain words
//   3. price chart with the 50- and 200-day averages
//   4. analysis card (trend, RSI with its convention label, volatility, largest fall, VaR, beta)
//   5. sentiment card (headlines, aggregate score, "not enough headlines")
//   6. the explanation with an AI-written / Template badge
//   7. a headline scorer (lexicon and, when installed, FinBERT)
//   8. CSV upload, the last fallback when no provider works
//   9. status panel (providers, calls today, FinBERT, cache ages)
// plus a permanent disclaimer banner and the Raw API response panel.
//
// The page owns the watchlist, the status and WHICH symbol is selected; sections 2-6 and 8 are
// re-mounted per symbol (key={symbol}) so nothing from one stock leaks into the next.

"use client";

import { Info } from "lucide-react";
import { useCallback, useState } from "react";

import { HeadlineScorer } from "@/components/headline-scorer";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { StockAnalysisSection } from "@/components/stock-analysis-section";
import { StockCsvUpload } from "@/components/stock-csv-upload";
import { StockDataCard } from "@/components/stock-data-card";
import { StockStatusPanel } from "@/components/stock-status-panel";
import { StockWatchlistCard } from "@/components/stock-watchlist-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { STOCKS_BANNER } from "@/lib/stocks";

export default function StocksPage() {
  const log = useApiLog();

  const loadWatchlist = useCallback(() => api.getWatchlist(), []);
  const watchlist = useApiData(loadWatchlist, log.track);
  const loadStatus = useCallback(() => api.getStockStatus(), []);
  const status = useApiData(loadStatus, log.track);

  // The selected symbol: the one clicked if it is still on the list, else the first one
  const [chosen, setChosen] = useState<string | null>(null);
  const items = watchlist.data?.items ?? [];
  const selected = chosen && items.some((item) => item.symbol === chosen) ? chosen : (items[0]?.symbol ?? null);

  // Bumped after a refresh or an upload: the watchlist dates and the status change, and the shown
  // analysis is older than the data (the analysis section says so)
  const [dataVersion, setDataVersion] = useState(0);
  const { reload: reloadWatchlist } = watchlist;
  const { reload: reloadStatus } = status;
  const dataChanged = useCallback(() => {
    setDataVersion((version) => version + 1);
    reloadWatchlist();
    reloadStatus();
  }, [reloadWatchlist, reloadStatus]);

  return (
    <>
      <PageHeader
        title="Stocks"
        description="Python computes every figure (trend, RSI, volatility, largest fall, VaR, beta, news sentiment) from cached daily prices and headlines; the LLM only describes them, and guards reject invented numbers, advice and predictions."
      />

      <Alert className="border-primary/40">
        <Info />
        <AlertTitle>{STOCKS_BANNER}</AlertTitle>
        <AlertDescription>
          A description of the past and the news only: it never says buy, sell or hold, or what a price will do. Prices
          are daily and may be delayed.
        </AlertDescription>
      </Alert>

      <StockWatchlistCard
        watchlist={watchlist.data}
        loading={watchlist.loading}
        error={watchlist.error}
        selected={selected}
        onSelect={setChosen}
        onChanged={(data) => {
          watchlist.setData(() => data);
          reloadStatus();
        }}
        track={log.track}
      />

      {selected ? (
        <div key={selected} className="space-y-6">
          <StockDataCard symbol={selected} track={log.track} onRefreshed={dataChanged} />
          <StockAnalysisSection symbol={selected} track={log.track} dataVersion={dataVersion} />
          <StockCsvUpload symbol={selected} track={log.track} onUploaded={dataChanged} />
        </div>
      ) : (
        !watchlist.loading && (
          <p className="text-sm text-muted-foreground">
            Add a symbol to the watchlist to see its data, chart and analysis.
          </p>
        )
      )}

      <HeadlineScorer finbertAvailable={status.data?.finbert_available ?? false} track={log.track} />

      <StockStatusPanel
        status={status.data}
        loading={status.loading}
        error={status.error}
        onReload={reloadStatus}
      />

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
