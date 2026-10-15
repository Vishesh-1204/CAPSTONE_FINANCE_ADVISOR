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
