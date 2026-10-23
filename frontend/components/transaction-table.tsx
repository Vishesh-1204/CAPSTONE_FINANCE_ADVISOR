// frontend/components/transaction-table.tsx
//
// Purpose: the transactions table shared by /transactions and /review. Per row it
// shows date, description (payee key below), signed amount, type, category (with
// the agent's original label if you overrode it), who decided it (source badge),
// how sure it was (confidence bar), the reasoning (hover the ⓘ) and a lock.
//
// Actions:
//   * Edit (pencil): an editor row opens under the transaction with a category
//     dropdown (only labels that fit the row's direction) and two checkboxes:
//       - "apply to similar": also relabel your UNLOCKED rows of the same payee
//       - "remember this payee": create a payee rule, so future imports of this
//         payee are categorized without the LLM
//     Save = PATCH /api/transactions/{id}/category. The row becomes locked
//     (source "user"), so the agent never overwrites your choice.
//   * Unlock: POST /api/transactions/{id}/unlock (the agent may redo it later).
//   * Optional selection checkboxes (the review page's bulk action).
//
// Display + row actions only: the page owns the list and refetches when
// several rows may have changed (onManyChanged).

"use client";

import { Info, Lock, LockOpen, Pencil } from "lucide-react";
import { Fragment, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { CategorySelect } from "@/components/category-select";
import { ConfidenceBar, SourceBadge, TxnTypeBadge } from "@/components/transaction-badges";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";
import type { CategoryOptions, Transaction } from "@/lib/types";

export interface Selection {
  selected: ReadonlySet<number>;
  onChange: (next: Set<number>) => void;
}

function AmountCell({ tx }: { tx: Transaction }) {
  const credit = tx.direction === "credit";
  return (
    <span className={`tabular-nums ${credit ? "text-emerald-700 dark:text-emerald-400" : ""}`}>
      {credit ? "+" : "−"}
      {formatMoney(tx.amount)}
    </span>
  );
}

function CategoryCell({ tx }: { tx: Transaction }) {
  const overridden = tx.category_source === "user" && tx.agent_category && tx.agent_category !== tx.category;
  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-1.5">
        {tx.category ? <span>{tx.category}</span> : <span className="text-muted-foreground">uncategorized</span>}
        {tx.category_reasoning && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Info className="size-3.5 cursor-help text-muted-foreground" aria-label="Why this category" />
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">{tx.category_reasoning}</TooltipContent>
          </Tooltip>
        )}
        {tx.needs_review && (
          <Badge variant="outline" className="border-amber-500/50 text-amber-700 dark:text-amber-400">
            review
          </Badge>
        )}
      </div>
      {overridden && <div className="text-xs text-muted-foreground">agent said: {tx.agent_category}</div>}
    </div>
  );
}

