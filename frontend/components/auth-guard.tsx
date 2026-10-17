// frontend/components/auth-guard.tsx
//
// Purpose: wraps every protected page. With no token it sends you to
// /login?next=<this page>; with a token it renders the page.
// This is only a convenience: the REAL protection is the backend, which
// rejects any request without a valid JWT (401) no matter what the UI does.

"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useToken } from "@/hooks/use-token";

export function AuthGuard({ children }: { children: ReactNode }) {
  const token = useToken();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (token === null) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [token, router, pathname]);

  // undefined = still checking (server render / hydration); null = redirecting
  if (!token) return <p className="p-6 text-sm text-muted-foreground">Checking login…</p>;
  return <>{children}</>;
}
