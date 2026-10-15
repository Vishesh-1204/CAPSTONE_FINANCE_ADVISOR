# app/agents/expense_categorizer.py
#
# Purpose: the LLM layer of the Expense Intelligence Agent (v2).
#
# What changed from v1:
#   * BATCH: one request classifies up to 20 descriptions (v1: one request each).
#   * CONTEXT: the prompt includes category definitions, the user's own past
#     corrections as examples, and how often each description repeats.
#   * SAFE FAILURE: a failed call returns NO result (so the row is retried
#     later) instead of silently labelling it "Other".
#   * SMART RETRIES: retries rate-limit/network errors, not a wrong API key.
#
# This file knows nothing about the database or HTTP.

import json
import logging
from dataclasses import dataclass

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.categories import CATEGORY_GUIDE, CATEGORY_NAMES, FALLBACK_CATEGORY, canonical_category
from app.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)

# Backward-compatible alias for code written against v1
EXPENSE_CATEGORIES = CATEGORY_NAMES


@dataclass(frozen=True)
class PendingItem:
    """One unique description to classify. `item_id` is only meaningful inside one batch."""
    item_id: int
    description: str
    amount: float
    occurrences: int = 1     # how many transactions share this description


@dataclass(frozen=True)
class CategorizationResult:
    """The decision for one description, and who made it."""
    category: str
    confidence: float        # 0.0 - 1.0
    reasoning: str
    source: str              # 'rule' | 'memory' | 'llm' | 'user'


class MalformedLLMOutputError(Exception):
    """The model answered, but not with the JSON we asked for."""


# Error text that means "try again later". A wrong API key is NOT in this list:
# retrying a wrong key just wastes a minute.
_RETRYABLE_HINTS = (
    "429", "rate limit", "rate_limit", "quota", "resource_exhausted", "resource exhausted",
    "unavailable", "503", "timeout", "timed out", "deadline", "overloaded", "connection",
)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, MalformedLLMOutputError):
        return True
    message = str(exc).lower()
    return any(hint in message for hint in _RETRYABLE_HINTS)


def _category_guide_text() -> str:
    return "\n".join(f"- {name}: {meaning}" for name, meaning in CATEGORY_GUIDE.items())


BATCH_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a transaction classifier for a personal finance app used in India. Amounts are in INR.\n"
        "Classify every transaction into EXACTLY ONE of these categories:\n"
        "{category_guide}\n\n"
        "Rules:\n"
        "- Use the category names exactly as written. Never invent a category.\n"
        "- Bank descriptions are noisy (UPI references, codes). Look for the merchant or purpose.\n"
        "- 'occurrences' tells you how often the user has this same description; a repeating "
        "fixed amount is often rent, a subscription or an EMI.\n"
        "- The user's confirmed examples are ground truth. Follow them for similar descriptions.\n"
        "- If you genuinely cannot tell, use 'Other' with confidence below 0.5.\n"
        "- confidence is a number from 0.0 to 1.0. reasoning is at most 15 words.\n"
        "- Return one result per input id.\n"
        "- Respond with ONLY a JSON array. No markdown fences, no preamble.\n\n"
        'Format: [{{"id": 0, "category": "...", "confidence": 0.9, "reasoning": "..."}}]'
    ),
    (
        "human",
        "The user's confirmed examples:\n{examples}\n\n"
        "Classify these transactions:\n{items_json}"
    ),
])


def _strip_code_fences(text: str) -> str:
    """Models often wrap JSON in ```json fences even when told not to."""
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def parse_batch_response(raw: str, valid_ids: set[int]) -> dict[int, CategorizationResult]:
    """
    Turns the model's text into results, defensively.
    Raises MalformedLLMOutputError if it is not usable JSON at all.
    Entries with unknown/duplicate ids are ignored; ids the model forgot are
    simply absent from the result (the caller treats those as 'not done yet').
    """
    try:
        data = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise MalformedLLMOutputError(f"Not valid JSON: {exc}") from exc

    # Some models wrap the array: {"results": [...]}
    if isinstance(data, dict):
        data = data.get("results") or data.get("items")
    if not isinstance(data, list):
        raise MalformedLLMOutputError("Expected a JSON array of results.")

    results: dict[int, CategorizationResult] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            item_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if item_id not in valid_ids or item_id in results:
            continue

        try:
            confidence = min(max(float(entry.get("confidence", 0.0)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.0
        reasoning = str(entry.get("reasoning", "")).strip()[:500] or "No reasoning provided."

        category = canonical_category(entry.get("category"))
        if category is None:
            logger.warning(f"Model returned unknown category {entry.get('category')!r}; using '{FALLBACK_CATEGORY}'.")
            category, confidence = FALLBACK_CATEGORY, min(confidence, 0.3)
            reasoning = f"Unrecognised category from model. {reasoning}"

        results[item_id] = CategorizationResult(category, confidence, reasoning, source="llm")
    return results


class ExpenseCategorizerAgent:
    """
    Classifies batches of descriptions with an LLM.

    `chain` can be injected for testing (a fake that returns canned text),
    so the unit tests never call the real API.
    """

    def __init__(self, chain=None) -> None:
        if chain is not None:
            self._chain = chain
            self.provider_name = "injected"
            return
        provider = get_llm_provider()          # raises ValueError if the API key is missing
        self.provider_name = provider.provider_name
        self._chain = BATCH_PROMPT | provider.get_chat_model() | StrOutputParser()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),   # 2s, 4s, 8s ...
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    def _invoke_and_parse(self, items: list[PendingItem], examples: list[tuple[str, str]]) -> dict[int, CategorizationResult]:
        """One LLM request for the whole batch, with retry on transient problems."""
        items_json = json.dumps(
            [
                {"id": i.item_id, "description": i.description, "amount": round(i.amount, 2), "occurrences": i.occurrences}
                for i in items
            ],
            ensure_ascii=False,
        )
        examples_text = "\n".join(f'- "{desc}" -> {cat}' for desc, cat in examples) or "(none yet)"

        raw = self._chain.invoke({
            "category_guide": _category_guide_text(),
            "examples": examples_text,
            "items_json": items_json,
        })
        return parse_batch_response(raw, {i.item_id for i in items})

    def categorize_batch(self, items: list[PendingItem], examples: list[tuple[str, str]]) -> dict[int, CategorizationResult]:
        """
        Returns {item_id: result}. Items the model failed to answer are simply
        missing, and an empty dict means the whole request failed. Callers must
        treat missing ids as 'still uncategorized', never as 'Other'.
        """
        if not items:
            return {}
        try:
            results = self._invoke_and_parse(items, examples)
            logger.info(f"LLM categorized {len(results)}/{len(items)} items via {self.provider_name}")
            return results
        except Exception as exc:
            logger.error(f"LLM batch failed ({type(exc).__name__}): {exc}")
            return {}
