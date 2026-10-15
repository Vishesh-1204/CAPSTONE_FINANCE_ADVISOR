// frontend/app/login/page.tsx
//
// Purpose: /login — log in, or register a SYNTHETIC test user and log in
// straight away. On success the JWT is stored (lib/auth.ts, test console only)
// and the user goes back to the page they came from (?next=/transactions).
// This page is outside the (console) route group, so it needs no login itself.

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiLog } from "@/hooks/use-api-log";
import { useToken } from "@/hooks/use-token";
import { api } from "@/lib/api";
import { clearToken, setToken } from "@/lib/auth";

const DEFAULT_NEXT = "/upload";

/**
 * Only allow redirects to OUR pages. "/transactions" is fine; "//evil.com" or
 * "https://evil.com" would send the user to another site after login (an
 * "open redirect", a classic phishing trick), so those fall back to the default.
 */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\") || raw.startsWith("/login")) {
    return DEFAULT_NEXT;
  }
  return raw;
}

export default function LoginPage() {
  // useSearchParams (inside LoginPanel) must sit under a Suspense boundary in Next.js 16
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <LoginPanel />
    </Suspense>
  );
}

function LoginPanel() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const token = useToken();
  const log = useApiLog();
  const [tab, setTab] = useState("login");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  /** Logs in, stores the token and leaves the page. Shared by both tabs. */
  async function logInAndGo(email: string, password: string) {
    const { data } = await log.track(api.login(email, password));
    setToken(data.access_token);
    router.replace(next);
  }

  async function handleLogin(email: string, password: string) {
    setBusy(true);
    setError(null);
    try {
      await logInAndGo(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleRegister(email: string, fullName: string, password: string) {
    setBusy(true);
    setError(null);
    try {
      await log.track(api.register(email, fullName, password));
      await logInAndGo(email, password); // register does not return a token, so log in right after
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-md space-y-6">
      {token ? (
        <Card>
          <CardHeader>
            <CardTitle>You are logged in</CardTitle>
            <CardDescription>The token is stored in this browser (test console only).</CardDescription>
          </CardHeader>
          <CardFooter className="gap-2">
            <Button asChild>
              <Link href={next}>Continue</Link>
            </Button>
            <Button variant="outline" onClick={clearToken}>
              Log out
            </Button>
          </CardFooter>
        </Card>
      ) : (
        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value);
            setError(null); // an error from the other tab would be confusing here
          }}
        >
          <TabsList className="w-full">
            <TabsTrigger value="login">Login</TabsTrigger>
            <TabsTrigger value="register">Register</TabsTrigger>
          </TabsList>
          <TabsContent value="login">
            <LoginForm busy={busy} onSubmit={handleLogin} />
          </TabsContent>
          <TabsContent value="register">
            <RegisterForm busy={busy} onSubmit={handleRegister} />
          </TabsContent>
        </Tabs>
      )}

      <ApiErrorAlert error={error} />
      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </div>
  );
}

/** Reads a named field from a submitted form. */
function field(form: HTMLFormElement, name: string): string {
  return String(new FormData(form).get(name) ?? "").trim();
}

function LoginForm({ busy, onSubmit }: { busy: boolean; onSubmit: (email: string, password: string) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); // stop the browser's full-page form post; we call the API ourselves
    onSubmit(field(event.currentTarget, "email"), field(event.currentTarget, "password"));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Log in</CardTitle>
        <CardDescription>Use the email and password of a test user.</CardDescription>
      </CardHeader>
      <form onSubmit={submit}>
        <CardContent className="space-y-4">
          <EmailField />
          <PasswordField autoComplete="current-password" />
        </CardContent>
        <CardFooter className="mt-6">
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Logging in…" : "Log in"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function RegisterForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (email: string, fullName: string, password: string) => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    onSubmit(field(form, "email"), field(form, "full_name"), field(form, "password"));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Register</CardTitle>
        <CardDescription>
          Create a test user with made-up details (e.g. test1@example.com). You are logged in right after.
        </CardDescription>
      </CardHeader>
      <form onSubmit={submit}>
        <CardContent className="space-y-4">
          <EmailField />
          <div className="space-y-2">
            <Label htmlFor="full_name">Full name</Label>
            <Input id="full_name" name="full_name" autoComplete="off" placeholder="Test User" required />
          </div>
          <PasswordField autoComplete="new-password" />
        </CardContent>
        <CardFooter className="mt-6">
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Creating account…" : "Register and log in"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function EmailField() {
  return (
    <div className="space-y-2">
      <Label htmlFor="email">Email</Label>
      <Input id="email" name="email" type="email" autoComplete="email" placeholder="test1@example.com" required />
    </div>
  );
}

function PasswordField({ autoComplete }: { autoComplete: string }) {
  return (
    <div className="space-y-2">
      <Label htmlFor="password">Password</Label>
      <Input id="password" name="password" type="password" autoComplete={autoComplete} required />
    </div>
  );
}
