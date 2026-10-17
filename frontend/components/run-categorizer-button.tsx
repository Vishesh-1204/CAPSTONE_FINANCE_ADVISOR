// frontend/components/run-categorizer-button.tsx
//
// Purpose: runs the Expense Intelligence Agent (POST /api/transactions/categorize)
// and shows its CategorizationSummary: how many rows each layer decided
// (payee rules, memory, rules, LLM), how many the privacy guard kept away from
// the LLM, how many need review, and how many failed (left uncategorized, retry later).
// Used on /upload, /transactions and /review; `onDone` lets the page refresh.

"use client";

import { Sparkles } from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import type { CategorizationSummary } from "@/lib/types";

export function RunCategorizerButton({ track, onDone }: { track: ApiLog["track"]; onDone?: () => void }) {
  const [resetAi, setResetAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CategorizationSummary | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const { data } = await track(api.categorize(resetAi));
      setResult(data);
      onDone?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4">
        <Button onClick={run} disabled={busy} className="gap-2">
          <Sparkles className="size-4" />
          {busy ? "Categorizing…" : "Run categorizer"}
        </Button>
        <div className="flex items-center gap-2">
          <Checkbox
            id="reset-ai"
            checked={resetAi}
            onCheckedChange={(checked) => setResetAi(checked === true)}
            disabled={busy}
          />
          <Label htmlFor="reset-ai" className="text-sm font-normal">
            Redo AI results (your locked rows are kept)
          </Label>
        </div>
      </div>
      {busy && (
        <p className="text-sm text-muted-foreground">
          This can take a minute: LLM batches are rate-limited (a few seconds between requests).
        </p>
      )}

      <ApiErrorAlert error={error} />

      {result && (
        <div className="space-y-3">
          <StatGrid>
            <StatTile label="Processed" value={result.total_processed} />
            <StatTile label="By payee rules" value={result.categorized_by_payee_rules} />
            <StatTile label="By memory" value={result.categorized_by_memory} hint="your past corrections" />
            <StatTile label="By rules" value={result.categorized_by_rules} hint="keywords + dictionary" />
            <StatTile label="By LLM" value={result.categorized_by_llm} />
            <StatTile
              label="Person-guarded"
              value={result.person_payees_guarded}
              hint="never sent to the LLM"
            />
            <StatTile label="Needs review" value={result.needs_review} tone={result.needs_review ? "warn" : "muted"} />
            <StatTile
              label="Failed"
              value={result.failed}
              tone={result.failed ? "bad" : "muted"}
              hint="left uncategorized"
            />
          </StatGrid>
          {result.message && (
            <Alert>
              <AlertDescription>{result.message}</AlertDescription>
            </Alert>
          )}
          {result.total_processed === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing to do: every transaction already has a category. Tick “Redo AI results” to run again.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
