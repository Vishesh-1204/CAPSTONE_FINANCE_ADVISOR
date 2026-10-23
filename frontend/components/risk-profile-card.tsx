// frontend/components/risk-profile-card.tsx
//
// Purpose: section 1 of /investing, the Investment Advisor's INPUT. A form for the
// three questionnaire answers (risk TOLERANCE: how you feel about losses) and the
// facts behind risk CAPACITY (how much loss your finances can absorb), saved with
// PUT /api/investing/profile. After saving it shows both scores, both categories,
// the final category (the SAFER of the two) and, in plain words, which side was
// the limit. Emergency-fund months are computed by the backend from your own
// essential spending, so they are shown here but never typed in.

"use client";

import { ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDateTime, formatMoney, humanize } from "@/lib/format";
import { LIMITING_FACTOR_TEXT } from "@/lib/investing";
import type { InvestmentProfile, InvestmentProfileUpdate } from "@/lib/types";

// Same rules as the backend schema (schemas/investment.py)
const MONEY_PATTERN = /^\d+(\.\d{1,2})?$/;
const INTEGER_PATTERN = /^\d+$/;
const NUMBER_PATTERN = /^\d+(\.\d+)?$/;
const MAX_DEPENDENTS = 20;
const MAX_HORIZON_YEARS = 50;

/** A 1-5 question; only the two ends have words (that is all the backend defines). */
interface Scale {
  id: string;
  label: string;
  low: string; // meaning of 1
  high: string; // meaning of 5
}

const QUESTIONS = {
  reaction_to_20pct_drop: {
    id: "q-reaction",
    label: "If your investments fell 20% in a month, you would…",
    low: "sell everything",
    high: "buy more",
  },
  investing_experience: {
    id: "q-experience",
    label: "Your investing experience",
    low: "none",
    high: "many years",
  },
  return_vs_safety: {
    id: "q-return",
    label: "What matters more to you",
    low: "safety first",
    high: "maximum growth",
  },
} satisfies Record<string, Scale>;

const INCOME_STABILITY: Scale = {
  id: "income-stability",
  label: "Income stability",
  low: "irregular",
  high: "very stable",
};

type FormValues = {
  reaction_to_20pct_drop: string;
  investing_experience: string;
  return_vs_safety: string;
  income_stability: string;
  dependents: string;
  horizon_years: string;
  liquid_savings: string;
  existing_monthly_sip: string;
};

function initialValues(profile: InvestmentProfile | null): FormValues {
  if (!profile) {
    return {
      reaction_to_20pct_drop: "3",
      investing_experience: "3",
      return_vs_safety: "3",
      income_stability: "3",
      dependents: "0",
      horizon_years: "10",
      liquid_savings: "",
      existing_monthly_sip: "0",
    };
  }
  return {
    reaction_to_20pct_drop: String(profile.answers.reaction_to_20pct_drop),
    investing_experience: String(profile.answers.investing_experience),
    return_vs_safety: String(profile.answers.return_vs_safety),
    income_stability: String(profile.income_stability),
    dependents: String(profile.dependents),
    horizon_years: String(profile.horizon_years),
    liquid_savings: String(profile.liquid_savings),
    existing_monthly_sip: String(profile.existing_monthly_sip),
  };
}

/** Returns the request body, or an error message when a field is invalid. */
function toRequest(values: FormValues): InvestmentProfileUpdate | string {
  const savings = values.liquid_savings.trim();
  const sip = values.existing_monthly_sip.trim() || "0";
  const dependents = values.dependents.trim();
  const horizon = values.horizon_years.trim();
  if (!MONEY_PATTERN.test(savings)) return "Liquid savings must be a number with at most 2 decimals (e.g. 150000).";
  if (!MONEY_PATTERN.test(sip)) return "Existing monthly SIP must be a number with at most 2 decimals (0 if none).";
  if (!INTEGER_PATTERN.test(dependents) || Number(dependents) > MAX_DEPENDENTS) {
    return `Dependents must be a whole number from 0 to ${MAX_DEPENDENTS}.`;
  }
  if (!NUMBER_PATTERN.test(horizon) || Number(horizon) > MAX_HORIZON_YEARS) {
    return `Horizon must be a number of years from 0 to ${MAX_HORIZON_YEARS}.`;
  }
  return {
    answers: {
      reaction_to_20pct_drop: Number(values.reaction_to_20pct_drop),
      investing_experience: Number(values.investing_experience),
      return_vs_safety: Number(values.return_vs_safety),
    },
    income_stability: Number(values.income_stability),
    dependents: Number(dependents),
    horizon_years: Number(horizon),
    liquid_savings: Number(savings),
    existing_monthly_sip: Number(sip),
  };
}

