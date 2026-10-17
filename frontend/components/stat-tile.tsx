// frontend/components/stat-tile.tsx
//
// Purpose: one labelled number ("Imported 24"). Used in grids by the import
// summary, the categorizer result and (F5) the monthly summary, so all three
// look the same. `tone` colours the value for good/bad outcomes.

import type { ReactNode } from "react";

const TONES = {
  default: "",
  good: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  bad: "text-destructive",
  muted: "text-muted-foreground",
};

export function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: keyof typeof TONES;
}) {
  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${TONES[tone]}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{children}</div>;
}
