"""
Unit tests for LLM token extraction and cost calculation.

All tests are pure — no network, no LLM, no mocks beyond direct
AIMessage construction.  These tests verify the math and extraction
logic; the eval harness (real Vertex AI run) verifies the wiring.
"""
import pytest
from langchain_core.messages import AIMessage

from app.graph.llm_tracking import (
    NodeTokenUsage,
    extract_usage,
    llm_cost_usd,
    node_usage,
)
from app.shared.schemas.incident import InvestigationBudget


# ── extract_usage ─────────────────────────────────────────────────────────────

def test_extract_usage_aimessage():
    msg = AIMessage(
        content="hello",
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    )
    assert extract_usage(msg) == (100, 50)


def test_extract_usage_include_raw_dict():
    """with_structured_output(include_raw=True) returns {"raw": AIMessage, "parsed": ...}."""
    msg = AIMessage(
        content="",
        usage_metadata={"input_tokens": 200, "output_tokens": 75, "total_tokens": 275},
    )
    raw = {"raw": msg, "parsed": object(), "parsing_error": None}
    assert extract_usage(raw) == (200, 75)


def test_extract_usage_none():
    assert extract_usage(None) == (0, 0)


def test_extract_usage_no_metadata():
    msg = AIMessage(content="hello", usage_metadata=None)
    assert extract_usage(msg) == (0, 0)


def test_extract_usage_include_raw_dict_no_raw():
    assert extract_usage({"raw": None, "parsed": object()}) == (0, 0)


def test_extract_usage_missing_keys_in_metadata():
    # Usage metadata exists but lacks input/output keys (non-standard response object).
    class _FakeResponse:
        usage_metadata = {"total_tokens": 150}  # partial — no input_tokens or output_tokens
    assert extract_usage(_FakeResponse()) == (0, 0)


# ── llm_cost_usd ─────────────────────────────────────────────────────────────

def test_cost_zero_tokens():
    assert llm_cost_usd("gemini-2.5-flash", 0, 0) == 0.0


def test_cost_input_only():
    # 1M input tokens at $0.075/MTok = $0.075
    cost = llm_cost_usd("gemini-2.5-flash", 1_000_000, 0)
    assert abs(cost - 0.075) < 1e-9


def test_cost_output_only():
    # 1M output tokens at $0.30/MTok = $0.30
    cost = llm_cost_usd("gemini-2.5-flash", 0, 1_000_000)
    assert abs(cost - 0.30) < 1e-9


def test_cost_combined():
    # 1M input + 1M output = $0.075 + $0.30 = $0.375
    cost = llm_cost_usd("gemini-2.5-flash", 1_000_000, 1_000_000)
    assert abs(cost - 0.375) < 1e-9


def test_cost_output_is_4x_input():
    in_cost = llm_cost_usd("gemini-2.5-flash", 1000, 0)
    out_cost = llm_cost_usd("gemini-2.5-flash", 0, 1000)
    assert abs(out_cost / in_cost - 4.0) < 1e-9


def test_cost_unknown_model_falls_back_to_default():
    cost_unknown = llm_cost_usd("gemini-unknown-xyz", 500, 200)
    cost_known = llm_cost_usd("gemini-2.5-flash", 500, 200)
    assert cost_unknown == cost_known


def test_cost_small_real_call():
    # ~400 input, ~90 output — typical planner call
    cost = llm_cost_usd("gemini-2.5-flash", 400, 90)
    expected = 400 * 0.075e-6 + 90 * 0.30e-6
    assert abs(cost - expected) < 1e-12


# ── node_usage ────────────────────────────────────────────────────────────────

def test_node_usage_fields():
    usage = node_usage("planner", "gemini-2.5-flash", 500, 100)
    assert usage.node == "planner"
    assert usage.input_tokens == 500
    assert usage.output_tokens == 100
    assert abs(usage.cost_usd - llm_cost_usd("gemini-2.5-flash", 500, 100)) < 1e-12


def test_node_usage_zero():
    usage = node_usage("synthesizer", "gemini-2.5-flash", 0, 0)
    assert usage.cost_usd == 0.0


def test_node_usage_is_pydantic_model():
    usage = node_usage("telemetry", "gemini-2.5-flash", 300, 60)
    assert isinstance(usage, NodeTokenUsage)


# ── InvestigationBudget token accumulation ────────────────────────────────────

def test_budget_token_accumulation():
    budget = InvestigationBudget(input_tokens_used=100, output_tokens_used=50)
    updated = budget.model_copy(update={
        "input_tokens_used": budget.input_tokens_used + 200,
        "output_tokens_used": budget.output_tokens_used + 75,
    })
    assert updated.input_tokens_used == 300
    assert updated.output_tokens_used == 125


def test_budget_starts_at_zero():
    budget = InvestigationBudget()
    assert budget.input_tokens_used == 0
    assert budget.output_tokens_used == 0


def test_budget_no_tokens_used_field():
    """tokens_used was retired; accessing it should raise AttributeError."""
    budget = InvestigationBudget()
    assert not hasattr(budget, "tokens_used")


def test_budget_accumulation_multiple_nodes():
    budget = InvestigationBudget()
    nodes = [
        (412, 87),   # planner
        (318, 42),   # telemetry
        (289, 55),   # deployment
        (401, 63),   # knowledge
        (890, 210),  # incident_analysis
        (220, 180),  # synthesizer
    ]
    for in_t, out_t in nodes:
        budget = budget.model_copy(update={
            "input_tokens_used": budget.input_tokens_used + in_t,
            "output_tokens_used": budget.output_tokens_used + out_t,
        })
    assert budget.input_tokens_used == sum(i for i, _ in nodes)
    assert budget.output_tokens_used == sum(o for _, o in nodes)
