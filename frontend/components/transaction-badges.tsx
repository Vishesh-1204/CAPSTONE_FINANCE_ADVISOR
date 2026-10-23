// frontend/components/transaction-badges.tsx
//
// Purpose: the small visual signals of one transaction, shared by /transactions
// and /review so both read the same way:
//   SourceBadge    WHO decided the category: rule, memory (your past
//                  corrections), llm, user (you), payee_rule (a rule you created)
//   TxnTypeBadge   WHAT kind of money movement it is (expense, income,
//                  investment out/in, own-account transfer). Only "expense" counts
//                  as spending in budgets and forecasts.
//   ConfidenceBar  how sure the categorizer was (0-100 %); low = goes to review

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { humanize } from "@/lib/format";
import type { TxnType } from "@/lib/types";

const SOURCE_STYLES: Record<string, { label: string; className: string }> = {
  rule: { label: "rule", className: "bg-secondary text-secondary-foreground" },
  memory: { label: "memory", className: "bg-sky-600/10 text-sky-700 dark:text-sky-400" },
  llm: { label: "llm", className: "bg-violet-600/10 text-violet-700 dark:text-violet-400" },
  user: { label: "user", className: "bg-primary text-primary-foreground" },
  payee_rule: { label: "payee rule", className: "bg-emerald-600/10 text-emerald-700 dark:text-emerald-400" },
};

export function SourceBadge({ source }: { source: string | null }) {
  if (!source) return <span className="text-muted-foreground">—</span>;
  const style = SOURCE_STYLES[source] ?? { label: source, className: "" };
  return (
    <Badge variant="outline" className={`border-transparent ${style.className}`}>
      {style.label}
    </Badge>
  );
}

const TYPE_STYLES: Record<TxnType, string> = {
  expense: "",
  income: "text-emerald-700 dark:text-emerald-400",
  investment_out: "text-sky-700 dark:text-sky-400",
  investment_in: "text-sky-700 dark:text-sky-400",
  transfer: "text-muted-foreground",
};

export function TxnTypeBadge({ type }: { type: TxnType | null }) {
  if (!type) return <span className="text-muted-foreground">—</span>;
  return (
    <Badge variant="outline" className={TYPE_STYLES[type]}>
      {humanize(type)}
    </Badge>
  );
}

/** `confidence` is 0-1 from the backend (null = not categorized yet). */
export function ConfidenceBar({ confidence }: { confidence: number | null }) {
  if (confidence == null) return <span className="text-muted-foreground">—</span>;
  const percent = Math.round(confidence * 100);
  const colour =
    percent >= 80
      ? "[&_[data-slot=progress-indicator]]:bg-emerald-600"
      : percent >= 60
        ? "[&_[data-slot=progress-indicator]]:bg-amber-500"
        : "[&_[data-slot=progress-indicator]]:bg-destructive";
  return (
    <div className="flex items-center gap-2" title={`${percent}% confident`}>
      <Progress value={percent} className={`h-1.5 w-14 ${colour}`} />
      <span className="w-9 text-xs tabular-nums text-muted-foreground">{percent}%</span>
    </div>
  );
}
