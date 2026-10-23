// frontend/components/investment-plan-result.tsx
//
// Purpose: shows ONE saved investment plan (InvestmentPlan from POST /plan,
// GET /plan/latest or GET /plans/{id}):
//   * header badges: when it was made, the category, the explanation source
//   * warnings in plain words (forecast_missing, forecast_stale, ...)
//   * target vs current allocation (chart) and the rebalance table
//   * the SIP card (capacity, recommendation, change vs existing, emergency top-up)
//     with the surplus and emergency-fund facts it was sized from
//   * the projection: bad / typical / good case and the chance of reaching the goal
//   * instrument TYPES per asset class, the assumptions panel, the explanation
// Display only: the page owns the data and the API calls. Every number comes from
// the backend (Python computes, the LLM only narrates).

import { BookOpen, Info, TriangleAlert } from "lucide-react";

import { AllocationChart } from "@/components/allocation-chart";
import { ProjectionChart, type ProjectionSeries } from "@/components/projection-chart";
import { StatGrid, StatTile } from "@/components/stat-tile";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatDay, formatMoney, formatPercent, humanize } from "@/lib/format";
import { ACTION_LABELS, ASSET_LABELS, LIMITING_FACTOR_TEXT, SIP_LIMIT_TEXT, WARNING_TEXT } from "@/lib/investing";
import type { InvestingAssumptions, InvestmentPlan, Projection, RebalanceRow } from "@/lib/types";

const SURPLUS_SOURCE: Record<InvestmentPlan["surplus"]["source"], string> = {
  forecast: "low end of your 30-day forecast",
  budget: "income minus budgets (no forecast)",
  none: "unknown",
};

const ESSENTIALS_SOURCE: Record<InvestmentPlan["emergency_fund"]["essentials_source"], string> = {
  budget: "from your budget limits",
  history: "from your spending history",
  none: "unknown",
};

/** "AI-written" only when the LLM text was used AND passed both guards (explanation_source "llm"). */
export function ExplanationBadge({ plan }: { plan: Pick<InvestmentPlan, "explanation_source" | "llm_used"> }) {
  if (plan.explanation_source === "llm") return <Badge variant="secondary">AI-written · guards passed</Badge>;
  if (plan.llm_used) return <Badge variant="outline">Template (AI text rejected by a guard)</Badge>;
  return <Badge variant="outline">Template</Badge>;
}

function signedMoney(value: number): string {
  return value > 0 ? `+${formatMoney(value)}` : formatMoney(value);
}

function months(value: number | null): string {
  if (value == null) return "—";
  const years = Math.floor(value / 12);
  const rest = value % 12;
  if (years === 0) return `${rest} months`;
  return rest === 0 ? `${years} years` : `${years} y ${rest} m`;
}

export function InvestmentPlanResult({ plan }: { plan: InvestmentPlan }) {
  const goal = plan.goal.amount;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Plan #{plan.id}</span>
        {plan.created_at && <span className="text-sm text-muted-foreground">{formatDateTime(plan.created_at)}</span>}
        <Badge>{humanize(plan.risk_profile.category)}</Badge>
        <ExplanationBadge plan={plan} />
        <Badge variant="outline">engine {plan.engine_version}</Badge>
      </div>

      <Warnings warnings={plan.warnings} />

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Allocation: target vs what you hold</h3>
        <p className="text-xs text-muted-foreground">
          {LIMITING_FACTOR_TEXT[plan.risk_profile.limiting_factor]} The target suits a {plan.risk_profile.category}{" "}
          investor over {plan.goal.years} years. Rebalancing is only suggested when a class is more than 5 percentage
          points off target.
        </p>
        <div className="grid gap-4 lg:grid-cols-2">
          <AllocationChart target={plan.target_allocation} current={plan.current_allocation} />
          <RebalanceTable rows={plan.rebalance} />
        </div>
      </section>

      <SipSection plan={plan} />

      <ProjectionSection plan={plan} goal={goal} />

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Instrument types per asset class</h3>
        <p className="text-xs text-muted-foreground">Kinds of instrument only, never named funds.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {plan.instruments.map((item) => (
            <div key={item.asset_class} className="rounded-lg border p-3">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium">{ASSET_LABELS[item.asset_class]}</span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  target {formatPercent(plan.target_allocation[item.asset_class] ?? 0)}
                </span>
              </div>
              <ul className="mt-1 list-disc pl-5 text-sm">
                {item.instrument_types.map((type) => (
                  <li key={type}>{type}</li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-muted-foreground">{item.note}</p>
            </div>
          ))}
        </div>
      </section>

      <AssumptionsPanel assumptions={plan.assumptions} />

      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="flex items-center gap-1.5 text-sm font-medium">
            <BookOpen className="size-4" /> Explanation
          </h3>
          <ExplanationBadge plan={plan} />
        </div>
        {plan.explanation ? (
          <p className="text-sm leading-relaxed whitespace-pre-line">{plan.explanation}</p>
        ) : (
          <p className="text-sm text-muted-foreground">No explanation was stored for this plan.</p>
        )}
      </section>

      <p className="text-xs text-muted-foreground">{plan.disclaimer}</p>
    </div>
  );
}

function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) {
    return <p className="text-sm text-muted-foreground">No warnings for this plan.</p>;
  }
  return (
    <div className="space-y-2">
      {warnings.map((code) => {
        const text = WARNING_TEXT[code] ?? { title: humanize(code), body: "See the backend docs for this warning." };
        return (
          <Alert key={code} className="border-amber-500/50 text-amber-800 dark:text-amber-300">
            <TriangleAlert />
            <AlertTitle>{text.title}</AlertTitle>
            <AlertDescription className="text-amber-800/90 dark:text-amber-300/90">{text.body}</AlertDescription>
          </Alert>
        );
      })}
    </div>
  );
}

