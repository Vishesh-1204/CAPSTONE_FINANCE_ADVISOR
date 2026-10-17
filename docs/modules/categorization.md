# Module: Categorization agent (Expense Intelligence Agent)

**Files:** `services/categorization_service.py` (orchestrator), `services/rule_based_categorizer.py`,
`services/person_payee_guard.py`, `services/payee_rule_service.py` (matching), `agents/expense_categorizer.py`,
`llm/base.py`, `llm/factory.py`, `llm/gemini_provider.py`, `llm/openai_provider.py`, `core/config.py`

## Why it exists
To label every transaction with a category, explain why, say how sure it is, and send only the hard,
non-personal cases to an LLM. It is the core of Phase 3 and the subject of the research comparison
(rules vs LLM vs hybrid, memory, privacy).

## Where it fits
`POST /api/transactions/categorize` → `categorize_pending_transactions(db, user_id, reset_ai_results)`.
Input: the user's rows with `category IS NULL` and `user_locked = false`. Output: categories written to
those rows and a `CategorizationSummary`.

## How it works: the priority order (defined only in `categorization_service.py`)

| # | Layer | Matches on | Source / confidence |
|---|---|---|---|
| 0 | **Lock** | `user_locked = true` | never touched |
| 1 | **Payee rules** | per row: UPI ID first, then merchant key; only if the rule's category fits the row's direction | `payee_rule`, 1.0 |
| 2 | **Memory** | (direction, `description_key`) of a row the user locked; newest correction wins | `memory`, 1.0 |
| 3a | **Type dictionary** | investments, self-transfers, every credit ([transaction-types.md](transaction-types.md)) | `rule`, 0.95 (unknown credit 0.5) |
| 3b | **Keyword rules** | `rule_based_categorizer.KEYWORDS`, only when exactly one category matches | `rule`, 0.95 |
| 4 | **Person guard** | `looks_like_person` on UPI/IMPS/NEFT rows | `rule`, Transfers, 0.5, always `needs_review`; **never sent to the LLM** |
| 5 | **LLM** | everything left (debits of type expense only) | `llm`, model's confidence |

Rows with the same (direction, `description_key`) are **grouped**, so "monthly house rent" ×3 is decided
once. Debits and credits are never grouped together: a Groww debit (investment) and a Groww credit
(redemption) mean different things.

`needs_review = confidence < CATEGORIZER_REVIEW_THRESHOLD` (0.60). `agent_category` stores the first
suggestion. `_backfill_types` first fills `txn_type` for pre-v3 rows. `reset_ai_results=true` clears all
unlocked categories first.

### The person guard (`person_payee_guard.looks_like_person`)
Only on P2P rails (UPI, IMPS, NEFT). Not a person if a merchant keyword, investment platform or business
word appears, or the UPI ID looks like a merchant (`paytmqr…`, `rzp…`, `…biz` handle). A mobile-number
UPI ID is a person. Otherwise 1–3 plain words is treated as a name. **Design choice:** when unsure, prefer
"person". A false positive costs the user one click; a false negative leaks a name to a third party.

### The LLM layer (`agents/expense_categorizer.py`)
- **What is sent:** per unique group, `{id, description: description_key, amount: median, occurrences}`,
  plus the 16 category definitions and up to 10 of the user's past corrections as few-shot examples
  (cleaned keys, person-like payees removed). Raw narrations, UPI IDs and reference numbers never leave the server.
- **Batching:** 20 descriptions per request (`CATEGORIZER_BATCH_SIZE`), 4 s pause between batches (free tier).
- **Output:** JSON; `parse_batch_response` validates ids and maps labels to the canonical **expense** list
  (a label outside the list becomes "Other" with confidence capped at 0.3, so it lands in the review
  queue; this only happens when the model *answered*, never on a failed call).
- **Retries:** up to 4 attempts with exponential backoff, only for retryable errors (429, quota, timeout,
  503, malformed JSON). A wrong API key is not retried.
- **Fallback chain:** `llm/factory.get_llm_provider_chain()` = `LLM_PROVIDER`, then `LLM_FALLBACK_PROVIDER`.
  A provider that cannot be built (missing key) is skipped.
- **Safe failure:** if every provider fails, the rows stay **uncategorized** (`failed` count + message) and are
  retried next run. After 2 consecutive failed batches the run stops early.

### Provider abstraction (`llm/`)
`LLMProvider` (abstract) → `get_chat_model()` returns a LangChain chat model. `PROVIDER_REGISTRY` maps
`"gemini"` and `"openai"` to classes. No vendor SDK is imported outside `llm/`.

## Configuration (`.env`)
`LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL` (`gemini-2.5-flash-lite`),
`OPENAI_API_KEY`, `OPENAI_MODEL` (`gpt-4o-mini`), `LLM_TEMPERATURE` (0.0), `CATEGORIZER_BATCH_SIZE`,
`CATEGORIZER_BATCH_DELAY_SECONDS`, `CATEGORIZER_REVIEW_THRESHOLD`, `CATEGORIZER_FEW_SHOT_LIMIT`, and the
ablation switches `CATEGORIZER_ENABLE_MEMORY`, `_RULES`, `_PAYEE_RULES`, `_PERSON_GUARD` (all true).

## Common mistakes
- Labelling a failed LLM row "Other": it would never be retried and would pollute metrics.
- Sending `transaction.description` to the LLM instead of `description_key` (privacy).
- Adding an ambiguous brand to `KEYWORDS` (Amazon, Paytm). Rules favour precision; the LLM handles ambiguity.
- Putting priority logic anywhere other than `categorization_service.py`.

## How to test
- `tests/test_categorization_service.py` (19): priority order, grouping, memory, failure leaves rows empty (fake agent).
- `tests/test_expense_categorizer.py` (14): prompt/JSON parsing, retries, fallback chain (fake chains, no network).
- `tests/test_person_payee_guard.py` (14): asserts no name/phone/UPI/ref string reaches the fake LLM.
- `tests/test_rule_based_categorizer.py` (16).
- Manual: upload a fixture, `POST /categorize` with no API key → rules/memory/guard still work and the rest is
  reported as `failed` with a clear message; with a key → `categorized_by_llm > 0`.

## Known limitations
- Person-guard recall 0.857 on the labeled set: 1 person-like string reached the LLM stage in the offline run.
- Only Gemini and OpenAI providers exist; HuggingFace/Llama is approved but not implemented yet.
- Synchronous; Phase 9 (Celery) moves long runs to the background.

## Connects to
[overrides-and-review.md](overrides-and-review.md) (user corrections feed memory and payee rules) and [evaluation.md](evaluation.md) (ablations of each layer).
