// frontend/components/stock-watchlist-card.tsx
//
// Purpose: section 1 of /stocks. Your watchlist with the date of the newest cached bar, a form to add a
// symbol (checked here AND by the backend) and a remove button per row. Clicking a row selects it for the
// sections below. The per-user limit protects the free market-data quota (the backend answers 409 when full).

"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { parseSymbol } from "@/lib/stocks";
import type { WatchlistResponse } from "@/lib/types";

export function StockWatchlistCard({
  watchlist,
  loading,
  error,
  selected,
  onSelect,
  onChanged,
  track,
}: {
  watchlist: WatchlistResponse | null;
  loading: boolean;
  error: unknown;
  selected: string | null;
  onSelect: (symbol: string | null) => void;
  onChanged: (watchlist: WatchlistResponse) => void;
  track: ApiLog["track"];
}) {
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // "add" or the symbol being removed
  const [actionError, setActionError] = useState<unknown>(null);
  const items = watchlist?.items ?? [];
  const full = watchlist ? items.length >= watchlist.limit : false;

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionError(null);
    const parsed = parseSymbol(symbol);
    if (typeof parsed !== "string") {
      setActionError(new Error(parsed.error));
      return;
    }
    setBusy("add");
    try {
      const { data } = await track(api.addToWatchlist(parsed));
      onChanged(data);
      onSelect(parsed);
      setSymbol("");
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(null);
    }
  }

  async function remove(target: string) {
    setActionError(null);
    setBusy(target);
    try {
      const { data } = await track(api.removeFromWatchlist(target));
      onChanged(data);
      if (selected === target) onSelect(data.items[0]?.symbol ?? null);
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>1. Watchlist</CardTitle>
        <CardDescription>
          NSE stocks end in <code>.NS</code> (e.g. RELIANCE.NS); the Nifty 50 index is <code>^NSEI</code>. At most{" "}
          {watchlist?.limit ?? 10} symbols, to protect the free data quota. Click a row to analyse it below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={add} className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="symbol">Symbol</Label>
            <Input
              id="symbol"
              autoComplete="off"
              placeholder="RELIANCE.NS"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              disabled={busy !== null}
              className="w-48 uppercase"
            />
          </div>
          <Button type="submit" disabled={busy !== null || full} className="gap-2">
            <Plus className="size-4" />
            {busy === "add" ? "Adding…" : "Add"}
          </Button>
          {full && <span className="text-sm text-muted-foreground">The list is full: remove a symbol first.</span>}
        </form>
        <ApiErrorAlert error={actionError} />
        <ApiErrorAlert error={error} />

        {loading && !watchlist ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Your watchlist is empty. Add a symbol above.</p>
        ) : (
          <ul className="divide-y rounded-md border">
            {items.map((item) => {
              const active = item.symbol === selected;
              return (
                <li key={item.symbol} className={`flex items-center gap-3 px-3 py-2 ${active ? "bg-muted" : ""}`}>
                  <button
                    type="button"
                    onClick={() => onSelect(item.symbol)}
                    className="flex flex-1 flex-wrap items-baseline gap-x-3 text-left"
                    aria-pressed={active}
                  >
                    <span className="font-mono font-medium">{item.symbol}</span>
                    <span className="text-xs text-muted-foreground">
                      {item.last_bar_date
                        ? `data to ${formatDay(item.last_bar_date, true)}`
                        : "no data yet: press Refresh below"}
                    </span>
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(item.symbol)}
                    disabled={busy !== null}
                    aria-label={`Remove ${item.symbol}`}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