function RebalanceTable({ rows }: { rows: RebalanceRow[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Asset class</TableHead>
            <TableHead className="text-right">Current</TableHead>
            <TableHead className="text-right">Target</TableHead>
            <TableHead>Action</TableHead>
            <TableHead className="text-right">Change</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.asset_class}>
              <TableCell className="font-medium">{ASSET_LABELS[row.asset_class]}</TableCell>
              <TableCell className="text-right tabular-nums">{formatPercent(row.current_weight)}</TableCell>
              <TableCell className="text-right tabular-nums">{formatPercent(row.target_weight)}</TableCell>
              <TableCell>
                <Badge variant={row.action === "hold" ? "outline" : "secondary"}>{ACTION_LABELS[row.action]}</Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {row.action === "hold" ? "—" : signedMoney(row.amount_change)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="mt-1 text-xs text-muted-foreground">
        Rupee changes move money between classes at the same total value.
      </p>
    </div>
  );
}

function SipSection({ plan }: { plan: InvestmentPlan }) {
  const { sip, surplus, emergency_fund: fund } = plan;
  const existing = sip.recommended_total - sip.change_vs_existing;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Monthly SIP</h3>
      <StatGrid>
        <StatTile
          label="Can safely invest"
          value={formatMoney(sip.capacity_total)}
          hint={`${formatPercent(plan.assumptions.sip_safety_factor)} of the low-end surplus, minus the top-up`}
        />
        <StatTile
          label="Recommended SIP"
          value={formatMoney(sip.recommended_total)}
          hint={SIP_LIMIT_TEXT[sip.limited_by]}
          tone="good"
        />
        <StatTile
          label="Change vs existing"
          value={signedMoney(sip.change_vs_existing)}
          hint={`you invest ${formatMoney(existing)} now`}
          tone={sip.change_vs_existing < 0 ? "warn" : "default"}
        />
        <StatTile
          label="Emergency top-up"
          value={formatMoney(sip.emergency_topup)}
          hint={sip.emergency_topup_capped ? "your whole surplus (capped)" : "a month, into the emergency fund first"}
          tone={sip.emergency_topup > 0 ? "warn" : "default"}
        />
      </StatGrid>
      <div className="text-xs text-muted-foreground">Sized from</div>
      <StatGrid>
        <StatTile
          label="Low-end monthly surplus"
          value={formatMoney(surplus.monthly_low)}
          hint={`${SURPLUS_SOURCE[surplus.source]}${
            surplus.forecast_as_of ? ` of ${formatDay(surplus.forecast_as_of, true)}` : ""
          }`}
          tone={surplus.source === "forecast" ? "default" : "warn"}
        />
        <StatTile
          label="Essential spending"
          value={formatMoney(fund.essential_monthly_spend)}
          hint={`a month, ${ESSENTIALS_SOURCE[fund.essentials_source]}`}
        />
        <StatTile
          label="Emergency fund"
          value={`${fund.months_covered.toFixed(1)} months`}
          hint={`target ${fund.target_months} months`}
          tone={fund.months_covered < fund.target_months ? "warn" : "good"}
        />
        <StatTile label="Emergency gap" value={formatMoney(fund.gap)} hint="still missing to reach the target" />
      </StatGrid>
    </section>
  );
}

