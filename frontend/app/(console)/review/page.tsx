// frontend/app/(console)/review/page.tsx
//
// Purpose: /review — the queue of transactions the categorizer was unsure about
// (GET /api/transactions/?status=needs_review, least confident first), plus rows
// the person-payee guard kept away from the LLM. Fix them one by one (pencil) or
// select several and set one category for all (POST /api/transactions/bulk-category).
// Every fixed row is locked and leaves the queue; your corrections also become
// "memory" that helps the categorizer next time. Synthetic data only.
//
// Bulk rule: rows the chosen category does not fit (e.g. "Salary" on a debit)
// are SKIPPED by the backend, not rejected, and listed in the result.

"use client";

import { CheckCheck } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { CategorySelect } from "@/components/category-select";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { RunCategorizerButton } from "@/components/run-categorizer-button";
import { TransactionTable } from "@/components/transaction-table";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import type { BulkCategoryResponse } from "@/lib/types";

const PAGE_SIZE = 50;

export default function ReviewPage() {
  const log = useApiLog();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const loadQueue = useCallback(
    () => api.listTransactions({ status: "needs_review", limit: PAGE_SIZE, offset }),
    [offset],
  );
  const queue = useApiData(loadQueue, log.track);
  const loadCategories = useCallback(() => api.getCategories(), []);
  const categories = useApiData(loadCategories, log.track);

  // Debit labels, then the credit-only ones (a label in both lists, like "Transfers", appears once)
  const groups = useMemo(() => {
    const debit = categories.data?.debit ?? [];
    const creditOnly = (categories.data?.credit ?? []).filter((label) => !debit.includes(label));
    return [
      { label: "Money out (debits)", options: debit },
      { label: "Money in (credits)", options: creditOnly },
    ];
  }, [categories.data]);

  // --- bulk ---
  const [bulkCategory, setBulkCategory] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<unknown>(null);
  const [bulkResult, setBulkResult] = useState<BulkCategoryResponse | null>(null);

  const { reload } = queue;
  const refresh = useCallback(() => {
    setSelected(new Set()); // fixed rows leave the queue, so the old selection no longer applies
    reload();
  }, [reload]);

  async function applyBulk() {
    setBulkBusy(true);
    setBulkError(null);
    setBulkResult(null);
    try {
      const { data } = await log.track(api.bulkCategory([...selected], bulkCategory));
      setBulkResult(data);
      refresh();
    } catch (err) {
      setBulkError(err);
    } finally {
      setBulkBusy(false);
    }
  }

  function changePage(next: number) {
    setSelected(new Set()); // never act on rows the user cannot see
    setOffset(next);
  }

  const total = queue.data?.total ?? 0;

  return (
    <>
      <PageHeader
        title="Review queue"
        description="Rows the categorizer was not sure about, least confident first. Fixing a row locks it and teaches the agent (memory)."
      />

      <Card>
        <CardHeader>
          <CardTitle>Bulk fix</CardTitle>
          <CardDescription>
            Tick rows in the table below, choose one category and apply it to all of them. Rows the category does not
            fit (e.g. “Salary” on money going out) are skipped and listed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm tabular-nums">{selected.size} selected</span>
            <CategorySelect
              groups={groups}
              value={bulkCategory}
              onChange={setBulkCategory}
              ariaLabel="Category for the selected rows"
              disabled={!categories.data || bulkBusy}
            />
            <Button
              className="gap-2"
              onClick={applyBulk}
              disabled={bulkBusy || selected.size === 0 || !bulkCategory}
            >
              <CheckCheck className="size-4" />
              {bulkBusy ? "Applying…" : `Apply to ${selected.size} row${selected.size === 1 ? "" : "s"}`}
            </Button>
            {selected.size > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())} disabled={bulkBusy}>
                Clear selection
              </Button>
            )}
          </div>
          <ApiErrorAlert error={bulkError} />
          {bulkResult && (
            <Alert>
              <AlertDescription>
                Updated {bulkResult.updated} row{bulkResult.updated === 1 ? "" : "s"}.
                {bulkResult.skipped_incompatible_ids.length > 0 &&
                  ` Skipped ${bulkResult.skipped_incompatible_ids.length} that the category does not fit (ids ${bulkResult.skipped_incompatible_ids.join(", ")}).`}
                {bulkResult.not_found_ids.length > 0 && ` Not found: ${bulkResult.not_found_ids.join(", ")}.`}
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Needs review{queue.data ? ` (${total})` : ""}</CardTitle>
          <CardDescription>
            “review” rows: low confidence, or a person-to-person payment the privacy guard kept away from the LLM.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ApiErrorAlert error={queue.error} />
          <ApiErrorAlert error={categories.error} />
          {queue.data ? (
            queue.data.items.length === 0 && offset > 0 ? (
              // fixing the last page's rows emptied it, but earlier pages still have rows
              <Button variant="outline" size="sm" onClick={() => changePage(0)}>
                This page is empty now: back to the first page
              </Button>
            ) : queue.data.items.length === 0 ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  {queue.loading ? "Loading…" : "Nothing to review. Upload a statement and categorize it to fill the queue."}
                </p>
                <RunCategorizerButton track={log.track} onDone={refresh} />
              </div>
            ) : (
              <div className={`space-y-3 ${queue.loading ? "opacity-60" : ""}`}>
                <TransactionTable
                  transactions={queue.data.items}
                  categories={categories.data}
                  track={log.track}
                  // a fixed row leaves the queue, so any change refetches it
                  onRowUpdated={refresh}
                  onManyChanged={refresh}
                  selection={{ selected, onChange: setSelected }}
                />
                <Pager total={total} offset={offset} limit={PAGE_SIZE} onOffsetChange={changePage} disabled={queue.loading} />
              </div>
            )
          ) : (
            <p className="text-sm text-muted-foreground">{queue.loading ? "Loading…" : "Could not load the queue."}</p>
          )}
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
