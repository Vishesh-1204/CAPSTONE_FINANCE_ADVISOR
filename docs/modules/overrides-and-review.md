# Module: Manual override, payee rules & review queue

**Files:** `services/category_override_service.py`, `services/payee_rule_service.py`,
`services/transaction_service.py` (status filters), `api/routes/transactions.py`, `api/routes/payee_rules.py`,
`models/payee_rule.py`

## Why it exists
The agent will be wrong sometimes. The user must be able to fix a category, trust that the agent will
never overwrite the fix, and teach the agent so the same mistake does not repeat. Every fix is also
the **feedback signal** for the override-rate metric.

## How it works

### Correct one row: `PATCH /api/transactions/{id}/category` (`apply_user_correction`)
1. Load the row **for this user** (else 404); check the category fits the direction (else 422).
2. Always: `category`, `category_source='user'`, confidence 1.0, `user_locked=true`, `needs_review=false`,
   `txn_type` synced to the category. `agent_category` keeps the agent's original suggestion.
3. `apply_to_similar=true`: also update the user's other **unlocked** rows with the same `payee_key`
   and direction (rows from before `payee_key` existed fall back to `description_key`). They get source
   `memory` and are **not** locked, so a later reset can still redo them.
4. `remember_payee=true`: `upsert_rule` keyed on the **UPI ID** if the narration has one, otherwise the
   merchant key. Never on a name alone. One rule per payee (unique constraint), so it updates.

A locked row automatically becomes **memory** for future runs, and a few-shot example for the LLM.

### Many rows: `POST /api/transactions/bulk-category`
Sets and locks the category on the listed ids. Ids that are not yours → `not_found_ids`; rows the
category does not fit → `skipped_incompatible_ids`. The rest succeed.

### Unlock: `POST /api/transactions/{id}/unlock`
Clears the lock only; the category stays until `/categorize?reset_ai_results=true` redoes it.

### Review queue
`GET /api/transactions/?status=needs_review` = `needs_review AND NOT user_locked`, least confident first.
Rows land there when confidence < 0.60, or when the person guard labelled them Transfers.

### Payee rules
`GET /api/payee-rules/`, `DELETE /api/payee-rules/{id}`. Deleting a rule does not change rows already categorized.
At categorization time rules are layer 1 (right after the lock), and only apply when their category fits
the row's direction (a restaurant rule never labels the restaurant's refund).

## Memory vs payee rule vs apply-to-similar

| | Scope | Key | Opt-in? |
|---|---|---|---|
| Memory | future runs | (direction, description_key) of locked rows | automatic |
| Payee rule | future runs, higher priority | UPI ID, else merchant key | yes (`remember_payee`) |
| Apply to similar | existing unlocked rows, now | payee_key | yes (`apply_to_similar`) |

Why UPI ID first: two people can share a first name ("rahul"), never a UPI ID.

## Common mistakes
- Expecting `apply_to_similar` to touch locked rows. It never overwrites a user decision.
- Using bulk for rows of mixed direction with a debit-only label (they are reported as incompatible, not changed).
- Forgetting that unlock does not recategorize. Run categorize with `reset_ai_results=true`.

## How to test
- `tests/test_category_overrides.py` (14): locks, `agent_category`, apply-to-similar by payee, payee-rule priority, unlock, bulk, direction checks.
- `tests/test_transactions_api_v3.py` (8): the same through HTTP, plus filters and paging.
- Manual (Swagger): categorize → PATCH a row with both flags → `similar_updated` > 0 and a `payee_rule` returned →
  upload a statement with the same payee → categorize → that row has source `payee_rule`.

## Research hook
Override rate = share of rows whose final category differs from `agent_category`, overall and per
category (`evaluation/metrics.py:override_rate`). Offline `full` configuration: 0.061.

## Connects to
[categorization.md](categorization.md) (layers 0–2 read what this module writes), [evaluation.md](evaluation.md), and the Test Console `/transactions`, `/review`, `/rules` pages.
