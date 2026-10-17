// frontend/app/page.tsx
//
// Purpose: console home = connection check. Calls GET /health (no login needed)
// so you can see at a glance whether the frontend can reach the backend
// (backend running? CORS allows this origin? API URL right?), then links onward.

"use client";

import { CircleCheck, CircleX, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { useToken } from "@/hooks/use-token";
import { API_URL, apiFetch } from "@/lib/api";

interface Health {
  status: string;
  project: string;
}

export default function HomePage() {
  const log = useApiLog();
  const token = useToken();
  const loadHealth = useCallback(() => apiFetch<Health>("/health", { auth: false }), []);
  const health = useApiData(loadHealth, log.track);
  // after a failed check the old "connected" data must not be shown
  const connected = health.data && !health.error ? health.data : null;

  return (
    <>
      <PageHeader
        title="Test Console"
        description="A small tool for exercising the Phase 3B backend from a browser. Synthetic data only."
      />

      <Card>
        <CardHeader>
          <CardTitle>Backend connection</CardTitle>
          <CardDescription className="font-mono">{API_URL}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            {health.loading ? (
              <span className="text-muted-foreground">Checking…</span>
            ) : connected ? (
              <>
                <CircleCheck className="size-5 text-emerald-600" />
                <span>
                  Connected: <strong>{connected.project}</strong> reports “{connected.status}”.
                </span>
              </>
            ) : (
              <>
                <CircleX className="size-5 text-destructive" />
                <span>Not connected.</span>
              </>
            )}
            <Button
              variant="outline"
              size="sm"
              className="ml-auto gap-2"
              onClick={health.reload}
              disabled={health.loading}
            >
              <RefreshCw className="size-4" /> Check again
            </Button>
          </div>
          <ApiErrorAlert error={health.error} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Next step</CardTitle>
          <CardDescription>
            {token ? "You are logged in." : "Log in (or register a synthetic test user) to use the console."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link href={token ? "/upload" : "/login"}>{token ? "Upload a statement" : "Go to login"}</Link>
          </Button>
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
