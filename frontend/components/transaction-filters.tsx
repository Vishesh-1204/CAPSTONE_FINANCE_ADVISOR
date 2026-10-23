// frontend/components/transaction-filters.tsx
//
// Purpose: the filter bar of /transactions. Every filter maps 1:1 to a query
// parameter of GET /api/transactions/ (status, txn_type, category, date_from,
// date_to). "" means "no filter": lib/api.ts drops empty values, so the backend
// uses its defaults. Changing a filter goes back to page 1 (the page does that).

"use client";

import { X } from "lucide-react";

import { ANY, CategorySelect } from "@/components/category-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { humanize } from "@/lib/format";
import type { CategoryOptions, StatusFilter, TxnType } from "@/lib/types";

export interface TransactionFilterValues {
  status: StatusFilter | "";
  txn_type: TxnType | "";
  category: string;
  date_from: string; // YYYY-MM-DD
  date_to: string; // YYYY-MM-DD (inclusive)
}

export const NO_FILTERS: TransactionFilterValues = { status: "", txn_type: "", category: "", date_from: "", date_to: "" };

const STATUSES: { value: StatusFilter; label: string }[] = [
  { value: "needs_review", label: "Needs review" },
  { value: "uncategorized", label: "Uncategorized" },
  { value: "categorized", label: "Categorized" },
  { value: "locked", label: "Locked (set by you)" },
];
const TXN_TYPES: TxnType[] = ["expense", "income", "investment_out", "investment_in", "transfer"];

/** A plain Select with an "any" option (Radix does not allow "" as an item value). */
function FilterSelect<T extends string>({
  id,
  value,
  onChange,
  anyLabel,
  options,
}: {
  id: string;
  value: T | "";
  onChange: (value: T | "") => void;
  anyLabel: string;
  options: { value: T; label: string }[];
}) {
  return (
    <Select value={value || ANY} onValueChange={(next) => onChange(next === ANY ? "" : (next as T))}>
      <SelectTrigger id={id} className="w-44">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>{anyLabel}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function TransactionFilters({
  values,
  categories,
  onChange,
}: {
  values: TransactionFilterValues;
  categories: CategoryOptions | null;
  onChange: (next: TransactionFilterValues) => void;
}) {
  // One list of every label (a label like "Transfers" exists for both directions: show it once)
  const allCategories = [...new Set([...(categories?.debit ?? []), ...(categories?.credit ?? [])])].sort();
  const active = Object.values(values).some((value) => value !== "");

  function set<K extends keyof TransactionFilterValues>(key: K, value: TransactionFilterValues[K]) {
    onChange({ ...values, [key]: value });
  }

  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="space-y-1.5">
        <Label htmlFor="filter-status">Status</Label>
        <FilterSelect
          id="filter-status"
          value={values.status}
          onChange={(value) => set("status", value)}
          anyLabel="Any status"
          options={STATUSES}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="filter-type">Type</Label>
        <FilterSelect
          id="filter-type"
          value={values.txn_type}
          onChange={(value) => set("txn_type", value)}
          anyLabel="Any type"
          options={TXN_TYPES.map((type) => ({ value: type, label: humanize(type) }))}
        />
      </div>
      <div className="space-y-1.5">
        <Label>Category</Label>
        <CategorySelect
          groups={[{ label: "all", options: allCategories }]}
          value={values.category || ANY}
          onChange={(value) => set("category", value === ANY ? "" : value)}
          anyLabel="Any category"
          ariaLabel="Category filter"
          className="w-48"
          disabled={!categories}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="filter-from">From</Label>
        <Input
          id="filter-from"
          type="date"
          className="w-40"
          value={values.date_from}
          onChange={(event) => set("date_from", event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="filter-to">To (inclusive)</Label>
        <Input
          id="filter-to"
          type="date"
          className="w-40"
          value={values.date_to}
          onChange={(event) => set("date_to", event.target.value)}
        />
      </div>
      {active && (
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => onChange(NO_FILTERS)}>
          <X className="size-3.5" /> Clear filters
        </Button>
      )}
    </div>
  );
}
