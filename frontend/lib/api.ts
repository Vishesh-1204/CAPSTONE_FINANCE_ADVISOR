// frontend/lib/api.ts
//
// Purpose: the ONE place that talks to the FastAPI backend.
// - builds URLs from NEXT_PUBLIC_API_URL
// - attaches the JWT ("Authorization: Bearer ...")
// - turns any error into an ApiError with a readable message
// - on 401 (expired/invalid token) clears the token (AuthGuard then shows /login)
// - records every call (method, URL, status, body, time) so pages can show it
//   in the "Raw API response" panel
// Pages never call fetch() directly; they use the named functions at the bottom.

import { clearToken, getToken } from "./auth";
import type {
  BulkCategoryResponse,
  CategorizationSummary,
  CategoryCorrection,
  CategoryOptions,
  CorrectionResponse,
  ImportBatch,
  MonthlySummary,
  PayeeRule,
  StatementImportSummary,
  Token,
  Transaction,
  TransactionPage,
  TransactionQuery,
  UserResponse,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

/** Everything about one HTTP call, shown in the Raw API response panel. */
export interface RawCall {
  method: string;
  url: string;
  status: number; // 0 = the request never reached the server
  durationMs: number;
  body: unknown;
}

export interface ApiResult<T> {
  data: T;
  raw: RawCall;
}

/** Thrown for any failed call. `message` is safe to show to the user. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public raw: RawCall,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryValue = string | number | boolean | null | undefined;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  json?: unknown; // sent as application/json
  form?: FormData; // multipart (file uploads)
  urlEncoded?: Record<string, string>; // application/x-www-form-urlencoded (OAuth2 login)
  auth?: boolean; // attach the token (default true)
  redirectOn401?: boolean; // default true; false for the login call itself
}

/**
 * FastAPI errors look like {"detail": "text"} or, for validation errors (422),
 * {"detail": [{"loc": ["body", "category"], "msg": "..."}]}. Flatten both.
 */
function errorMessage(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { loc?: unknown[]; msg?: string }) => {
        const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .join("; ");
  }
  if (typeof body === "string" && body) return body;
  return `Request failed with HTTP ${status}.`;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(API_URL + path);
  for (const [key, value] of Object.entries(query ?? {})) {
    // skip empty filters so the backend uses its defaults
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { method = "GET", query, json, form, urlEncoded, auth = true, redirectOn401 = true } = options;
  const url = buildUrl(path, query);
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;

  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (urlEncoded) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(urlEncoded);
  } else if (form) {
    // Do NOT set Content-Type here: the browser adds "multipart/form-data; boundary=..." itself.
    body = form;
  }

  const token = auth ? getToken() : null;
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const started = performance.now();
  let response: Response;
  try {
    response = await fetch(url, { method, headers, body });
  } catch {
    // fetch only throws when there is no HTTP response at all
    const raw: RawCall = { method, url, status: 0, durationMs: Math.round(performance.now() - started), body: null };
    throw new ApiError(
      0,
      `Could not reach the backend at ${API_URL}. Is "docker compose up" running, and is this page opened at ` +
        `http://localhost:3000 (the origin allowed by CORS_ORIGINS)?`,
      raw,
    );
  }

  // 204 No Content (e.g. DELETE) has no body
  const text = response.status === 204 ? "" : await response.text();
  let parsed: unknown = text;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    // not JSON (e.g. a plain-text 500 page): keep the text
  }

  const raw: RawCall = {
    method,
    url,
    status: response.status,
    durationMs: Math.round(performance.now() - started),
    body: parsed,
  };

  if (!response.ok) {
    if (response.status === 401 && redirectOn401) {
      // Clearing the token is enough: AuthGuard sees "logged out" and redirects
      // to /login?next=<this page> with the Next.js router.
      clearToken();
    }
    throw new ApiError(response.status, errorMessage(response.status, parsed), raw);
  }
  return { data: parsed as T, raw };
}

// ---------------------------------------------------------------------------
// Named endpoint functions (one per backend route, see spec section 8)
// ---------------------------------------------------------------------------

export const api = {
  // auth
  register: (email: string, full_name: string, password: string) =>
    apiFetch<UserResponse>("/api/auth/register", {
      method: "POST",
      json: { email, full_name, password },
      auth: false,
      redirectOn401: false,
    }),
  // OAuth2 password flow: form-encoded, and the email goes in the "username" field
  login: (email: string, password: string) =>
    apiFetch<Token>("/api/auth/login", {
      method: "POST",
      urlEncoded: { username: email, password },
      auth: false,
      redirectOn401: false,
    }),

  // statements
  uploadStatement: (file: File, password?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (password) form.append("password", password);
    return apiFetch<StatementImportSummary>("/api/statements/upload", { method: "POST", form });
  },
  listBatches: () => apiFetch<ImportBatch[]>("/api/import-batches"),

  // transactions
  listTransactions: (query: TransactionQuery) =>
    apiFetch<TransactionPage>("/api/transactions/", { query: { ...query } }),
  categorize: (resetAiResults = false) =>
    apiFetch<CategorizationSummary>("/api/transactions/categorize", {
      method: "POST",
      query: { reset_ai_results: resetAiResults || undefined },
    }),
  correctCategory: (id: number, correction: CategoryCorrection) =>
    apiFetch<CorrectionResponse>(`/api/transactions/${id}/category`, { method: "PATCH", json: correction }),
  unlockTransaction: (id: number) =>
    apiFetch<Transaction>(`/api/transactions/${id}/unlock`, { method: "POST" }),
  bulkCategory: (transactionIds: number[], category: string) =>
    apiFetch<BulkCategoryResponse>("/api/transactions/bulk-category", {
      method: "POST",
      json: { transaction_ids: transactionIds, category },
    }),
  getSummary: (month?: string) => apiFetch<MonthlySummary>("/api/transactions/summary", { query: { month } }),

  // payee rules
  listPayeeRules: () => apiFetch<PayeeRule[]>("/api/payee-rules/"),
  deletePayeeRule: (id: number) => apiFetch<null>(`/api/payee-rules/${id}`, { method: "DELETE" }),

  // categories
  getCategories: () => apiFetch<CategoryOptions>("/api/categories/"),
};
