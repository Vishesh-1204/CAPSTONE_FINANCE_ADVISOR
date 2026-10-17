// frontend/components/nav-bar.tsx
//
// Purpose: top navigation of the test console. Highlights the current page and
// shows Login or Logout depending on whether a token is stored.

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useToken } from "@/hooks/use-token";
import { clearToken } from "@/lib/auth";

const LINKS = [
  { href: "/upload", label: "Upload" },
  { href: "/transactions", label: "Transactions" },
  { href: "/review", label: "Review" },
  { href: "/summary", label: "Summary" },
  { href: "/rules", label: "Rules" },
];

export function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const token = useToken();

  function logout() {
    clearToken();
    router.push("/login");
  }

  return (
    <header className="border-b bg-background">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="font-semibold">
          Finance Advisor <span className="font-normal text-muted-foreground">· Test Console</span>
        </Link>
        <nav className="flex flex-wrap gap-1">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  active ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto">
          {token ? (
            <Button variant="outline" size="sm" onClick={logout}>
              Logout
            </Button>
          ) : token === null ? (
            <Button asChild variant="outline" size="sm">
              <Link href="/login">Login</Link>
            </Button>
          ) : null}
        </div>
      </div>
    </header>
  );
}
