// frontend/app/(console)/transactions/page.tsx
//
// Purpose: /transactions — browse everything that was imported, with filters
// (status, type, category, dates) and paging, see how each row was categorized
// (source badge, confidence bar, reasoning tooltip, lock) and correct a category
// inline ("apply to similar", "remember this payee"). Synthetic data only.
//
// The page owns the filters, the page offset and the loaded page; the table only
// reports changes back: one row changed -> swap it locally; several rows may have
// changed (apply to similar / a new payee rule) -> refetch the page.

"use client";

import { useCallback, useMemo, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { RunCategorizerButton } from "@/components/run-categorizer-button";
import { NO_FILTERS, TransactionFilters, type TransactionFilterValues } from "@/components/transaction-filters";
import { TransactionTable } from "@/components/transaction-table";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import type { Transaction, TransactionQuery } from "@/lib/types";

const PAGE_SIZE = 50;

export default function TransactionsPage() {
  const log = useApiLog();
  const [filters, setFilters] = useState<TransactionFilterValues>(NO_FILTERS);
  const [offset, setOffset] = useState(0);

  // One query object per (filters, offset): the loader below changes only when it changes
  const query = useMemo<TransactionQuery>(
    () => ({
      status: filters.status || undefined,
      txn_type: filters.txn_type || undefined,
      category: filters.category || undefined,
      date_from: filters.date_from || undefined,
      date_to: filters.date_to || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [filters, offset],
  );
  const loadPage = useCallback(() => api.listTransactions(query), [query]);
  const page = useApiData(loadPage, log.track);
  const loadCategories = useCallback(() => api.getCategories(), []);
  const categories = useApiData(loadCategories, log.track);

  function changeFilters(next: TransactionFilterValues) {
    setFilters(next);
    setOffset(0); // new filters start at page 1
  }

  const { setData, reload } = page;
  const replaceRow = useCallback(
    (updated: Transaction) =>
      setData((current) =>
        current ? { ...current, items: current.items.map((tx) => (tx.id === updated.id ? updated : tx)) } : current,
      ),
    [setData],
  );

  return (
    <>
      <PageHeader
        title="Transactions"
        description="Every imported row with how it was categorized. Correcting a category locks the row, so the agent never overwrites your choice."
      />

      <Card>
        <CardHeader>
          <CardTitle>Categorize</CardTitle>
          <CardDescription>
            Runs over every uncategorized, unlocked transaction (payee rules → memory → rules → person guard → LLM).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RunCategorizerButton track={log.track} onDone={reload} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Transactions</CardTitle>
          <CardDescription>
            Newest first. Hover ⓘ for the categorizer’s reasoning; the pencil edits a category; the lock means you set it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <TransactionFilters values={filters} categories={categories.data} onChange={changeFilters} />
          <ApiErrorAlert error={page.error} />
          <ApiErrorAlert error={categories.error} />
          {page.data ? (
            page.data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {page.loading ? "Loading…" : "No transactions match. Upload a statement or clear the filters."}
              </p>
            ) : (
              <div className={`space-y-3 ${page.loading ? "opacity-60" : ""}`}>
                <TransactionTable
                  transactions={page.data.items}
                  categories={categories.data}
                  track={log.track}
                  onRowUpdated={replaceRow}
                  onManyChanged={reload}
                />
                <Pager
                  total={page.data.total}
                  offset={offset}
                  limit={PAGE_SIZE}
                  onOffsetChange={setOffset}
                  disabled={page.loading}
                />
              </div>
            )
          ) : (
            <p className="text-sm text-muted-foreground">{page.loading ? "Loading…" : "Could not load transactions."}</p>
          )}
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
