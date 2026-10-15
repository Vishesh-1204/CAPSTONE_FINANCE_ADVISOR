// frontend/lib/auth.ts
//
// Purpose: keep the JWT for the TEST CONSOLE only.
// localStorage is readable by any script on the page, so a real app should not
// keep tokens there. Phase 12 replaces this with an httpOnly cookie set by the
// backend. Every function guards `typeof window` because Next.js also renders
// pages on the server, where localStorage does not exist.

const TOKEN_KEY = "finance_console_token";

// The browser's "storage" event only fires in OTHER tabs, so we fire our own
// event to tell this tab's components (nav bar, auth guard) that login changed.
export const AUTH_CHANGED_EVENT = "finance-console-auth";

function notify(): void {
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked (private mode, strict settings)
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // ignored: the user will simply have to log in again
  }
  notify();
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignored
  }
  notify();
}
