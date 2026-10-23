// frontend/components/what-if-simulator.tsx
//
// Purpose: section 5 of /investing. "What if I invested ₹X a month for Y years as a
// moderate investor?" Calls POST /api/investing/simulate, which runs the same
// seeded Monte Carlo as a plan but stores NOTHING. The number of paths and the
// seed come from the backend settings, so the client cannot change them.
// Shows the split for that category, the portfolio's assumed return and
// volatility, and the bad / typical / good range (with the chance of a goal).

"use client";

import { FlaskConical } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { AssumptionsPanel } from "@/components/investment-plan-result";
import { ProjectionChart } from "@/components/projection-chart";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatMoney, formatPercent, humanize } from "@/lib/format";
import { ASSET_CLASSES, ASSET_LABELS, RISK_CATEGORIES } from "@/lib/investing";
import type { InvestingAssumptions, RiskCategory, SimulateRequest, SimulateResponse } from "@/lib/types";

// Same bounds as the backend (schemas/investment.py)
const MONEY_PATTERN = /^\d+(\.\d{1,2})?$/;
const YEARS_PATTERN = /^\d+(\.\d+)?$/;
const MIN_YEARS = 1;
const MAX_YEARS = 40;
const MAX_MONTHLY_SIP = 10_000_000;

/** Returns the request, or an error message. */
function toRequest(sip: string, years: string, category: RiskCategory, goal: string): SimulateRequest | string {
  if (!MONEY_PATTERN.test(sip) || Number(sip) > MAX_MONTHLY_SIP) {
    return `Monthly SIP must be a number from 0 to ${formatMoney(MAX_MONTHLY_SIP)} with at most 2 decimals.`;
  }
  if (!YEARS_PATTERN.test(years) || Number(years) < MIN_YEARS || Number(years) > MAX_YEARS) {
    return `Years must be from ${MIN_YEARS} to ${MAX_YEARS}.`;
  }
  if (goal !== "" && (!MONEY_PATTERN.test(goal) || Number(goal) <= 0)) {
    return "Goal must be a number above 0 with at most 2 decimals, or empty.";
  }
  return {
    monthly_sip: Number(sip),
    years: Number(years),
    category,
    ...(goal !== "" ? { goal_amount: Number(goal) } : {}),
  };
}

export function WhatIfSimulator({
  defaultCategory,
  assumptions,
  track,
}: {
  defaultCategory: RiskCategory | null; // the saved profile's category, if any
  assumptions: InvestingAssumptions | null; // the ones in force now (GET /assumptions)
  track: ApiLog["track"];
}) {
  const [sip, setSip] = useState("10000");
  const [years, setYears] = useState("10");
  const [chosenCategory, setChosenCategory] = useState<RiskCategory | null>(null); // null = follow the profile
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // the response plus the goal that was SENT (the input may be edited after the run)
  const [result, setResult] = useState<{ data: SimulateResponse; goal: number | null } | null>(null);
  const category = chosenCategory ?? defaultCategory ?? "moderate";

  async function simulate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = toRequest(sip.trim(), years.trim(), category, goal.trim());
    if (typeof request === "string") {
      setError(new Error(request));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await track(api.simulateInvestment(request));
      setResult({ data, goal: request.goal_amount ?? null });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const projection = result?.data.projection;
  const resultGoal = result?.goal ?? null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>5. What-if simulator</CardTitle>
        <CardDescription>
          Try any SIP, period and risk category under the current assumptions. Nothing is saved, and it does not use your
          profile or holdings (only the category defaults to yours).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <form onSubmit={simulate} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-2">
              <Label htmlFor="sim-sip">Monthly SIP (₹)</Label>
              <Input
                id="sim-sip"
                inputMode="decimal"
                autoComplete="off"
                value={sip}
                onChange={(event) => setSip(event.target.value)}
                disabled={busy}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="sim-years">Years ({MIN_YEARS}–{MAX_YEARS})</Label>
              <Input
                id="sim-years"
                inputMode="decimal"
                autoComplete="off"
                value={years}
                onChange={(event) => setYears(event.target.value)}
                disabled={busy}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="sim-category">Risk category</Label>
              <Select
                value={category}
                onValueChange={(value) => setChosenCategory(value as RiskCategory)}
                disabled={busy}
              >
                <SelectTrigger id="sim-category" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RISK_CATEGORIES.map((option) => (
                    <SelectItem key={option} value={option}>
                      {humanize(option)}
                      {option === defaultCategory && " (yours)"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="sim-goal">Goal (₹, optional)</Label>
              <Input
                id="sim-goal"
                inputMode="decimal"
                autoComplete="off"
                placeholder="e.g. 2000000"
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                disabled={busy}
              />
            </div>
          </div>
          <Button type="submit" disabled={busy} className="gap-2">
            <FlaskConical className="size-4" />
            {busy ? "Simulating…" : "Simulate"}
          </Button>
          <ApiErrorAlert error={error} />
        </form>

        {result && projection ? (
          <div className="space-y-3 border-t pt-4">
            <div className="text-xs text-muted-foreground">
              {humanize(result.data.category)} split:{" "}
              {ASSET_CLASSES.map(
                (assetClass) => `${ASSET_LABELS[assetClass]} ${formatPercent(result.data.target_allocation[assetClass] ?? 0)}`,
              ).join(" · ")}{" "}
              — assumed return {formatPercent(result.data.expected_return)} a year, volatility{" "}
              {formatPercent(result.data.volatility)}, {projection.n_paths.toLocaleString("en-IN")} paths.
            </div>
            <StatGrid>
              <StatTile label="Bad case (p10)" value={formatMoney(projection.p10)} />
              <StatTile label="Typical (p50)" value={formatMoney(projection.p50)} />
              <StatTile label="Good case (p90)" value={formatMoney(projection.p90)} />
              <StatTile
                label={projection.probability_of_goal != null ? "Chance of reaching the goal" : "You would put in"}
                value={
                  projection.probability_of_goal != null
                    ? formatPercent(projection.probability_of_goal)
                    : formatMoney(projection.monthly_sip * Math.round(projection.years * 12))
                }
                hint={projection.probability_of_goal != null ? `of ${formatMoney(resultGoal)}` : "SIP × months"}
              />
            </StatGrid>
            <ProjectionChart
              series={[{ label: `SIP ${formatMoney(projection.monthly_sip)}`, projection }]}
              goal={resultGoal}
            />
            <p className="text-xs text-muted-foreground">{result.data.disclaimer}</p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No simulation yet. Pick the numbers above and press Simulate.</p>
        )}

        {assumptions && <AssumptionsPanel assumptions={assumptions} title="Assumptions in force now" />}
      </CardContent>
    </Card>
  );
}
