// frontend/components/api-error-alert.tsx
//
// Purpose: show any failed API call as a readable red box ("HTTP 422 · This PDF
// looks scanned...") instead of a silent failure or a raw stack trace.

"use client";

import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ApiError } from "@/lib/api";

const STATUS_HINTS: Record<number, string> = {
  400: "Bad request",
  401: "Not logged in",
  403: "Not allowed",
  404: "Not found",
  409: "Conflict",
  413: "File too large",
  422: "Could not process the input",
  500: "Server error (check the backend logs: docker compose logs backend)",
};

export function ApiErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;

  let title = "Something went wrong";
  let message = String(error);
  if (error instanceof ApiError) {
    title = error.status === 0 ? "Network error" : `HTTP ${error.status} · ${STATUS_HINTS[error.status] ?? "Error"}`;
    message = error.message;
  } else if (error instanceof Error) {
    message = error.message;
  }

  return (
    <Alert variant="destructive">
      <CircleAlert />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}
