// frontend/components/month-picker.tsx
//
// Purpose: the "YYYY-MM" month input used by /budgets and /summary. A cleared or
// half-typed value is ignored, so a page never sends a bad month to the API
// (the backend would answer 422).

"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const MONTH_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/; // same rule as the backend

export function MonthPicker({
  id,
  value,
  onChange,
  label = "Month",
}: {
  id: string;
  value: string;
  onChange: (month: string) => void;
  label?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <Label htmlFor={id} className="text-sm">
        {label}
      </Label>
      <Input
        id={id}
        type="month"
        className="w-44"
        value={value}
        onChange={(event) => MONTH_PATTERN.test(event.target.value) && onChange(event.target.value)}
      />
    </div>
  );
}
