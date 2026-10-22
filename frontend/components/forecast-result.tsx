// frontend/components/forecast-result.tsx
//
// Purpose: shows ONE saved forecast (ForecastRun from POST /api/forecast/generate
// or GET /api/forecast/latest):
//   * badges: as-of date, horizon, the chosen model (hover: every model's backtest
//     error, i.e. WHY it was chosen), months of history, template vs AI explanation
//   * a cash-crunch banner and a low-confidence notice when they apply
//   * cards: opening balance, income, fixed bills, everyday spending (with its
//     80% range), investments, net cash flow, end balance (with its range)
//   * the daily balance chart, the explanation, and Phase 4 budget warnings
// Display only: the page owns the data and the API calls.

import { CircleAlert, Info, TriangleAlert } from "lucide-react";

import { ForecastBalanceChart } from "@/components/forecast-balance-chart";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDay, formatMoney } from "@/lib/format";
import type { Band, ForecastRun, IncomeSource } from "@/lib/types";

// Same words as the backend explainer (agents/forecast_explainer.py MODEL_LABELS)
const MODEL_LABELS: Record<string, string> = {
  naive_last: "last month's spending",
  moving_average_3: "average of the last 3 months",
  exp_smoothing: "weighted average (recent months count more)",
  seasonal_naive_12: "same month last year",
};

const INCOME_HINTS: Record<IncomeSource, string> = {
  recurring: "from your regular credits",
  profile: "from your profile (no salary detected)",
  none: "no salary detected and no profile",
};

function range(band: Band): string {
  return `${formatMoney(band.lower)} – ${formatMoney(band.upper)}`;
}

/** Which text the user sees: the LLM's (it passed the guard) or the deterministic template. */
function ExplanationBadge({ run }: { run: ForecastRun }) {
  if (run.llm_used && run.explanation_faithful) return <Badge variant="secondary">explanation: AI · guard passed</Badge>;
  if (run.llm_used && run.explanation_faithful === false) {
    return <Badge variant="destructive">explanation: template (AI text rejected by guard)</Badge>;
  }
  return <Badge variant="outline">explanation: template</Badge>;
}

function ModelBadge({ run }: { run: ForecastRun }) {
  const badge = <Badge>model: {MODEL_LABELS[run.model_name] ?? run.model_name}</Badge>;
  if (run.model_scores.length === 0) return badge; // fallback model: nothing was compared
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help">{badge}</span>
      </TooltipTrigger>
      <TooltipContent>
        <div className="space-y-0.5 text-xs">
          <div className="font-medium">Backtest error on your past months (lower wins)</div>
          {run.model_scores.map((score) => (
            <div key={score.model_name} className="flex justify-between gap-4">
              <span>{MODEL_LABELS[score.model_name] ?? score.model_name}</span>
              <span className="tabular-nums">
                {formatMoney(score.mae)} · {score.folds} folds
              </span>
            </div>
          ))}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

export function ForecastResult({ run }: { run: ForecastRun }) {
  const endDate = run.trajectory.at(-1)?.date;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {run.horizon_days} days from {formatDay(run.as_of, true)}
          {endDate && ` (until ${formatDay(endDate, true)})`}
        </span>
        <ModelBadge run={run} />
        <Badge variant="outline">
          {run.history_months} month{run.history_months === 1 ? "" : "s"} of history
        </Badge>
        <ExplanationBadge run={run} />
      </div>

      {run.cash_crunch_date && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Cash crunch: your balance may go below ₹0 on {formatDay(run.cash_crunch_date, true)}</AlertTitle>
          <AlertDescription>
            This is the cautious estimate: the day the LOWER end of the range first goes below zero (if everyday
            spending is on the high side). Keep a buffer or move a payment.
          </AlertDescription>
        </Alert>
      )}

      {run.low_confidence && (
        <Alert className="border-amber-500/50 text-amber-800 dark:text-amber-300">
          <TriangleAlert />
          <AlertTitle>Low confidence</AlertTitle>
          <AlertDescription className="text-amber-800/90 dark:text-amber-300/90">
            Based on only {run.history_months} complete month{run.history_months === 1 ? "" : "s"} of history, too few to
            test the models against your past (or to measure their error), so the range may be a fixed ±25%. Import
            older statements for a better forecast.
          </AlertDescription>
        </Alert>
      )}

      <StatGrid>
        <StatTile
          label="Opening balance"
          value={formatMoney(run.opening_balance)}
          hint={
            run.opening_balance_source === "statement"
              ? `statement of ${run.opening_balance_date ? formatDay(run.opening_balance_date, true) : "?"}`
              : "typed by you"
          }
        />
        <StatTile
          label="Expected income"
          value={formatMoney(run.expected_income)}
          hint={INCOME_HINTS[run.income_source]}
          tone={run.income_source === "none" ? "warn" : "good"}
        />
        <StatTile label="Fixed bills" value={formatMoney(run.expected_fixed_expenses)} hint="recurring, on their dates" />
        <StatTile
          label="Everyday spending"
          value={formatMoney(run.expected_variable_expenses.point)}
          hint={`80% range ${range(run.expected_variable_expenses)}`}
        />
        <StatTile label="Investments" value={formatMoney(run.expected_investments)} hint="saved, not spent" />
        <StatTile
          label="Net cash flow"
          value={formatMoney(run.net_cashflow)}
          hint="income − bills − spending − investments"
          tone={run.net_cashflow < 0 ? "bad" : "good"}
        />
        <StatTile
          label="End balance"
          value={formatMoney(run.projected_end_balance.point)}
          hint={`80% range ${range(run.projected_end_balance)}`}
          tone={run.projected_end_balance.lower < 0 ? "bad" : "default"}
        />
      </StatGrid>

      <div className="space-y-2">
        <div className="text-xs text-muted-foreground">
          Projected daily balance. The shaded band is the 80% range; it widens because everyday spending is uncertain.
        </div>
        <ForecastBalanceChart trajectory={run.trajectory} cashCrunchDate={run.cash_crunch_date} />
      </div>

      <div className="space-y-1">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Info className="size-3.5" /> Explanation
        </div>
        <p className="text-sm leading-relaxed">{run.summary}</p>
      </div>

      <div className="space-y-2">
        <div className="text-xs text-muted-foreground">Budget warnings (Phase 4 limits)</div>
        {run.budget_warnings.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No budget category is projected over its limit (or there are no budgets for these months).
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Projected</TableHead>
                <TableHead className="text-right">Limit</TableHead>
                <TableHead className="text-right">Over by</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.budget_warnings.map((warning) => (
                <TableRow key={`${warning.month}-${warning.category}`}>
                  <TableCell>{warning.month}</TableCell>
                  <TableCell className="font-medium">{warning.category}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(warning.projected)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(warning.limit)}</TableCell>
                  <TableCell className="text-right tabular-nums text-destructive">
                    {formatMoney(warning.over_by)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
