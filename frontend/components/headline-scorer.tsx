// frontend/components/headline-scorer.tsx
//
// Purpose: section 7 of /stocks. Type one headline and see its positive / negative / neutral
// probabilities from the word-list scorer (lexicon) and, when the backend has it installed, FinBERT,
// side by side (POST /api/stocks/sentiment/score with `scorer`). Nothing is stored. Good for demos:
// try "Profit surges on record orders" and "Profit did not fall this quarter" (the lexicon's negation
// rule and FinBERT disagree on the second one).

"use client";

import { Gauge } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import type { ApiLog } from "@/hooks/use-api-log";
import { api } from "@/lib/api";
import { formatPercent, humanize } from "@/lib/format";
import { warningText } from "@/lib/stocks";
import type { ScorerName, SentimentScoreResponse } from "@/lib/types";

const MAX_HEADLINE_LENGTH = 500; // same as the backend
const SCORER_LABELS: Record<ScorerName, string> = { lexicon: "Word list (lexicon)", finbert: "FinBERT" };

function Probabilities({ requested, result }: { requested: ScorerName; result: SentimentScoreResponse }) {
  const rows: [string, number][] = [
    ["Positive", result.positive],
    ["Negative", result.negative],
    ["Neutral", result.neutral],
  ];
  const fellBack = requested !== result.scorer;
  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{SCORER_LABELS[requested]}</span>
        <Badge variant="secondary">{humanize(result.label)}</Badge>
      </div>
      {fellBack ? (
        <p className="text-sm text-muted-foreground">{warningText("finbert_unavailable")}</p>
      ) : (
        rows.map(([label, value]) => (
          <div key={label} className="space-y-1">
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">{label}</span>
              <span className="font-mono tabular-nums">{formatPercent(value)}</span>
            </div>
            <Progress value={value * 100} aria-label={`${label} probability`} />
          </div>
        ))
      )}
    </div>
  );
}

export function HeadlineScorer({ finbertAvailable, track }: { finbertAvailable: boolean; track: ApiLog["track"] }) {
  const [headline, setHeadline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [results, setResults] = useState<[ScorerName, SentimentScoreResponse][]>([]);

  async function score(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const text = headline.trim();
    if (!text) {
      setError(new Error("Type a headline first."));
      return;
    }
    const scorers: ScorerName[] = finbertAvailable ? ["lexicon", "finbert"] : ["lexicon"];
    setBusy(true);
    try {
      const answers = await Promise.all(scorers.map((name) => track(api.scoreHeadline(text, name))));
      setResults(answers.map((answer, index) => [scorers[index], answer.data]));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>7. Headline scorer</CardTitle>
        <CardDescription>
          Score any headline (not stored). {finbertAvailable
            ? "FinBERT is installed on this backend, so both scorers are shown."
            : "FinBERT is not installed on this backend (start the ml profile on port 8001 and point NEXT_PUBLIC_API_URL at it to compare), so only the word list is shown."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={score} className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1 space-y-2">
            <Label htmlFor="headline">Headline</Label>
            <Input
              id="headline"
              autoComplete="off"
              maxLength={MAX_HEADLINE_LENGTH}
              placeholder="Profit surges on record orders"
              value={headline}
              onChange={(event) => setHeadline(event.target.value)}
              disabled={busy}
            />
          </div>
          <Button type="submit" disabled={busy} className="gap-2">
            <Gauge className="size-4" />
            {busy ? "Scoring…" : "Score"}
          </Button>
        </form>
        <ApiErrorAlert error={error} />
        {results.length > 0 && (
          <div className="grid gap-3 md:grid-cols-2">
            {results.map(([requested, result]) => (
              <Probabilities key={requested} requested={requested} result={result} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
