// frontend/lib/investing.ts
//
// Purpose: the plain-language words the /investing page uses for the Investment
// Advisor's codes (asset classes, the limiting factor, warning codes, SIP limits),
// in one place so every investing component says the same thing.
// Display only: no maths here. Every number comes from the backend.

import type { AssetClass, LimitingFactor, RebalanceAction, RiskCategory, SipLimitedBy } from "./types";

/** Same order as the backend (allocation.ASSET_CLASSES), so tables and charts always line up. */
export const ASSET_CLASSES: AssetClass[] = ["equity", "debt", "gold", "cash"];

export const ASSET_LABELS: Record<AssetClass, string> = {
  equity: "Equity",
  debt: "Debt",
  gold: "Gold",
  cash: "Cash",
};

export const RISK_CATEGORIES: RiskCategory[] = ["conservative", "moderate", "aggressive"];

/**
 * Why the final category is what it is. Tolerance = attitude (the questionnaire),
 * capacity = finances (emergency fund, income stability, dependents, horizon);
 * the safer one wins, and "both" means the two gave the same category.
 */
export const LIMITING_FACTOR_TEXT: Record<LimitingFactor, string> = {
  capacity: "Your finances, not your comfort with risk, are the limit right now.",
  tolerance: "Your comfort with risk, not your finances, is the limit right now.",
  both: "Your comfort with risk and your finances point to the same category.",
};

export const ACTION_LABELS: Record<RebalanceAction, string> = {
  add: "Add",
  reduce: "Reduce",
  hold: "Hold",
};

export const SIP_LIMIT_TEXT: Record<SipLimitedBy, string> = {
  capacity: "limited by what you can safely afford",
  target: "capped at what the goal needs",
  none: "exactly what the goal needs", // capacity == the goal's required SIP
};

/** Plain words for the plan's warning codes (investment_service.WARNING_*). Unknown codes fall back to humanize(). */
export const WARNING_TEXT: Record<string, { title: string; body: string }> = {
  forecast_missing: {
    title: "No 30-day forecast",
    body:
      "The monthly surplus came from your income minus your budgets instead of a forecast. Generate a 30-day " +
      "forecast on the Forecast page, then generate this plan again for a better SIP.",
  },
  forecast_stale: {
    title: "Forecast is out of date",
    body: "Your latest 30-day forecast is old. Generate a new one on the Forecast page, then generate this plan again.",
  },
  surplus_unknown: {
    title: "Surplus unknown",
    body: "There is no forecast and no budget to measure your monthly surplus, so the recommended SIP is ₹0.",
  },
  essentials_unknown: {
    title: "Essential spending unknown",
    body:
      "No budgets or categorized essential spending were found, so the emergency fund could not be measured in " +
      "months. Categorize your transactions or generate a budget.",
  },
  no_holdings: {
    title: "No holdings saved",
    body: "The current allocation is empty. Add what you hold in section 2 to see rebalancing suggestions.",
  },
  emergency_fill_slow: {
    title: "Emergency fund fills slowly",
    body:
      "Your whole low-end surplus goes to the emergency fund (so the SIP is ₹0), and filling it will still take " +
      "more than 12 months.",
  },
};
