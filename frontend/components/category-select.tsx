// frontend/components/category-select.tsx
//
// Purpose: one dropdown for choosing a category label, used by the inline editor,
// the transaction filters and the review page's bulk action. The allowed labels
// come from GET /api/categories/ (debit and credit lists), never hard-coded, so
// the frontend can never offer a label the backend would reject.
//
// Radix Select does not allow an item whose value is "" (that value means
// "nothing selected"), so an "any" option uses the ANY sentinel instead.

"use client";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const ANY = "__any__";

export interface CategoryGroup {
  label: string;
  options: string[];
}

export function CategorySelect({
  groups,
  value,
  onChange,
  placeholder = "Choose a category",
  anyLabel,
  ariaLabel,
  className = "w-56",
  disabled,
}: {
  groups: CategoryGroup[];
  value: string; // "" = nothing chosen yet (shows the placeholder)
  onChange: (value: string) => void; // receives ANY when the "any" option is picked
  placeholder?: string;
  anyLabel?: string; // when set, an extra first option meaning "no filter"
  ariaLabel?: string;
  className?: string;
  disabled?: boolean;
}) {
  const shown = groups.filter((group) => group.options.length > 0);
  return (
    <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger className={className} aria-label={ariaLabel}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {anyLabel && <SelectItem value={ANY}>{anyLabel}</SelectItem>}
        {shown.map((group, index) => (
          <SelectGroup key={group.label}>
            {(index > 0 || anyLabel) && <SelectSeparator />}
            {shown.length > 1 && <SelectLabel>{group.label}</SelectLabel>}
            {group.options.map((option) => (
              <SelectItem key={`${group.label}-${option}`} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}