function sipLabel(prefix: string, projection: Projection): string {
  return `${prefix} ${formatMoney(projection.monthly_sip)}`;
}

function ProjectionSection({ plan, goal }: { plan: InvestmentPlan; goal: number | null }) {
  const current = plan.projection_current;
  const recommended = plan.projection_recommended;
  // With no existing SIP the "current" bars are all ₹0: show only the recommendation
  const series: ProjectionSeries[] = [
    ...(current.monthly_sip > 0 ? [{ label: sipLabel("Existing SIP", current), projection: current }] : []),
    { label: sipLabel("Recommended SIP", recommended), projection: recommended },
  ];
  const putIn = recommended.monthly_sip * Math.round(recommended.years * 12);

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Projection over {recommended.years} years</h3>
      <p className="text-xs text-muted-foreground">
        {recommended.n_paths.toLocaleString("en-IN")} simulated paths with the target split (assumed return{" "}
        {formatPercent(plan.portfolio.expected_return)} a year, volatility {formatPercent(plan.portfolio.volatility)}).
        These cover the monthly SIP only, starting from ₹0 (your current holdings are not included). Scenarios under
        the assumptions below, not predictions.
      </p>
      <StatGrid>
        <StatTile label="Bad case (p10)" value={formatMoney(recommended.p10)} hint="1 in 10 paths end lower" />
        <StatTile label="Typical (p50)" value={formatMoney(recommended.p50)} hint="half end lower, half higher" />
        <StatTile label="Good case (p90)" value={formatMoney(recommended.p90)} hint="1 in 10 paths end higher" />
        <StatTile label="You would put in" value={formatMoney(putIn)} hint="recommended SIP × months" />
      </StatGrid>
      {goal != null && (
        <StatGrid>
          <StatTile label="Goal" value={formatMoney(goal)} hint={`in ${plan.goal.years} years`} />
          <StatTile
            label="Chance of reaching it"
            value={formatPercent(recommended.probability_of_goal)}
            hint={`at the recommended SIP (existing: ${formatPercent(current.probability_of_goal)})`}
            tone={(recommended.probability_of_goal ?? 0) >= 0.5 ? "good" : "warn"}
          />
          <StatTile
            label="Months to goal"
            value={months(recommended.months_to_goal)}
            hint={`at the assumed return (existing SIP: ${months(current.months_to_goal)})`}
          />
          <StatTile
            label="SIP the goal needs"
            value={formatMoney(plan.goal.required_sip)}
            hint="flat, at the assumed return (~50% chance)"
          />
        </StatGrid>
      )}
      <ProjectionChart series={series} goal={goal} />
    </section>
  );
}

/** The return and volatility numbers a projection depends on, shown so they can be challenged. */
export function AssumptionsPanel({ assumptions, title = "Assumptions this plan used" }: {
  assumptions: InvestingAssumptions;
  title?: string;
}) {
  return (
    <section className="space-y-2 rounded-lg border border-dashed p-3">
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Info className="size-4" /> {title}
      </h3>
      <p className="text-xs text-muted-foreground">
        These are ASSUMPTIONS, not market data or promises: {assumptions.note}
      </p>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Asset class</TableHead>
              <TableHead className="text-right">Assumed return / year</TableHead>
              <TableHead className="text-right">Assumed volatility / year</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {assumptions.assets.map((asset) => (
              <TableRow key={asset.asset_class}>
                <TableCell className="font-medium">{ASSET_LABELS[asset.asset_class]}</TableCell>
                <TableCell className="text-right tabular-nums">{formatPercent(asset.expected_return)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatPercent(asset.volatility)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        SIP safety factor {assumptions.sip_safety_factor} · {assumptions.mc_paths.toLocaleString("en-IN")} Monte Carlo
        paths · seed {assumptions.mc_seed} (same inputs, same result) · normal returns, no tax, fees or inflation.
      </p>
    </section>
  );
}
