// frontend/app/(console)/budgets/page.tsx
//
// Purpose: /budgets — exercise the Budget Planning Agent (Phase 4) from a browser:
// 1. financial profile (income + savings target, with an income suggestion),
// 2. generate a plan for the chosen month (Python computes, the LLM only explains),
// 3. the limits table (inline override / reset),
// 4. status: actual spending vs limits (bars + chart),
// plus the Raw API response panel. Synthetic data only.
//
// The page owns the month and the three loads (profile, limits, status); the
// components only display and report changes back so the page can refetch.

"use client";

import { Sparkles } from "lucide-react";
import { useCallback, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { BudgetPlanCard } from "@/components/budget-plan-card";
import { BudgetStatusCard } from "@/components/budget-status-card";
import { BudgetTable } from "@/components/budget-table";
import { FinancialProfileCard } from "@/components/financial-profile-card";
import { MonthPicker } from "@/components/month-picker";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { ApiError, api } from "@/lib/api";
import { currentMonth } from "@/lib/format";
import type { BudgetPlan } from "@/lib/types";

export default function BudgetsPage() {
  const log = useApiLog();
  const [month, setMonth] = useState(currentMonth);

  // --- loads (each loader is wrapped in useCallback, see hooks/use-api-data.ts) ---
  const loadProfile = useCallback(() => api.getFinancialProfile(), []);
  const profile = useApiData(loadProfile, log.track);
  const loadBudgets = useCallback(() => api.listBudgets(month), [month]);
  const budgets = useApiData(loadBudgets, log.track);
  const loadStatus = useCallback(() => api.getBudgetStatus(month), [month]);
  const status = useApiData(loadStatus, log.track);

  // A 404 from GET /api/profile/financial just means "not saved yet": not an error to show.
  // Once a profile is saved, its data wins over that old 404.
  const noProfileYet = profile.error instanceof ApiError && profile.error.status === 404;
  const profileError = noProfileYet || profile.data ? null : profile.error;

  // --- generate ---
  const [plan, setPlan] = useState<BudgetPlan | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<unknown>(null);
  // the plan belongs to one month; switching month hides it (no effect needed)
  const shownPlan = plan?.month === month ? plan : null;

  const { reload: reloadBudgets } = budgets;
  const { reload: reloadStatus } = status;
  const refreshMonth = useCallback(() => {
    reloadBudgets();
    reloadStatus();
  }, [reloadBudgets, reloadStatus]);

  async function generate() {
    setGenerating(true);
    setGenerateError(null);
    try {
      const { data } = await log.track(api.generateBudget(month));
      setPlan(data);
      refreshMonth();
    } catch (err) {
      setGenerateError(err);
    } finally {
      setGenerating(false);
    }
  }

  const monthPicker = <MonthPicker id="budget-month" value={month} onChange={setMonth} />;

  return (
    <>
      <PageHeader
        title="Budgets"
        description="Python computes every limit; the LLM only explains them (one call per plan) and a guard rejects any rupee amount it invents."
        actions={monthPicker}
      />

      <FinancialProfileCard
        profile={profile.data}
        loading={profile.loading}
        error={profileError}
        month={month}
        track={log.track}
        onSaved={(saved) => profile.setData(() => saved)}
      />

      <Card>
        <CardHeader>
          <CardTitle>2. Generate budget for {month}</CardTitle>
          <CardDescription>
            Uses your categorized expenses from the months before {month}. Regenerating refreshes the planner’s limits
            and keeps the ones you set yourself. The explanation below is only shown right after generating (it is not
            stored).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={generate} disabled={generating || !profile.data} className="gap-2">
              <Sparkles className="size-4" />
              {generating ? "Generating…" : "Generate budget"}
            </Button>
            {!profile.data && !profile.loading && (
              <span className="text-sm text-muted-foreground">Save your financial profile first.</span>
            )}
          </div>
          <ApiErrorAlert error={generateError} />
          {shownPlan && <BudgetPlanCard plan={shownPlan} />}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>3. Limits for {month}</CardTitle>
          <CardDescription>
            Edit a limit to override it (source becomes “user”); reset puts the planner’s number back. Hover a reason to
            read it in full.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ApiErrorAlert error={budgets.error} />
          <BudgetTable
            budgets={budgets.data}
            loading={budgets.loading}
            spendable={profile.data?.spendable_income ?? null}
            track={log.track}
            onChanged={refreshMonth}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>4. Status for {month}</CardTitle>
          <CardDescription>
            Actual spending against each limit. Warning near the limit (from 80 % by default, set by
            BUDGET_WARNING_THRESHOLD on the backend); exceeded above 100 %. Investments are shown separately, never
            as spending.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ApiErrorAlert error={status.error} />
          {status.data ? (
            <BudgetStatusCard status={status.data} />
          ) : (
            <p className="text-sm text-muted-foreground">{status.loading ? "Loading…" : "Could not load status."}</p>
          )}
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
