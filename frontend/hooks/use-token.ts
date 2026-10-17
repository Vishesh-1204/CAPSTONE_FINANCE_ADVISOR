// frontend/hooks/use-token.ts
//
// Purpose: React hook that returns the current login token and re-renders when
// it changes (login, logout, another tab).
// Returns `undefined` while rendering on the server / during hydration (we do
// not know yet), `null` when logged out, and the token string when logged in.
// useSyncExternalStore is React's built-in way to read outside state like localStorage.

"use client";

import { useSyncExternalStore } from "react";

import { AUTH_CHANGED_EVENT, getToken } from "@/lib/auth";

function subscribe(onChange: () => void): () => void {
  window.addEventListener(AUTH_CHANGED_EVENT, onChange);
  window.addEventListener("storage", onChange); // other tabs
  return () => {
    window.removeEventListener(AUTH_CHANGED_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function useToken(): string | null | undefined {
  return useSyncExternalStore(subscribe, getToken, () => undefined);
}
