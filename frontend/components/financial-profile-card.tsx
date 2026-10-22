// frontend/components/financial-profile-card.tsx
//
// Purpose: the Budget Planning Agent's INPUT. A form for monthly income and the
// savings target (% of income), saved with PUT /api/profile/financial, plus a
// "Suggest from my income" button (GET /api/profile/financial/suggestion) that
// averages your income credits in the months before the chosen month.
// The suggestion only fills the field: nothing is saved until you press Save,
// matching the backend, where the suggestion endpoint is read-only.

"use client";

import { Lightbulb } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import type { FinancialProfile, IncomeSuggestion } from "@/lib/types";

// Same rule as the backend schema: a plain number with at most 2 decimal places
const MONEY_PATTERN = /^\d+(\.\d{1,2})?$/;

/** Returns an error message, or null when both fields are valid. */
function validate(income: string, pct: string): string | null {
  if (!MONEY_PATTERN.test(income) || Number(income) <= 0) {
    return "Monthly income must be a number above 0 with at most 2 decimals (e.g. 60000).";
  }
  if (!MONEY_PATTERN.test(pct) || Number(pct) >= 100) {
    return "Savings target must be a percentage from 0 to less than 100 (e.g. 20).";
  }
  return null;
}

export function FinancialProfileCard({
  profile,
  loading,
  error,
  month,
  track,
  onSaved,
}: {
  profile: FinancialProfile | null;
  loading: boolean;
  error: unknown; // already filtered by the page: a 404 "no profile yet" is NOT passed here
  month: string;
  track: ApiLog["track"];
  onSaved: (profile: FinancialProfile) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          1. Financial profile
          {!loading && !profile && <Badge variant="outline">not set yet</Badge>}
        </CardTitle>
        <CardDescription>
          {profile?.updated_at
            ? `Last saved ${formatDateTime(profile.updated_at)}.`
            : "The planner splits (income − savings target) across your expense categories. Use made-up numbers."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ApiErrorAlert error={error} />
        {loading && !profile ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          // `key` re-creates the form (fresh field values) whenever a new profile is saved.
          // That avoids copying props into state inside an effect.
          <ProfileForm
            key={profile?.updated_at ?? "new"}
            profile={profile}
            month={month}
            track={track}
            onSaved={onSaved}
          />
        )}
        {profile && (
          <StatGrid>
            <StatTile label="Monthly income" value={formatMoney(profile.monthly_income)} />
            <StatTile
              label="Savings target"
              value={formatMoney(profile.savings_target_amount)}
              hint={`${profile.savings_target_pct}% of income`}
              tone="good"
            />
            <StatTile label="Spendable" value={formatMoney(profile.spendable_income)} hint="what the budget splits" />
          </StatGrid>
        )}
      </CardContent>
    </Card>
  );
}

function ProfileForm({
  profile,
  month,
  track,
  onSaved,
}: {
  profile: FinancialProfile | null;
  month: string;
  track: ApiLog["track"];
  onSaved: (profile: FinancialProfile) => void;
}) {
  const [income, setIncome] = useState(profile ? String(profile.monthly_income) : "");
  const [pct, setPct] = useState(profile ? String(profile.savings_target_pct) : "20");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [suggestion, setSuggestion] = useState<IncomeSuggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = validate(income.trim(), pct.trim());
    if (problem) {
      setError(new Error(problem));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await track(api.saveFinancialProfile(Number(income), Number(pct)));
      onSaved(data);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function suggest() {
    setSuggesting(true);
    setError(null);
    try {
      const { data } = await track(api.getIncomeSuggestion(month));
      setSuggestion(data);
    } catch (err) {
      setError(err);
    } finally {
      setSuggesting(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="monthly-income">Monthly income (₹)</Label>
          <Input
            id="monthly-income"
            inputMode="decimal"
            autoComplete="off"
            placeholder="60000"
            value={income}
            onChange={(event) => setIncome(event.target.value)}
            disabled={busy}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="savings-pct">Savings target (% of income)</Label>
          <Input
            id="savings-pct"
            inputMode="decimal"
            autoComplete="off"
            placeholder="20"
            value={pct}
            onChange={(event) => setPct(event.target.value)}
            disabled={busy}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : profile ? "Save changes" : "Save profile"}
        </Button>
        <Button type="button" variant="outline" className="gap-2" onClick={suggest} disabled={suggesting || busy}>
          <Lightbulb className="size-4" />
          {suggesting ? "Looking…" : "Suggest from my income"}
        </Button>
      </div>

      <ApiErrorAlert error={error} />
      {suggestion && <SuggestionBox suggestion={suggestion} onUse={(value) => setIncome(String(value))} />}
    </form>
  );
}

function SuggestionBox({ suggestion, onUse }: { suggestion: IncomeSuggestion; onUse: (value: number) => void }) {
  const value = suggestion.suggested_monthly_income;
  return (
    <div className="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm">
      <p>
        <span className="font-medium">Suggestion for {suggestion.month}</span>{" "}
        <span className="text-muted-foreground">
          (looked at {suggestion.window_start} to {suggestion.window_end})
        </span>
      </p>
      <p className="text-muted-foreground">{suggestion.message}</p>
      {suggestion.income_by_month.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestion.income_by_month.map((entry) => (
            <Badge key={entry.month} variant="outline">
              {entry.month}: {formatMoney(entry.total)}
            </Badge>
          ))}
        </div>
      )}
      {value != null && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-base font-semibold tabular-nums">{formatMoney(value)}</span>
          <Button type="button" size="sm" variant="secondary" onClick={() => onUse(value)}>
            Use this
          </Button>
          <span className="text-xs text-muted-foreground">fills the field; press Save to keep it</span>
        </div>
      )}
    </div>
  );
}
