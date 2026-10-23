// frontend/app/(console)/rules/page.tsx
//
// Purpose: /rules — your payee rules (GET /api/payee-rules/). A rule says "every
// payment to THIS payee is THIS category". It is checked FIRST by the categorizer
// (before memory, keyword rules and the LLM), so it is the cheapest and most
// reliable way to fix a payee for good.
//
// Rules are created from a correction ("Remember this payee" in the transaction
// editor), never here: there is deliberately no POST endpoint. Deleting a rule
// (DELETE /api/payee-rules/{id}) only affects FUTURE categorization; rows that
// were already categorized keep their category. Synthetic data only.

"use client";

import { Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { PayeeRule } from "@/lib/types";

const MATCH_LABELS: Record<string, string> = {
  upi_id: "UPI ID",
  merchant_key: "merchant name",
};

export default function RulesPage() {
  const log = useApiLog();
  const loadRules = useCallback(() => api.listPayeeRules(), []);
  const rules = useApiData(loadRules, log.track);

  // Two-step delete: the first click arms the row, the second deletes
  const [armedId, setArmedId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function remove(rule: PayeeRule) {
    setBusyId(rule.id);
    setError(null);
    setNotice(null);
    try {
      await log.track(api.deletePayeeRule(rule.id));
      rules.setData((current) => current?.filter((item) => item.id !== rule.id) ?? current);
      setNotice(`Deleted the rule for “${rule.match_value}”. Rows it already categorized keep “${rule.category}”.`);
    } catch (err) {
      setError(err);
    } finally {
      setBusyId(null);
      setArmedId(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Payee rules"
        description="“Every payment to this payee is this category.” Checked first by the categorizer, before memory, keyword rules and the LLM."
      />

      <Card>
        <CardHeader>
          <CardTitle>Your rules</CardTitle>
          <CardDescription>
            Create a rule by correcting a transaction on{" "}
            <Link href="/transactions" className="underline underline-offset-2">
              Transactions
            </Link>{" "}
            or{" "}
            <Link href="/review" className="underline underline-offset-2">
              Review
            </Link>{" "}
            with “Remember this payee” ticked. Deleting a rule only changes future categorization.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ApiErrorAlert error={rules.error} />
          <ApiErrorAlert error={error} />
          {notice && (
            <Alert>
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          )}
          {!rules.data ? (
            <p className="text-sm text-muted-foreground">{rules.loading ? "Loading…" : "Could not load rules."}</p>
          ) : rules.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No payee rules yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Matches</TableHead>
                  <TableHead>Payee</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">
                    <span className="sr-only">Delete</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.data.map((rule) => {
                  const armed = armedId === rule.id;
                  const busy = busyId === rule.id;
                  return (
                    <TableRow key={rule.id}>
                      <TableCell>
                        <Badge variant="outline">{MATCH_LABELS[rule.match_type] ?? rule.match_type}</Badge>
                      </TableCell>
                      <TableCell className="max-w-72 truncate font-mono text-xs" title={rule.match_value}>
                        {rule.match_value}
                      </TableCell>
                      <TableCell className="font-medium">{rule.category}</TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDateTime(rule.created_at)}
                      </TableCell>
                      <TableCell className="text-right whitespace-nowrap">
                        {armed ? (
                          <div className="flex justify-end gap-1">
                            <Button size="sm" variant="destructive" onClick={() => remove(rule)} disabled={busy}>
                              {busy ? "Deleting…" : "Confirm delete"}
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setArmedId(null)} disabled={busy}>
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="gap-1.5"
                            onClick={() => setArmedId(rule.id)}
                            disabled={busyId !== null}
                          >
                            <Trash2 className="size-3.5" /> Delete
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}
