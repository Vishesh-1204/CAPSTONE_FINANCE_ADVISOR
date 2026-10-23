// frontend/components/holdings-editor.tsx
//
// Purpose: section 2 of /investing. Edit what you already hold, in rupees per
// asset class (equity, debt, gold, cash), with an optional label, and save it with
// PUT /api/investing/holdings. Then show each class's share of the total.
//
// The PUT REPLACES the whole list, so the editor must always send every holding.
// The backend allows several holdings in one class as long as their labels differ
// ("index fund" and "flexi-cap" are both equity), so each class starts with one row
// and "Add another" adds more. A strict one-row-per-class editor would silently
// delete the extra holdings on the next save.
// Rows with an empty amount are not sent (that is how you remove a holding).

"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatMoney, formatPercent } from "@/lib/format";
import { ASSET_CLASSES, ASSET_LABELS } from "@/lib/investing";
import type { AssetClass, HoldingIn, HoldingsResponse } from "@/lib/types";

const MONEY_PATTERN = /^\d+(\.\d{1,2})?$/;
const MAX_LABEL_LENGTH = 100;
const MAX_HOLDINGS = 50;

interface Row {
  key: number; // stable React key (rows can be added and removed)
  asset_class: AssetClass;
  amount: string;
  label: string;
}

/** Every saved holding as a row, grouped by class, plus one empty row for any class with none. */
function initialRows(holdings: HoldingsResponse | null): Row[] {
  let key = 0;
  const rows: Row[] = [];
  for (const assetClass of ASSET_CLASSES) {
    const saved = holdings?.holdings.filter((holding) => holding.asset_class === assetClass) ?? [];
    if (saved.length === 0) rows.push({ key: key++, asset_class: assetClass, amount: "", label: "" });
    for (const holding of saved) {
      rows.push({ key: key++, asset_class: assetClass, amount: String(holding.amount), label: holding.label });
    }
  }
  return rows;
}

/** Returns the request list, or an error message. */
function toRequest(rows: Row[]): HoldingIn[] | string {
  const holdings: HoldingIn[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const amount = row.amount.trim();
    if (amount === "") continue; // empty = not held
    const name = `${ASSET_LABELS[row.asset_class]}${row.label.trim() ? ` "${row.label.trim()}"` : ""}`;
    if (!MONEY_PATTERN.test(amount)) return `${name}: the amount must be a number with at most 2 decimals.`;
    if (row.label.trim().length > MAX_LABEL_LENGTH) return `${name}: the label is longer than ${MAX_LABEL_LENGTH} characters.`;
    // Same rule as the backend: one holding per (class, label), labels compared without case
    const duplicateKey = `${row.asset_class}|${row.label.trim().toLowerCase()}`;
    if (seen.has(duplicateKey)) return `${name} appears twice. Give each ${ASSET_LABELS[row.asset_class]} row a different label.`;
    seen.add(duplicateKey);
    holdings.push({ asset_class: row.asset_class, amount: Number(amount), label: row.label.trim() });
  }
  if (holdings.length > MAX_HOLDINGS) return `At most ${MAX_HOLDINGS} holdings.`;
  return holdings;
}

