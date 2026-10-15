// frontend/components/raw-response-panel.tsx
//
// Purpose: the testing aid at the bottom of every page. A collapsible panel
// listing the page's recent API calls (newest first): method, URL, HTTP status,
// time taken and the exact JSON body. Compare it with Swagger when debugging.

"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { RawCall } from "@/lib/api";

function statusClass(status: number): string {
  if (status >= 200 && status < 300) return "text-emerald-700 dark:text-emerald-400";
  return "text-destructive";
}

export function RawResponsePanel({ calls, onClear }: { calls: RawCall[]; onClear?: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border bg-muted/30">
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="gap-2">
            <ChevronDown className={`size-4 transition-transform ${open ? "" : "-rotate-90"}`} />
            Raw API response
            <span className="text-muted-foreground">({calls.length})</span>
          </Button>
        </CollapsibleTrigger>
        {onClear && calls.length > 0 && (
          <Button variant="ghost" size="sm" onClick={onClear}>
            Clear
          </Button>
        )}
      </div>
      <CollapsibleContent className="space-y-3 px-3 pb-3">
        {calls.length === 0 && <p className="text-sm text-muted-foreground">No API calls yet on this page.</p>}
        {calls.map((call, index) => (
          <div key={`${call.url}-${index}`} className="rounded-md border bg-background">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-2 font-mono text-xs">
              <span className="font-semibold">{call.method}</span>
              <span className="break-all text-muted-foreground">{call.url}</span>
              <span className={`font-semibold ${statusClass(call.status)}`}>
                {call.status === 0 ? "NETWORK ERROR" : call.status}
              </span>
              <span className="text-muted-foreground">{call.durationMs} ms</span>
            </div>
            <pre className="max-h-80 overflow-auto p-3 font-mono text-xs leading-relaxed">
              {call.body === null || call.body === "" ? "(empty body)" : JSON.stringify(call.body, null, 2)}
            </pre>
          </div>
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}
