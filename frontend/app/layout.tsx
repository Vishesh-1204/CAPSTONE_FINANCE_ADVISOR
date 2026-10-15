// frontend/app/layout.tsx
//
// Purpose: root layout shared by every page: fonts, global CSS, the nav bar and
// the TooltipProvider that Shadcn tooltips need. Pages render inside <main>.

import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { NavBar } from "@/components/nav-bar";
import { TooltipProvider } from "@/components/ui/tooltip";

import "./globals.css";

// globals.css reads --font-sans / --font-geist-mono, so the variable names must match
const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Finance Advisor · Test Console",
  description: "Testing console for the Expense Intelligence Agent (Phase 3B v3). Not the Phase 12 dashboard.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-background font-sans text-foreground">
        <TooltipProvider>
          <NavBar />
          <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 px-4 py-6">{children}</main>
        </TooltipProvider>
      </body>
    </html>
  );
}
