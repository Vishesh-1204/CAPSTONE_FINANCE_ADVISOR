// frontend/lib/format.ts
//
// Purpose: one place for number/date formatting so every page shows money and
// dates the same way (Indian grouping: ₹1,23,456.00).

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });

export function formatMoney(value: number | null | undefined): string {
  return value == null ? "—" : inr.format(value);
}

const compactInr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Short rupee amounts for chart axes, Indian style (₹12.5L, ₹1.2Cr). */
export function formatMoneyCompact(value: number): string {
  return compactInr.format(value);
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

/**
 * A date-only API value ("2026-02-16") as "16 Feb" (or "16 Feb 2026" with withYear).
 * Parsed as a LOCAL date: new Date("2026-02-16") would be UTC midnight, which is
 * still the previous day for anyone west of UTC.
 */
export function formatDay(isoDate: string, withYear = false): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

/** "investment_out" -> "Investment out" (for enum-like API values). */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** This month as "YYYY-MM" in the browser's local time (the format the budget API and <input type="month"> use). */
export function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}
