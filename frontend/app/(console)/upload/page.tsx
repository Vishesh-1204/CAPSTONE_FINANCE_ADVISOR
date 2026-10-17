// frontend/app/(console)/upload/page.tsx
//
// Purpose: /upload — import a CSV/XLSX/PDF bank statement
// (POST /api/statements/upload), see what the import did (counts, transaction
// types, balance check), run the categorizer, and list past uploads
// (GET /api/import-batches). Synthetic statements only.

"use client";

import { useCallback, useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { FileDropzone } from "@/components/file-dropzone";
import { ImportSummaryCard } from "@/components/import-summary-card";
import { PageHeader } from "@/components/page-header";
import { RawResponsePanel } from "@/components/raw-response-panel";
import { RunCategorizerButton } from "@/components/run-categorizer-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiData } from "@/hooks/use-api-data";
import { useApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ImportBatch, StatementImportSummary } from "@/lib/types";

export default function UploadPage() {
  const log = useApiLog();
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<StatementImportSummary | null>(null);
  const [error, setError] = useState<unknown>(null);

  const loadBatches = useCallback(() => api.listBatches(), []);
  const batches = useApiData(loadBatches, log.track);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      const { data } = await log.track(api.uploadStatement(file, password || undefined));
      setSummary(data);
      setPassword(""); // do not keep the PDF password around longer than needed
      batches.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const isPdf = file?.name.toLowerCase().endsWith(".pdf") ?? false;

  return (
    <>
      <PageHeader
        title="Upload a statement"
        description="Parsing happens on the server, locally: the file is never sent to an LLM. Debits and credits are imported; duplicates are skipped."
      />

      <Card>
        <CardHeader>
          <CardTitle>1. Choose a file</CardTitle>
          <CardDescription>
            Test files live in <code className="text-xs">backend/tests/fixtures/statements/</code> (the protected PDF’s
            password is <code className="text-xs">test1234</code>).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={upload} className="space-y-4">
            <FileDropzone
              file={file}
              onFileChange={(chosen) => {
                setFile(chosen);
                setError(null);
              }}
              disabled={busy}
            />
            <div className="max-w-sm space-y-2">
              <Label htmlFor="pdf-password">PDF password (optional)</Label>
              <Input
                id="pdf-password"
                type="password"
                autoComplete="off"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
                placeholder={isPdf ? "Only if the PDF is protected" : "Only for password-protected PDFs"}
              />
              <p className="text-xs text-muted-foreground">Used once for this upload. Never stored or logged.</p>
            </div>
            <Button type="submit" disabled={!file || busy}>
              {busy ? "Uploading…" : "Upload"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <ApiErrorAlert error={error} />
      {summary && <ImportSummaryCard summary={summary} />}

      <Card>
        <CardHeader>
          <CardTitle>2. Categorize</CardTitle>
          <CardDescription>
            Runs over every uncategorized, unlocked transaction: payee rules → memory → rules → person guard → LLM.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RunCategorizerButton track={log.track} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Past uploads</CardTitle>
          <CardDescription>Newest first (import batches).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ApiErrorAlert error={batches.error} />
          <BatchTable batches={batches.data} loading={batches.loading} />
        </CardContent>
      </Card>

      <RawResponsePanel calls={log.calls} onClear={log.clear} />
    </>
  );
}

function BalanceCell({ batch }: { batch: ImportBatch }) {
  if (!batch.balance_checked) return <span className="text-muted-foreground">—</span>;
  return batch.balance_mismatches === 0 ? (
    <Badge variant="secondary">✓ ok</Badge>
  ) : (
    <Badge variant="destructive">✗ {batch.balance_mismatches}</Badge>
  );
}

function BatchTable({ batches, loading }: { batches: ImportBatch[] | null; loading: boolean }) {
  if (!batches) return <p className="text-sm text-muted-foreground">{loading ? "Loading…" : "Could not load uploads."}</p>;
  if (batches.length === 0) return <p className="text-sm text-muted-foreground">No uploads yet.</p>;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>#</TableHead>
          <TableHead>File</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Layout</TableHead>
          <TableHead className="text-right">Found</TableHead>
          <TableHead className="text-right">Imported</TableHead>
          <TableHead className="text-right">Duplicates</TableHead>
          <TableHead className="text-right">Invalid</TableHead>
          <TableHead>Balance</TableHead>
          <TableHead>Uploaded</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {batches.map((batch) => (
          <TableRow key={batch.id}>
            <TableCell className="text-muted-foreground">{batch.id}</TableCell>
            <TableCell className="max-w-56 truncate font-medium">{batch.filename}</TableCell>
            <TableCell>{batch.source_type.toUpperCase()}</TableCell>
            <TableCell className="text-muted-foreground">{batch.layout ?? "—"}</TableCell>
            <TableCell className="text-right tabular-nums">{batch.rows_total}</TableCell>
            <TableCell className="text-right tabular-nums">{batch.rows_imported}</TableCell>
            <TableCell className="text-right tabular-nums">{batch.rows_duplicate}</TableCell>
            <TableCell className="text-right tabular-nums">{batch.rows_invalid}</TableCell>
            <TableCell>
              <BalanceCell batch={batch} />
            </TableCell>
            <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(batch.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
