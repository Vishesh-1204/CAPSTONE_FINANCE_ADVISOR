// frontend/app/(console)/layout.tsx
//
// Purpose: every page inside the (console) folder needs a login. The brackets
// make this a "route group": it groups pages under one layout WITHOUT adding
// "/console" to the URL, so app/(console)/upload/page.tsx is still /upload.

import { AuthGuard } from "@/components/auth-guard";

export default function ConsoleLayout({ children }: LayoutProps<"/">) {
  return <AuthGuard>{children}</AuthGuard>;
}