export function RiskProfileCard({
  profile,
  loading,
  error,
  track,
  onSaved,
}: {
  profile: InvestmentProfile | null;
  loading: boolean;
  error: unknown; // already filtered by the page: a 404 "no profile yet" is NOT passed here
  track: ApiLog["track"];
  onSaved: (profile: InvestmentProfile) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          1. Risk profile
          {!loading && !profile && <Badge variant="outline">not set yet</Badge>}
        </CardTitle>
        <CardDescription>
          Two separate ideas are scored: your comfort with losses (tolerance, from the questions) and how much loss your
          finances can absorb (capacity, from the facts). The safer of the two wins. Use made-up numbers.
          {profile?.updated_at && ` Last saved ${formatDateTime(profile.updated_at)}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <ApiErrorAlert error={error} />
        {loading && !profile ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          // `key` re-creates the form (fresh field values) whenever a new profile is saved
          <ProfileForm key={profile?.updated_at ?? "new"} profile={profile} track={track} onSaved={onSaved} />
        )}
        {profile ? (
          <ProfileResult profile={profile} />
        ) : (
          !loading && (
            <p className="text-sm text-muted-foreground">
              No risk profile yet. Answer the questions above and press Save to see your category.
            </p>
          )
        )}
      </CardContent>
    </Card>
  );
}

function ScaleSelect({ scale, value, onChange, disabled }: {
  scale: Scale;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={scale.id}>{scale.label}</Label>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger id={scale.id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {["1", "2", "3", "4", "5"].map((option) => (
            <SelectItem key={option} value={option}>
              {option}
              {option === "1" && ` · ${scale.low}`}
              {option === "5" && ` · ${scale.high}`}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        1 = {scale.low}, 5 = {scale.high}
      </p>
    </div>
  );
}

function ProfileForm({ profile, track, onSaved }: {
  profile: InvestmentProfile | null;
  track: ApiLog["track"];
  onSaved: (profile: InvestmentProfile) => void;
}) {
  const [values, setValues] = useState<FormValues>(() => initialValues(profile));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  function set(field: keyof FormValues) {
    return (value: string) => setValues((previous) => ({ ...previous, [field]: value }));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = toRequest(values);
    if (typeof request === "string") {
      setError(new Error(request));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await track(api.saveInvestmentProfile(request));
      onSaved(data);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-5">
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Questionnaire (tolerance)</legend>
        <div className="grid gap-4 md:grid-cols-3">
          {(Object.keys(QUESTIONS) as (keyof typeof QUESTIONS)[]).map((field) => (
            <ScaleSelect
              key={field}
              scale={QUESTIONS[field]}
              value={values[field]}
              onChange={set(field)}
              disabled={busy}
            />
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Your situation (capacity)</legend>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <ScaleSelect
            scale={INCOME_STABILITY}
            value={values.income_stability}
            onChange={set("income_stability")}
            disabled={busy}
          />
          <div className="space-y-2">
            <Label htmlFor="dependents">Dependents</Label>
            <Input
              id="dependents"
              inputMode="numeric"
              autoComplete="off"
              value={values.dependents}
              onChange={(event) => set("dependents")(event.target.value)}
              disabled={busy}
            />
            <p className="text-xs text-muted-foreground">People who rely on your income</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="horizon">Horizon (years)</Label>
            <Input
              id="horizon"
              inputMode="decimal"
              autoComplete="off"
              value={values.horizon_years}
              onChange={(event) => set("horizon_years")(event.target.value)}
              disabled={busy}
            />
            <p className="text-xs text-muted-foreground">When you expect to need the money</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="liquid-savings">Liquid savings (₹)</Label>
            <Input
              id="liquid-savings"
              inputMode="decimal"
              autoComplete="off"
              placeholder="150000"
              value={values.liquid_savings}
              onChange={(event) => set("liquid_savings")(event.target.value)}
              disabled={busy}
            />
            <p className="text-xs text-muted-foreground">Cash you could use in an emergency</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="existing-sip">Existing monthly SIP (₹)</Label>
            <Input
              id="existing-sip"
              inputMode="decimal"
              autoComplete="off"
              placeholder="0"
              value={values.existing_monthly_sip}
              onChange={(event) => set("existing_monthly_sip")(event.target.value)}
              disabled={busy}
            />
            <p className="text-xs text-muted-foreground">What you already invest each month</p>
          </div>
        </div>
      </fieldset>

      <Button type="submit" disabled={busy}>
        {busy ? "Saving…" : profile ? "Save changes" : "Save profile"}
      </Button>
      <ApiErrorAlert error={error} />
    </form>
  );
}

// The two caps in risk_scoring.capacity_score (EMERGENCY_FLOOR_MONTHS, the fragile-household rule)
const EMERGENCY_FLOOR_MONTHS = 3;
const FRAGILE_MAX_INCOME_STABILITY = 2;
const FRAGILE_MIN_DEPENDENTS = 2;

/** What is holding capacity down, in the order the backend applies its caps. */
function capacityHint(profile: InvestmentProfile): string {
  // Savings but 0 months: the backend found no essential spending to divide by (counted as 0 months)
  if (profile.emergency_fund_months === 0 && profile.liquid_savings > 0) {
    return (
      "Your essential spending is unknown (no budgets or categorized history yet), so your emergency fund counts " +
      `as 0 months and capacity is capped. Categorize transactions or generate a budget, then save again.`
    );
  }
  if (profile.emergency_fund_months < EMERGENCY_FLOOR_MONTHS) {
    return `With under ${EMERGENCY_FLOOR_MONTHS} months of essentials saved, capacity is capped until the emergency fund reaches ${EMERGENCY_FLOOR_MONTHS} months.`;
  }
  if (profile.income_stability <= FRAGILE_MAX_INCOME_STABILITY && profile.dependents >= FRAGILE_MIN_DEPENDENTS) {
    return "Irregular income with two or more dependents caps capacity at conservative.";
  }
  return "More emergency-fund months, steadier income, fewer dependents or a longer horizon raise capacity.";
}

function ProfileResult({ profile }: { profile: InvestmentProfile }) {
  const risk = profile.risk_profile;
  return (
    <div className="space-y-3 border-t pt-4">
      <StatGrid>
        <StatTile
          label="Tolerance score"
          value={risk.tolerance_score.toFixed(1)}
          hint={`out of 100 → ${risk.tolerance_category}`}
        />
        <StatTile
          label="Capacity score"
          value={risk.capacity_score.toFixed(1)}
          hint={`out of 100 → ${risk.capacity_category}`}
        />
        <StatTile label="Your category" value={humanize(risk.category)} hint="the safer of the two" />
        <StatTile
          label="Emergency fund"
          value={`${profile.emergency_fund_months.toFixed(1)} months`}
          hint={`${formatMoney(profile.liquid_savings)} ÷ essential spending`}
          tone={profile.emergency_fund_months < 3 ? "warn" : "default"}
        />
      </StatGrid>
      <Alert>
        <ShieldCheck />
        <AlertTitle>{LIMITING_FACTOR_TEXT[risk.limiting_factor]}</AlertTitle>
        <AlertDescription>
          Tolerance says {risk.tolerance_category}, capacity says {risk.capacity_category}, so you are{" "}
          {risk.category}.
          {risk.limiting_factor === "capacity" && ` ${capacityHint(profile)}`}
        </AlertDescription>
      </Alert>
      <p className="text-xs text-muted-foreground">{profile.disclaimer}</p>
    </div>
  );
}
