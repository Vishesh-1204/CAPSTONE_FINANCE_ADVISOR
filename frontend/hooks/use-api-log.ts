// frontend/hooks/use-api-log.ts
//
// Purpose: remembers the last few API calls a page made (success OR failure) so
// the "Raw API response" panel can show exactly what the backend returned.
//
// Usage in a page:
//   const log = useApiLog();
//   const { data } = await log.track(api.listTransactions(query));
// `track` records the call and then returns/throws exactly like the original promise.

"use client";

import { useCallback, useState } from "react";

import { ApiError, type ApiResult, type RawCall } from "@/lib/api";

const MAX_CALLS = 10;

export interface ApiLog {
  calls: RawCall[]; // newest first
  track: <T>(request: Promise<ApiResult<T>>) => Promise<ApiResult<T>>;
  clear: () => void;
}

export function useApiLog(): ApiLog {
  const [calls, setCalls] = useState<RawCall[]>([]);

  const record = useCallback((raw: RawCall) => {
    setCalls((previous) => [raw, ...previous].slice(0, MAX_CALLS));
  }, []);

  const track = useCallback(
    async <T,>(request: Promise<ApiResult<T>>): Promise<ApiResult<T>> => {
      try {
        const result = await request;
        record(result.raw);
        return result;
      } catch (error) {
        if (error instanceof ApiError) record(error.raw);
        throw error;
      }
    },
    [record],
  );

  const clear = useCallback(() => setCalls([]), []);

  return { calls, track, clear };
}
