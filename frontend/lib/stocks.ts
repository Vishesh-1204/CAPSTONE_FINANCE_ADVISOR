// frontend/lib/stocks.ts
//
// Purpose: plain-words text and formatting for the /stocks page (Phase 7), kept in one place so
// every card says the same thing. The warning sentences match the backend's
// agents/stock_explainer.py WARNING_TEXT, so the page and the explanation never disagree.
// Descriptive only: nothing here (or anywhere in the UI) says buy, sell, hold or what a price will do.

import { formatMoney } from "./format";
import type { CacheStatus, RsiLabel, TrendLabel } from "./types";

/** The permanent banner; identical to the backend's STOCKS_DISCLAIMER. */
export const STOCKS_BANNER = "Educational analysis tool, not financial advice. Past data does not predict future prices.";

export const WARNING_TEXT: Record<string, string> = {
  data_stale: "The price data could not be refreshed, so this uses the most recent cached prices.",
  insufficient_history: "There is less than a year of price history, so some measures are missing.",
  benchmark_unavailable: "No index prices were available, so beta and correlation are missing.",
  benchmark_stale: "The index prices could not be refreshed, so beta and correlation use cached prices.",
  news_stale: "Headlines could not be refreshed, so the sentiment uses earlier headlines.",
  news_unavailable: "No headlines could be loaded, so there is no sentiment reading.",
  few_headlines: "There are too few headlines for a reliable sentiment reading.",
  finbert_unavailable: "The FinBERT model was not available, so the simpler word-list scorer was used.",
  rows_rejected: "Some rows were skipped (bad date, a future date, a missing or non-positive price, or low above high).",
  existing_days_kept: "Days that were already cached were kept; an upload only adds missing days.",
};

/** A warning code in plain words (unknown codes are shown as they are). */
export function warningText(code: string): string {
  return WARNING_TEXT[code] ?? code;
}

export const TREND_TEXT: Record<TrendLabel, string> = {
  above_both_averages: "Last close above both its 50-day and 200-day averages",
  below_both_averages: "Last close below both its 50-day and 200-day averages",
  between_averages: "Last close between its 50-day and 200-day averages",
  not_enough_history: "Not enough history for the 200-day average",
};

/** RSI labels are CONVENTIONS, not signals ("high" does not mean "about to fall"). */
export const RSI_TEXT: Record<RsiLabel, string> = {
  high: "high by the common convention (above 70)",
  normal: "in the usual range by the common convention (30 to 70)",
  low: "low by the common convention (below 30)",
};

export const CACHE_STATUS_TEXT: Record<CacheStatus, string> = {
  fresh: "Cached (up to date)",
  refreshed: "Just refreshed",
  stale: "Stale (provider failed)",
};

export const PROVIDER_LABELS: Record<string, string> = {
  yfinance: "yfinance",
  alphavantage: "Alpha Vantage",
  csv_upload: "CSV upload",
  google_rss: "Google News RSS",
};

/** An index ("^NSEI") is in points, a stock in rupees: the same rule as the backend's money_text. */
export function isIndex(symbol: string): boolean {
  return symbol.startsWith("^");
}

const points = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatPrice(value: number | null | undefined, symbol: string): string {
  if (value == null) return "—";
  return isIndex(symbol) ? `${points.format(value)} points` : formatMoney(value);
}

/** A signed share of 1 as a percentage: 0.093 -> "+9.3%". */
export function formatSignedPercent(value: number | null | undefined): string {
  if (value == null) return "—";
  const text = `${(value * 100).toFixed(1)}%`;
  return value > 0 ? `+${text}` : text;
}

/** A score from -1 to +1 with its sign: 0.2628 -> "+0.26". */
export function formatSignedDecimal(value: number | null | undefined, places = 2): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(places)}`;
}

export function formatDecimal(value: number | null | undefined, places = 2): string {
  return value == null ? "—" : value.toFixed(places);
}

/** Same pattern as backend/app/core/stock_symbols.py (checked again by the backend). */
export const SYMBOL_PATTERN = /^\^?[A-Z0-9][A-Z0-9.-]*$/;
export const MAX_SYMBOL_LENGTH = 20; // the whole symbol, caret included

/** Upper-cased, trimmed symbol, or an error message. */
export function parseSymbol(raw: string): string | { error: string } {
  const symbol = raw.trim().toUpperCase();
  if (!symbol) return { error: "Type a symbol, e.g. RELIANCE.NS (NSE stocks end in .NS)." };
  if (symbol.length > MAX_SYMBOL_LENGTH || !SYMBOL_PATTERN.test(symbol)) {
    return { error: "Use letters, digits, '.', '-' and an optional leading '^', at most 20 characters." };
  }
  return symbol;
}