export function HoldingsEditor({
  holdings,
  loading,
  error,
  track,
  onSaved,
}: {
  holdings: HoldingsResponse | null;
  loading: boolean;
  error: unknown;
  track: ApiLog["track"];
  onSaved: (holdings: HoldingsResponse) => void;
}) {
  const hasHoldings = (holdings?.holdings.length ?? 0) > 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          2. Holdings
          {!loading && !hasHoldings && <Badge variant="outline">none saved</Badge>}
        </CardTitle>
        <CardDescription>
          What you already hold, in rupees. The plan compares this split with its target. Leave an amount empty to
          remove that holding; Save replaces the whole list.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <ApiErrorAlert error={error} />
        {loading && !holdings ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          // `key` re-creates the form whenever a new list is saved (fresh rows, no effect needed)
          <HoldingsForm
            key={JSON.stringify(holdings?.holdings ?? [])}
            holdings={holdings}
            track={track}
            onSaved={onSaved}
          />
        )}
        {holdings && hasHoldings ? (
          <div className="space-y-2 border-t pt-4">
            <div className="text-xs text-muted-foreground">Current weights (share of {formatMoney(holdings.total)})</div>
            <StatGrid>
              {ASSET_CLASSES.map((assetClass) => (
                <StatTile
                  key={assetClass}
                  label={ASSET_LABELS[assetClass]}
                  value={formatPercent(holdings.weights_by_class[assetClass] ?? 0)}
                  hint={formatMoney(
                    holdings.holdings
                      .filter((holding) => holding.asset_class === assetClass)
                      .reduce((sum, holding) => sum + holding.amount, 0),
                  )}
                />
              ))}
            </StatGrid>
            <p className="text-xs text-muted-foreground">{holdings.disclaimer}</p>
          </div>
        ) : (
          !loading && (
            <p className="text-sm text-muted-foreground">
              No holdings saved yet. A plan still works without them, but it cannot suggest rebalancing.
            </p>
          )
        )}
      </CardContent>
    </Card>
  );
}

function HoldingsForm({ holdings, track, onSaved }: {
  holdings: HoldingsResponse | null;
  track: ApiLog["track"];
  onSaved: (holdings: HoldingsResponse) => void;
}) {
  const [rows, setRows] = useState<Row[]>(() => initialRows(holdings));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  function update(key: number, field: "amount" | "label", value: string) {
    setRows((previous) => previous.map((row) => (row.key === key ? { ...row, [field]: value } : row)));
  }

  /** A new empty row right after the last row of that class. */
  function addRow(assetClass: AssetClass) {
    setRows((previous) => {
      const nextKey = Math.max(...previous.map((row) => row.key)) + 1;
      const lastIndex = previous.findLastIndex((row) => row.asset_class === assetClass);
      const copy = [...previous];
      copy.splice(lastIndex + 1, 0, { key: nextKey, asset_class: assetClass, amount: "", label: "" });
      return copy;
    });
  }

  function removeRow(key: number) {
    setRows((previous) => previous.filter((row) => row.key !== key));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = toRequest(rows);
    if (typeof request === "string") {
      setError(new Error(request));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await track(api.saveHoldings(request));
      onSaved(data);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-4">
      <div className="space-y-4">
        {ASSET_CLASSES.map((assetClass) => {
          const classRows = rows.filter((row) => row.asset_class === assetClass);
          return (
            <div key={assetClass} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{ASSET_LABELS[assetClass]}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="gap-1.5"
                  onClick={() => addRow(assetClass)}
                  disabled={busy}
                >
                  <Plus className="size-3.5" />
                  Add another
                </Button>
              </div>
              {classRows.map((row, index) => (
                <div key={row.key} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
                  <Input
                    aria-label={`${ASSET_LABELS[assetClass]} amount`}
                    inputMode="decimal"
                    autoComplete="off"
                    placeholder="Amount (₹)"
                    value={row.amount}
                    onChange={(event) => update(row.key, "amount", event.target.value)}
                    disabled={busy}
                  />
                  <Input
                    aria-label={`${ASSET_LABELS[assetClass]} label`}
                    autoComplete="off"
                    placeholder="Label (optional)"
                    value={row.label}
                    onChange={(event) => update(row.key, "label", event.target.value)}
                    disabled={busy}
                  />
                  {/* the first row of a class stays (empty it to remove the holding); extra rows can go */}
                  {index > 0 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Remove row"
                      onClick={() => removeRow(row.key)}
                      disabled={busy}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  ) : (
                    <span className="size-9" />
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>
      <Button type="submit" disabled={busy}>
        {busy ? "Saving…" : "Save holdings"}
      </Button>
      <ApiErrorAlert error={error} />
    </form>
  );
}
