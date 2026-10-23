// frontend/app/(console)/investing/page.tsx
//
// Purpose: /investing — exercise the Investment Advisor Agent (Phase 6) from a browser:
// 1. risk profile: questionnaire (tolerance) + facts (capacity); the safer one wins
// 2. holdings: rupees per asset class, with current weights
// 3. generate a plan, with an optional goal (amount + years)
// 4. the plan: allocation vs target, rebalancing, SIP, projection range, instrument
//    types, assumptions, warnings, explanation; plus the history of saved plans
// 5. a what-if simulator (nothing stored)
// plus a permanent disclaimer banner and the Raw API response panel. Synthetic data only.
//
// The page owns the loads (profile, holdings, latest plan, plan history, assumptions)
// and which plan is shown; the components only display and report changes back.
// GET /profile and GET /plan/latest answer 404 until the first one is saved: that is
// an empty state, not an error.

"use client";

import { Info, Sparkles } from "lucide-react";
import { useCallback, useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { HoldingsEditor } from "@/components/holdings-editor";
import { InvestmentPlanResult } from "@/components/investment-plan-result";
import { PageHeader } from "@/components/page-header";
import { PlanHistoryList } from "@/components/plan-history-list";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { RiskProfileCard } from "@/components/risk-profile-card";
import { WhatIfSimulator } from "@/components/what-if-simulator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { ApiError, api } from "@/lib/api";
import type { InvestmentPlan } from "@/lib/types";

// The page's own wording, kept identical to the backend's INVESTING_DISCLAIMER
const BANNER =
  "Educational planning tool, not financial advice. Projections are scenarios under stated assumptions, not guarantees.";

// Same rules as the backend PlanRequest
const MONEY_PATTERN = /^\d+(\.\d{1,2})?$/;
const YEARS_PATTERN = /^\d+(\.\d+)?$/;
const GOAL_MIN_YEARS = 1;
const GOAL_MAX_YEARS = 40;

/** Returns [goalAmount, goalYears] (undefined = not sent), or an error message. */
function parseGoal(amount: string, years: string): [number | undefined, number | undefined] | string {
  if (amount === "" && years === "") return [undefined, undefined];
  if (amount === "") return "Goal years need a goal amount (or leave both empty).";
  if (!MONEY_PATTERN.test(amount) || Number(amount) <= 0) {
    return "Goal amount must be a number above 0 with at most 2 decimals (e.g. 1000000).";
  }
  if (years === "") return [Number(amount), undefined]; // the backend uses the profile horizon
  if (!YEARS_PATTERN.test(years) || Number(years) < GOAL_MIN_YEARS || Number(years) > GOAL_MAX_YEARS) {
    return `Goal years must be from ${GOAL_MIN_YEARS} to ${GOAL_MAX_YEARS}.`;
  }
  return [Number(amount), Number(years)];
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export default function InvestingPage() {
  const log = useApiLog();

  // --- loads (each loader is wrapped in useCallback, see hooks/use-api-data.ts) ---
  const loadProfile = useCallback(() => api.getInvestmentProfile(), []);
  const profile = useApiData(loadProfile, log.track);
  const loadHoldings = useCallback(() => api.getHoldings(), []);
  const holdings = useApiData(loadHoldings, log.track);
  const loadLatest = useCallback(() => api.getLatestInvestmentPlan(), []);
  const latest = useApiData(loadLatest, log.track);
  const loadHistory = useCallback(() => api.listInvestmentPlans(), []);
  const history = useApiData(loadHistory, log.track);
  const loadAssumptions = useCallback(() => api.getInvestingAssumptions(), []);
  const assumptions = useApiData(loadAssumptions, log.track);

  // A 404 just means "nothing saved yet". Once data arrives it wins over that old 404.
  const profileError = isNotFound(profile.error) || profile.data ? null : profile.error;
  const latestError = isNotFound(latest.error) || latest.data ? null : latest.error;

  // --- which plan is shown: one opened from the history / just generated, else the latest ---
  const [opened, setOpened] = useState<InvestmentPlan | null>(null);
  const shownPlan = opened ?? latest.data;
  const [openingId, setOpeningId] = useState<number | null>(null);
  const [openError, setOpenError] = useState<unknown>(null);

  async function openPlan(id: number) {
    if (id === shownPlan?.id) return;
    setOpeningId(id);
    setOpenError(null);
    try {
      const { data } = await log.track(api.getInvestmentPlan(id));
      setOpened(data);
    } catch (err) {
      setOpenError(err);
    } finally {
      setOpeningId(null);
    }
  }

  // --- generate ---
  const [goalAmount, setGoalAmount] = useState("");
  const [goalYears, setGoalYears] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<unknown>(null);
  const { reload: reloadHistory } = history;

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setGenerateError(null);
    const goal = parseGoal(goalAmount.trim(), goalYears.trim());
    if (typeof goal === "string") {
      setGenerateError(new Error(goal));
      return;
    }
    setGenerating(true);
    try {
      const { data } = await log.track(api.generateInvestmentPlan(goal[0], goal[1]));
      setOpened(data); // the new plan replaces the shown one, no refetch needed
      reloadHistory();
    } catch (err) {
      setGenerateError(err);
    } finally {
      setGenerating(false);
    }
  }

  const noProfile = !profile.data && !profile.loading;
  const profileMissingError = generateError instanceof ApiError && generateError.status === 409;

  return (
    <>
      <PageHeader
        title="Investing"
        description="Python computes every number (risk scores, target split, an affordable SIP from your forecast's low end, a seeded simulation); the LLM only explains them, and two guards reject invented amounts and promises."
      />

      <Alert className="border-primary/40">
        <Info />
        <AlertTitle>{BANNER}</AlertTitle>
        <AlertDescription>
          Use synthetic data only. Every return and volatility figure below is an illustrative assumption.
        </AlertDescription>
      </Alert>

      <RiskProfileCard
        profile={profile.data}
        loading={profile.loading}
        error={profileError}
        track={log.track}
        onSaved={(saved) => profile.setData(() => saved)}
      />

      <HoldingsEditor
        holdings={holdings.data}
        loading={holdings.loading}
        error={holdings.error}
        track={log.track}
        onSaved={(saved) => holdings.setData(() => saved)}
      />

      <Card>
        <CardHeader>
          <CardTitle>3. Generate a plan</CardTitle>
          <CardDescription>
            Uses your risk profile, your holdings, the low end of your latest 30-day forecast (generate one on the
            Forecast page first for the best SIP) and your essential spending. A goal is optional; without goal years
            the plan uses your profile horizon. Each plan is saved.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={generate} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:max-w-xl">
              <div className="space-y-2">
                <Label htmlFor="goal-amount">Goal amount (₹, optional)</Label>
                <Input
                  id="goal-amount"
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="e.g. 1000000"
                  value={goalAmount}
                  onChange={(event) => setGoalAmount(event.target.value)}
                  disabled={generating}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="goal-years">
                  Goal years ({GOAL_MIN_YEARS}–{GOAL_MAX_YEARS}, optional)
                </Label>
                <Input
                  id="goal-years"
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder={profile.data ? `default ${profile.data.horizon_years}` : "e.g. 8"}
                  value={goalYears}
                  onChange={(event) => setGoalYears(event.target.value)}
                  disabled={generating}
                />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={generating || !profile.data} className="gap-2">
                <Sparkles className="size-4" />
                {generating ? "Generating…" : "Generate plan"}
              </Button>
              {noProfile && (
                <span className="text-sm text-muted-foreground">Save your risk profile (section 1) first.</span>
              )}
            </div>
            <ApiErrorAlert error={generateError} />
            {profileMissingError && (
              <p className="text-sm text-muted-foreground">
                The backend has no risk profile for you yet: fill in section 1 and press Save, then generate again.
              </p>
            )}
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>4. Your plan</CardTitle>
          <CardDescription>
            The latest saved plan, or the one you open from the history below. Warnings about a missing or old forecast
            appear at the top.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <ApiErrorAlert error={latestError} />
          {shownPlan ? (
            <InvestmentPlanResult plan={shownPlan} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {latest.loading ? "Loading…" : "No plan yet. Generate one in section 3."}
            </p>
          )}

          <div className="space-y-2 border-t pt-4">
            <h3 className="text-sm font-medium">Plan history</h3>
            <p className="text-xs text-muted-foreground">Newest first. Click a plan to show it above.</p>
            <ApiErrorAlert error={history.error} />
            <ApiErrorAlert error={openError} />
            <PlanHistoryList
              plans={history.data?.plans ?? null}
              loading={history.loading}
              shownId={shownPlan?.id ?? null}
              openingId={openingId}
              onOpen={openPlan}
            />
          </div>
        </CardContent>
      </Card>

      <WhatIfSimulator
        defaultCategory={profile.data?.risk_profile.category ?? null}
        assumptions={assumptions.data}
        track={log.track}
      />
      <ApiErrorAlert error={assumptions.error} />

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
