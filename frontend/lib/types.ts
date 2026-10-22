// frontend/lib/types.ts
//
// Purpose: TypeScript mirrors of the backend's Pydantic response/request shapes
// (backend/app/schemas/*.py). If a schema changes on the backend, change it here
// too; `npm run build` then points at every page that needs updating.

export type Direction = "debit" | "credit";
export type TxnType = "expense" | "income" | "investment_out" | "investment_in" | "transfer";
export type CategorySource = "rule" | "memory" | "llm" | "user" | "payee_rule";
export type StatusFilter = "needs_review" | "uncategorized" | "categorized" | "locked";

// --- auth (schemas/user.py, schemas/token.py) ---
export interface UserResponse {
  id: number;
  email: string;
  full_name: string;
  is_active: boolean;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
}

// --- transactions (schemas/transaction.py) ---
export interface Transaction {
  id: number;
  description: string;
  amount: number;
  direction: Direction;
  txn_type: TxnType | null;
  investment_subtype: string | null;
  channel: string | null;
  payee_key: string | null;
  category: string | null;
  category_confidence: number | null;
  category_reasoning: string | null;
  category_source: CategorySource | string | null;
  agent_category: string | null;
  user_locked: boolean;
  needs_review: boolean;
  balance: number | null;
  balance_ok: boolean | null;
  import_batch_id: number | null;
  transaction_date: string;
  created_at: string;
}

export interface TransactionPage {
  total: number;
  limit: number;
  offset: number;
  items: Transaction[];
}

export interface TransactionQuery {
  status?: StatusFilter;
  txn_type?: TxnType;
  category?: string;
  date_from?: string; // YYYY-MM-DD
  date_to?: string; // YYYY-MM-DD, inclusive
  limit?: number;
  offset?: number;
}

export interface PayeeRule {
  id: number;
  match_type: "upi_id" | "merchant_key" | string;
  match_value: string;
  category: string;
  created_at: string;
}

export interface CategoryCorrection {
  category: string;
  apply_to_similar: boolean;
  remember_payee: boolean;
}

export interface CorrectionResponse {
  transaction: Transaction;
  similar_updated: number;
  payee_rule: PayeeRule | null;
}

export interface BulkCategoryResponse {
  updated: number;
  updated_ids: number[];
  skipped_incompatible_ids: number[];
  not_found_ids: number[];
}

export interface CategoryTotal {
  category: string;
  total: number;
  count: number;
}

export interface MonthlySummary {
  month: string;
  transaction_count: number;
  expense_total: number;
  income_total: number;
  investment_out_total: number;
  redemption_total: number;
  transfer_total: number;
  net_investment_flow: number;
  savings_investment_rate: number | null;
  by_category: CategoryTotal[];
}

export interface CategorizationSummary {
  total_processed: number;
  categorized_by_payee_rules: number;
  categorized_by_memory: number;
  categorized_by_rules: number;
  categorized_by_llm: number;
  person_payees_guarded: number;
  needs_review: number;
  failed: number;
  message: string | null;
}

export interface CategoryOptions {
  debit: string[];
  credit: string[];
}

// --- statements (schemas/statement.py) ---
export interface StatementImportSummary {
  import_batch_id: number;
  filename: string;
  source_type: string;
  layout: string;
  rows_total: number;
  rows_imported: number;
  rows_duplicate: number;
  rows_invalid: number;
  by_txn_type: Record<string, number>;
  balance_checked: boolean;
  balance_mismatches: number;
  balance_mismatch_rows: number[];
  next_step: string;
}

export interface ImportBatch {
  id: number;
  filename: string;
  source_type: string;
  layout: string | null;
  rows_total: number;
  rows_imported: number;
  rows_duplicate: number;
  rows_invalid: number;
  balance_checked: boolean;
  balance_mismatches: number;
  created_at: string;
}

// --- budgets (schemas/budget.py, Phase 4) ---
// Money arrives as plain numbers (the backend converts Decimal -> float).
export type BudgetSource = "agent" | "user";
export type PlanMethod = "history" | "baseline";
export type BudgetStatusValue = "ok" | "warning" | "exceeded" | "unbudgeted";

export interface FinancialProfile {
  monthly_income: number;
  savings_target_pct: number; // percent, e.g. 20 = 20 %
  savings_target_amount: number;
  spendable_income: number; // income minus the savings target
  updated_at: string | null;
}

