// frontend/components/stock-csv-upload.tsx
//
// Purpose: section 8 of /stocks, the last step of the fallback chain. When no provider works and nothing
// is cached, upload a CSV of daily prices (POST /{symbol}/prices/upload). The file is checked here
// (.csv, at most 1 MB) and again by the backend, which only ADDS missing days: cached provider bars are
// never overwritten, and uploaded rows are visible to every user (prices are shared public data).

"use client";

import { Upload } from "lucide-react";
import { useRef, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatTile } from "@/components/stat-tile";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { warningText } from "@/lib/stocks";
import type { PriceUploadResponse } from "@/lib/types";

const MAX_UPLOAD_BYTES = 1_000_000; // same as the backend (413 above it)

export function StockCsvUpload({
  symbol,
  track,
  onUploaded,
}: {
  symbol: string;
  track: ApiLog["track"];
  onUploaded: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<PriceUploadResponse | null>(null);

  function choose(candidate: File | undefined) {
    setError(null);
    setResult(null);
    if (!candidate) {
      setFile(null);
      return;
    }
    if (!candidate.name.toLowerCase().endsWith(".csv")) {
      setError(new Error(`"${candidate.name}" is not a .csv file.`));
      setFile(null);
    } else if (candidate.size > MAX_UPLOAD_BYTES) {
      setError(new Error(`"${candidate.name}" is larger than 1 MB.`));
      setFile(null);
    } else {
      setFile(candidate);
    }
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await track(api.uploadStockPrices(symbol, file));
      setResult(data);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onUploaded();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>8. Upload prices (CSV fallback)</CardTitle>
        <CardDescription>
          Only when no provider works. Columns <code>date, open, high, low, close</code> (<code>volume</code> optional);
          dates like 2026-10-30 or 30-10-2026; at most 1 MB and 20,000 rows. Days already cached are kept.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="price-csv">CSV file for {symbol}</Label>
            <Input
              id="price-csv"
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => choose(event.target.files?.[0])}
              disabled={busy}
              className="max-w-xs"
            />
          </div>
          <Button onClick={upload} disabled={!file || busy} className="gap-2">
            <Upload className="size-4" />
            {busy ? "Uploading…" : "Upload"}
          </Button>
        </div>
        <ApiErrorAlert error={error} />
        {result && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Rows read" value={result.rows_read} />
              <StatTile label="Bars saved" value={result.bars_saved} tone={result.bars_saved > 0 ? "good" : "muted"} />
              <StatTile label="Rows skipped" value={result.rows_rejected} tone={result.rows_rejected ? "warn" : "muted"} />
              <StatTile label="Data now to" value={result.data_as_of ? formatDay(result.data_as_of, true) : "—"} />
            </div>
            {result.warnings.length > 0 && (
              <ul className="list-disc pl-5 text-sm text-muted-foreground">
                {result.warnings.map((code) => (
                  <li key={code}>{warningText(code)}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
