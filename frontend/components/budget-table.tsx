// frontend/components/budget-table.tsx
//
// Purpose: the month's category limits (GET /api/budgets/?month=) with the
// planner's reasoning and a source badge: "agent" = the planner's number,
// "user" = your override. You can edit a limit in place (PUT /api/budgets/{id},
// which marks it "user" so regeneration keeps it) or reset it
// (POST /api/budgets/{id}/reset, back to the planner's original number).
// Overrides are a research signal too: the override rate and size measure how
// far users disagree with the agent (spec section 7).

"use client";

import { Pencil, RotateCcw } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { BudgetItem } from "@/lib/types";

// Same rule as the backend schema (BudgetUpdate): >= 0, at most 2 decimals
const LIMIT_PATTERN = /^\d+(\.\d{1,2})?$/;

export function BudgetTable({
  budgets,
  loading,
  spendable,
  track,
  onChanged,
}: {
  budgets: BudgetItem[] | null;
  loading: boolean;
  spendable: number | null; // from the profile; null when no profile yet
  track: ApiLog["track"];
  onChanged: () => void; // the page refetches the table and the status
}) {
  // Only one row is edited at a time, so one id + one draft value is enough
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  if (!budgets) return <p className="text-sm text-muted-foreground">{loading ? "Loading…" : "Could not load limits."}</p>;
  if (budgets.length === 0) {
    return <p className="text-sm text-muted-foreground">No budget for this month yet. Press “Generate budget”.</p>;
  }

  function startEdit(budget: BudgetItem) {
    setEditingId(budget.id);
    setDraft(String(budget.limit_amount));
    setError(null);
  }

  /** Runs one row action (save / reset) with a per-row busy state and shared error box. */
  async function runRowAction(id: number, action: () => Promise<unknown>) {
    setBusyId(id);
    setError(null);
    try {
      await action();
      setEditingId(null);
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusyId(null);
    }
  }

  function save(budget: BudgetItem) {
    const value = draft.trim();
    if (!LIMIT_PATTERN.test(value)) {
      setError(new Error("A limit must be a number of 0 or more with at most 2 decimals (e.g. 4500)."));
      return;
    }
    void runRowAction(budget.id, () => track(api.updateBudget(budget.id, Number(value))));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>, budget: BudgetItem) {
    if (event.key === "Enter") save(budget);
    if (event.key === "Escape") setEditingId(null);
  }

  const total = budgets.reduce((sum, budget) => sum + budget.limit_amount, 0);
  const over = spendable != null ? total - spendable : 0;

  return (
    <div className="space-y-3">
      <ApiErrorAlert error={error} />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category</TableHead>
            <TableHead className="text-right">Limit</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Reasoning</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {budgets.map((budget) => {
            const editing = editingId === budget.id;
            const busy = busyId === budget.id;
            const overridden = budget.source === "user";
            return (
              <TableRow key={budget.id}>
                <TableCell className="font-medium">{budget.category}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {editing ? (
                    <Input
                      autoFocus
                      aria-label={`New limit for ${budget.category}`}
                      inputMode="decimal"
                      className="ml-auto h-8 w-32 text-right"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => onKeyDown(event, budget)}
                      disabled={busy}
                    />
                  ) : (
                    <div>
                      <div>{formatMoney(budget.limit_amount)}</div>
                      {overridden && budget.agent_limit_amount !== budget.limit_amount && (
                        <div className="text-xs text-muted-foreground">
                          planner: {formatMoney(budget.agent_limit_amount)}
                        </div>
                      )}
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={overridden ? "default" : "secondary"}>{budget.source}</Badge>
                </TableCell>
                <TableCell className="max-w-md">
                  {budget.reasoning ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="block cursor-help truncate text-muted-foreground">{budget.reasoning}</span>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-sm">{budget.reasoning}</TooltipContent>
                    </Tooltip>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right whitespace-nowrap">
                  {editing ? (
                    <div className="flex justify-end gap-1">
                      <Button size="sm" onClick={() => save(budget)} disabled={busy}>
                        {busy ? "Saving…" : "Save"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingId(null)} disabled={busy}>
                        Cancel
                      </Button>
                    </div>
                  ) : (
                    <div className="flex justify-end gap-1">
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Edit limit for ${budget.category}`}
                        onClick={() => startEdit(budget)}
                        disabled={busyId !== null}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Reset ${budget.category} to the planner's limit`}
                        title="Reset to the planner's limit"
                        onClick={() => runRowAction(budget.id, () => track(api.resetBudget(budget.id)))}
                        disabled={!overridden || busyId !== null}
                      >
                        <RotateCcw />
                      </Button>
                    </div>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell>Total</TableCell>
            <TableCell className="text-right tabular-nums">{formatMoney(total)}</TableCell>
            <TableCell colSpan={3} className="font-normal text-muted-foreground">
              {spendable == null
                ? "No profile, so there is no spendable income to compare with."
                : over > 0.005
                  ? `Your limits are ${formatMoney(over)} above the spendable income of ${formatMoney(spendable)}.`
                  : `of ${formatMoney(spendable)} spendable income.`}
            </TableCell>
          </TableRow>
        </TableFooter>
      </Table>
    </div>
  );
}
