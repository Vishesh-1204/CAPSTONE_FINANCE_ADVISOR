# tests/test_expense_categorizer.py
#
# Tests the LLM agent WITHOUT calling any LLM: a fake "chain" returns canned text.

import pytest

from app.agents.expense_categorizer import (
    ExpenseCategorizerAgent, MalformedLLMOutputError, PendingItem, parse_batch_response,
)


class FakeChain:
    """Stands in for prompt | model | parser."""
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls, self.last_inputs = reply, error, 0, None

    def invoke(self, inputs):
        self.calls += 1
        self.last_inputs = inputs
        if self.error:
            raise self.error
        return self.reply


def test_parses_clean_json():
    raw = '[{"id": 0, "category": "Groceries", "confidence": 0.9, "reasoning": "supermarket"}]'
    result = parse_batch_response(raw, {0})
    assert result[0].category == "Groceries" and result[0].confidence == 0.9 and result[0].source == "llm"


def test_strips_markdown_fences_and_fixes_category_case():
    raw = '```json\n[{"id": 0, "category": "food & dining", "confidence": 0.8, "reasoning": "x"}]\n```'
    assert parse_batch_response(raw, {0})[0].category == "Food & Dining"


def test_unknown_category_becomes_other_with_low_confidence():
    raw = '[{"id": 0, "category": "Stuff", "confidence": 0.95, "reasoning": "x"}]'
    result = parse_batch_response(raw, {0})[0]
    assert result.category == "Other" and result.confidence <= 0.3


def test_confidence_is_clamped_and_bad_values_become_zero():
    raw = '[{"id": 0, "category": "Other", "confidence": 7}, {"id": 1, "category": "Other", "confidence": "high"}]'
    result = parse_batch_response(raw, {0, 1})
    assert result[0].confidence == 1.0 and result[1].confidence == 0.0


def test_unknown_and_duplicate_ids_are_ignored_and_missing_ids_absent():
    raw = '[{"id": 0, "category": "Other"}, {"id": 0, "category": "Groceries"}, {"id": 99, "category": "Other"}]'
    result = parse_batch_response(raw, {0, 1})
    assert set(result) == {0} and result[0].category == "Other"    # first one wins, id 1 missing


def test_string_ids_and_wrapped_object_are_accepted():
    raw = '{"results": [{"id": "0", "category": "Travel", "confidence": 0.7, "reasoning": "flight"}]}'
    assert parse_batch_response(raw, {0})[0].category == "Travel"


def test_garbage_raises_malformed_error():
    with pytest.raises(MalformedLLMOutputError):
        parse_batch_response("Sorry, I cannot help with that.", {0})


def test_batch_sends_examples_and_occurrences_to_the_model():
    chain = FakeChain('[{"id": 0, "category": "Housing & Rent", "confidence": 0.9, "reasoning": "rent"}]')
    agent = ExpenseCategorizerAgent(chain=chain)
    agent.categorize_batch([PendingItem(0, "Flat payment Rahul", 25000.0, occurrences=3)], [("Swiggy dinner", "Food & Dining")])
    assert '"occurrences": 3' in chain.last_inputs["items_json"]
    assert "Swiggy dinner" in chain.last_inputs["examples"]


def test_wrong_api_key_fails_fast_without_retrying_and_returns_nothing():
    chain = FakeChain(error=RuntimeError("API key not valid. Please pass a valid API key."))
    agent = ExpenseCategorizerAgent(chain=chain)
    assert agent.categorize_batch([PendingItem(0, "x", 1.0)], []) == {}
    assert chain.calls == 1          # not retried


def test_empty_batch_makes_no_call():
    chain = FakeChain("[]")
    assert ExpenseCategorizerAgent(chain=chain).categorize_batch([], []) == {}
    assert chain.calls == 0