export interface MonthlyIncome {
  month: string;
  total: number;
}

export interface IncomeSuggestion {
  month: string;
  lookback_months: number;
  window_start: string;
  window_end: string;
  months_used: number;
  income_by_month: MonthlyIncome[];
  suggested_monthly_income: number | null; // null when there is no income in the window
  message: string;
}

export interface BudgetItem {
  id: number;
  month: string;
  category: string;
  limit_amount: number; // the limit in force (agent's or user's)
  agent_limit_amount: number; // the planner's original number
  source: BudgetSource;
  reasoning: string | null;
}

export interface DataQuality {
  months_used: number;
  uncategorized_count: number;
  needs_review_count: number;
  transfers_total: number;
}

export interface BudgetPlan {
  month: string;
  method: PlanMethod;
  monthly_income: number;
  savings_target_amount: number;
  spendable_income: number;
  feasible: boolean;
  shortfall: number;
  summary: string;
  llm_used: boolean;
  explanation_faithful: boolean | null;
  data_quality: DataQuality;
  budgets: BudgetItem[];
}

export interface BudgetStatusItem {
  category: string;
  limit: number | null; // null = unbudgeted category
  spent: number;
  remaining: number | null;
  percent_used: number | null; // 0-100 (NOT a fraction); null when no limit or limit 0
  status: BudgetStatusValue;
  source: BudgetSource | null;
}

export interface BudgetStatus {
  month: string;
  items: BudgetStatusItem[];
  total_limit: number;
  total_spent: number;
  total_remaining: number;
  invested_this_month: number; // investment_out total (saved, not spent)
  savings_target_amount: number | null; // null when no financial profile exists
}

// --- forecast (schemas/forecast.py, Phase 5) ---
export type ForecastHorizon = 30 | 60 | 90;
export type OpeningBalanceSource = "request" | "statement";
export type IncomeSource = "recurring" | "profile" | "none";
// money in, a fixed bill (rent, EMI, subscription), or an investment (SIP)
export type RecurringKind = "income" | "fixed_expense" | "investment";

/** A point forecast with its 80% range. */
export interface Band {
  point: number;
  lower: number;
  upper: number;
}

/** Projected end-of-day balance; the band comes from everyday-spending uncertainty. */
export interface TrajectoryPoint {
  date: string; // YYYY-MM-DD
  balance: number;
  lower: number;
  upper: number;
}

export interface RecurringItem {
  key: string; // the payee, as the forecast grouped it
  kind: RecurringKind;
  category: string | null;
  typical_amount: number;
  typical_day: number; // day of month it usually lands on
  occurrences: number;
  last_seen: string; // YYYY-MM-DD
  active: boolean; // false = not seen recently, so NOT projected
  projected_dates: string[];
}

export interface ForecastBudgetWarning {
  month: string; // YYYY-MM
  category: string;
  projected: number; // month-to-date actual + projected rest of the month
  limit: number;
  over_by: number;
}

export interface ModelScore {
  model_name: string;
  mae: number; // backtest mean absolute error (lower = better)
  folds: number;
}

export interface ForecastRun {
  id: number;
  as_of: string; // YYYY-MM-DD
  horizon_days: number;
  created_at: string | null;
  model_name: string;
  history_months: number;
  low_confidence: boolean;
  opening_balance: number;
  opening_balance_source: OpeningBalanceSource;
  opening_balance_date: string | null;
  income_source: IncomeSource;
  expected_income: number;
  expected_fixed_expenses: number;
  expected_variable_expenses: Band;
  expected_investments: number;
  net_cashflow: number; // income - fixed - variable - investments (point values)
  projected_end_balance: Band;
  cash_crunch_date: string | null; // null = no crunch expected
  cash_crunch_basis: "lower"; // the crunch is read from the LOWER balance band
  summary: string;
  llm_used: boolean;
  explanation_faithful: boolean | null;
  model_scores: ModelScore[];
  recurring_items: RecurringItem[];
  budget_warnings: ForecastBudgetWarning[];
  trajectory: TrajectoryPoint[];
}

export interface ForecastHistoryItem {
  id: number;
  as_of: string;
  horizon_days: number;
  model_name: string;
  low_confidence: boolean;
  net_cashflow: number;
  projected_end_balance: number;
  cash_crunch_date: string | null;
  llm_used: boolean;
  created_at: string | null;
}

export interface RecurringList {
  as_of: string;
  items: RecurringItem[];
}
