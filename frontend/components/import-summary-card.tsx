// frontend/components/import-summary-card.tsx
//
// Purpose: shows what ONE statement upload did (StatementImportSummary):
// row counts, how many of each transaction type, and the running-balance check.
// The balance check is a parsing-quality signal: a mismatch usually means a
// misread amount or a skipped row, so it is shown prominently.

import { CircleAlert, CircleCheck, CircleMinus } from "lucide-react";

import { StatGrid, StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { humanize } from "@/lib/format";
import type { StatementImportSummary } from "@/lib/types";

function BalanceCheck({ summary }: { summary: StatementImportSummary }) {
  if (!summary.balance_checked) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <CircleMinus className="size-4" /> Balance not checked: this file has no running-balance column.
      </p>
    );
  }
  if (summary.balance_mismatches === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
        <CircleCheck className="size-4" /> Balance check passed: every running balance adds up.
      </p>
    );
  }
  const shown = summary.balance_mismatch_rows.join(", ");
  const more = summary.balance_mismatches - summary.balance_mismatch_rows.length;
  return (
    <div className="space-y-1 text-sm">
      <p className="flex items-center gap-2 font-medium text-destructive">
        <CircleAlert className="size-4" />
        Balance check: {summary.balance_mismatches} row{summary.balance_mismatches === 1 ? "" : "s"} did not add up
      </p>
      <p className="text-muted-foreground">
        Source row{summary.balance_mismatch_rows.length === 1 ? "" : "s"} {shown}
        {more > 0 ? ` and ${more} more` : ""}. Previous balance − debit + credit ≠ printed balance. Usually a
        misread amount or a skipped row; the rows were still imported.
      </p>
    </div>
  );
}

export function ImportSummaryCard({ summary }: { summary: StatementImportSummary }) {
  const types = Object.entries(summary.by_txn_type);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {summary.filename}
          <Badge variant="secondary">{summary.source_type.toUpperCase()}</Badge>
          <Badge variant="outline">layout: {summary.layout}</Badge>
        </CardTitle>
        <CardDescription>Import batch #{summary.import_batch_id}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <StatGrid>
          <StatTile label="Rows found" value={summary.rows_total} />
          <StatTile label="Imported" value={summary.rows_imported} tone={summary.rows_imported ? "good" : "muted"} />
          <StatTile
            label="Duplicates skipped"
            value={summary.rows_duplicate}
            tone={summary.rows_duplicate ? "warn" : "muted"}
            hint="already in your account"
          />
          <StatTile
            label="Invalid rows"
            value={summary.rows_invalid}
            tone={summary.rows_invalid ? "bad" : "muted"}
            hint="bad date / amount / empty"
          />
        </StatGrid>

        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">Imported rows by transaction type</div>
          {types.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing new was imported.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {types.map(([type, count]) => (
                <Badge key={type} variant="outline">
                  {humanize(type)}: {count}
                </Badge>
              ))}
            </div>
          )}
        </div>

        <BalanceCheck summary={summary} />

        <p className="text-sm text-muted-foreground">
          Rows are saved <strong>uncategorized</strong>. Next step: run the categorizer below.
        </p>
      </CardContent>
    </Card>
  );
}