export function TransactionTable({
  transactions,
  categories,
  track,
  onRowUpdated,
  onManyChanged,
  selection,
}: {
  transactions: Transaction[];
  categories: CategoryOptions | null;
  track: ApiLog["track"];
  onRowUpdated: (tx: Transaction) => void; // replace one row locally
  onManyChanged: () => void; // other rows may have changed: refetch
  selection?: Selection;
}) {
  // Only one row is edited at a time
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [applySimilar, setApplySimilar] = useState(false);
  const [rememberPayee, setRememberPayee] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const columns = selection ? 10 : 9;
  const pageIds = transactions.map((tx) => tx.id);
  const selectedOnPage = selection ? pageIds.filter((id) => selection.selected.has(id)).length : 0;

  function startEdit(tx: Transaction) {
    setEditingId(tx.id);
    setDraft(tx.category ?? "");
    setApplySimilar(false);
    setRememberPayee(false);
    setError(null);
    setNotice(null);
  }

  async function save(tx: Transaction) {
    if (!draft) {
      setError(new Error("Choose a category first."));
      return;
    }
    setBusyId(tx.id);
    setError(null);
    try {
      const { data } = await track(
        api.correctCategory(tx.id, { category: draft, apply_to_similar: applySimilar, remember_payee: rememberPayee }),
      );
      const parts = [`Saved “${data.transaction.category}” (locked).`];
      if (data.similar_updated) parts.push(`Also updated ${data.similar_updated} similar row(s).`);
      if (data.payee_rule) parts.push(`Payee rule: ${data.payee_rule.match_value} → ${data.payee_rule.category}.`);
      setNotice(parts.join(" "));
      setEditingId(null);
      if (data.similar_updated || data.payee_rule) onManyChanged();
      else onRowUpdated(data.transaction);
    } catch (err) {
      setError(err);
    } finally {
      setBusyId(null);
    }
  }

  async function unlock(tx: Transaction) {
    setBusyId(tx.id);
    setError(null);
    setNotice(null);
    try {
      const { data } = await track(api.unlockTransaction(tx.id));
      onRowUpdated(data);
      setNotice("Unlocked: the category stays, but the agent may redo it on “Re-run, replacing AI results”.");
    } catch (err) {
      setError(err);
    } finally {
      setBusyId(null);
    }
  }

  function toggle(id: number, checked: boolean) {
    if (!selection) return;
    const next = new Set(selection.selected);
    if (checked) next.add(id);
    else next.delete(id);
    selection.onChange(next);
  }

  function toggleAll(checked: boolean) {
    if (!selection) return;
    const next = new Set(selection.selected);
    for (const id of pageIds) {
      if (checked) next.add(id);
      else next.delete(id);
    }
    selection.onChange(next);
  }

  return (
    <div className="space-y-3">
      <ApiErrorAlert error={error} />
      {notice && (
        <Alert>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            {selection && (
              <TableHead className="w-8">
                <Checkbox
                  aria-label="Select all on this page"
                  checked={
                    selectedOnPage === 0 ? false : selectedOnPage === pageIds.length ? true : "indeterminate"
                  }
                  onCheckedChange={(checked) => toggleAll(checked === true)}
                />
              </TableHead>
            )}
            <TableHead>Date</TableHead>
            <TableHead>Description</TableHead>
            <TableHead className="text-right">Amount</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Confidence</TableHead>
            <TableHead className="w-8">
              <span className="sr-only">Lock</span>
            </TableHead>
            <TableHead className="w-10">
              <span className="sr-only">Edit</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {transactions.map((tx) => {
            const editing = editingId === tx.id;
            const busy = busyId === tx.id;
            return (
              <Fragment key={tx.id}>
                <TableRow data-state={selection?.selected.has(tx.id) ? "selected" : undefined}>
                  {selection && (
                    <TableCell>
                      <Checkbox
                        aria-label={`Select transaction ${tx.id}`}
                        checked={selection.selected.has(tx.id)}
                        onCheckedChange={(checked) => toggle(tx.id, checked === true)}
                      />
                    </TableCell>
                  )}
                  <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(tx.transaction_date)}</TableCell>
                  <TableCell className="max-w-72">
                    <div className="truncate font-medium" title={tx.description}>
                      {tx.description}
                    </div>
                    {tx.payee_key && (
                      <div className="truncate font-mono text-xs text-muted-foreground" title={tx.payee_key}>
                        {tx.payee_key}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap">
                    <AmountCell tx={tx} />
                  </TableCell>
                  <TableCell>
                    <TxnTypeBadge type={tx.txn_type} />
                  </TableCell>
                  <TableCell>
                    <CategoryCell tx={tx} />
                  </TableCell>
                  <TableCell>
                    <SourceBadge source={tx.category_source} />
                  </TableCell>
                  <TableCell>
                    <ConfidenceBar confidence={tx.category_confidence} />
                  </TableCell>
                  <TableCell>
                    {tx.user_locked ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            size="icon-sm"
                            variant="ghost"
                            aria-label="Unlock this transaction"
                            onClick={() => unlock(tx)}
                            disabled={busyId !== null}
                          >
                            <Lock />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Locked by you: the agent never changes it. Click to unlock.</TooltipContent>
                      </Tooltip>
                    ) : (
                      <LockOpen className="mx-auto size-3.5 text-muted-foreground/40" aria-label="Not locked" />
                    )}
                  </TableCell>
                  <TableCell>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Edit category of transaction ${tx.id}`}
                      onClick={() => (editing ? setEditingId(null) : startEdit(tx))}
                      disabled={busyId !== null}
                    >
                      <Pencil />
                    </Button>
                  </TableCell>
                </TableRow>
                {editing && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell colSpan={columns}>
                      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 py-1">
                        <CategorySelect
                          groups={[{ label: tx.direction, options: categories?.[tx.direction] ?? [] }]}
                          value={draft}
                          onChange={setDraft}
                          ariaLabel="New category"
                          disabled={busy || !categories}
                        />
                        <div className="flex items-center gap-2">
                          <Checkbox
                            id={`similar-${tx.id}`}
                            checked={applySimilar}
                            onCheckedChange={(checked) => setApplySimilar(checked === true)}
                            disabled={busy}
                          />
                          <Label htmlFor={`similar-${tx.id}`} className="font-normal">
                            Apply to similar (unlocked rows of this payee)
                          </Label>
                        </div>
                        <div className="flex items-center gap-2">
                          <Checkbox
                            id={`remember-${tx.id}`}
                            checked={rememberPayee}
                            onCheckedChange={(checked) => setRememberPayee(checked === true)}
                            disabled={busy}
                          />
                          <Label htmlFor={`remember-${tx.id}`} className="font-normal">
                            Remember this payee (future imports)
                          </Label>
                        </div>
                        <div className="flex gap-1">
                          <Button size="sm" onClick={() => save(tx)} disabled={busy || !draft}>
                            {busy ? "Saving…" : "Save"}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setEditingId(null)} disabled={busy}>
                            Cancel
                          </Button>
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
