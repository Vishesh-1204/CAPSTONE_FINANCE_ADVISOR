// frontend/hooks/use-api-data.ts
//
// Purpose: "load this data when the page opens, and again whenever `load`
// changes (e.g. new filters) or reload() is called". Every page needs it, so the
// tricky parts live here once:
//   * state is only set inside .then() (React's lint rule forbids synchronous
//     setState inside useEffect: it causes an extra render)
//   * the `ignore` flag drops the answer of an OLD request if a newer one was
//     started (fast filter clicks would otherwise show stale rows)
//   * `loading` is DERIVED (is the data for the current request yet?) instead of
//     being set at the start of the effect
//
// IMPORTANT: wrap `load` in useCallback in the page, otherwise it is a new
// function on every render and the request repeats forever.

"use client";

import { useCallback, useEffect, useState } from "react";

import type { ApiResult } from "@/lib/api";
import type { ApiLog } from "./use-api-log";

interface Loaded<T> {
  data: T | null;
  error: unknown;
  load: unknown; // which `load` function produced this result
  reloadKey: number; // ...and on which reload
}

export interface ApiData<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
  /** Update the shown data locally (e.g. after editing one row) without refetching. */
  setData: (update: (current: T | null) => T | null) => void;
}

export function useApiData<T>(load: () => Promise<ApiResult<T>>, track: ApiLog["track"]): ApiData<T> {
  const [reloadKey, setReloadKey] = useState(0);
  const [loaded, setLoaded] = useState<Loaded<T>>({ data: null, error: null, load: null, reloadKey: -1 });

  useEffect(() => {
    let ignore = false;
    track(load()).then(
      (result) => {
        if (!ignore) setLoaded({ data: result.data, error: null, load, reloadKey });
      },
      (error: unknown) => {
        // keep the previous data on screen and show the error above it
        if (!ignore) setLoaded((previous) => ({ ...previous, error, load, reloadKey }));
      },
    );
    return () => {
      ignore = true;
    };
  }, [load, track, reloadKey]);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);
  const setData = useCallback(
    (update: (current: T | null) => T | null) => setLoaded((previous) => ({ ...previous, data: update(previous.data) })),
    [],
  );

  return {
    data: loaded.data,
    error: loaded.error,
    loading: loaded.load !== load || loaded.reloadKey !== reloadKey,
    reload,
    setData,
  };
}
