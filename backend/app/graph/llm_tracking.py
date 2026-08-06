"""
LLM token and cost tracking utilities.

Two pure functions — extract_usage and llm_cost_usd — are called per-node
immediately after each LLM response. NodeTokenUsage accumulates in state
via operator.add, mirroring the timeline/error_log pattern.

Pricing: Gemini 2.5 Flash on Vertex AI (standard tier, non-thinking mode).
Update LLM_PRICING when the model or tier changes.
"""
from pydantic import BaseModel

LLM_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {
        "input_usd_per_token": 0.075e-6,   # $0.075 / 1M tokens
        "output_usd_per_token": 0.30e-6,   # $0.300 / 1M tokens  (4x input)
    },
}

_FALLBACK_MODEL = "gemini-2.5-flash"


class NodeTokenUsage(BaseModel):
    node: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def extract_usage(response) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an AIMessage or include_raw=True dict.

    Returns (0, 0) rather than raising when usage metadata is absent — covers
    mocked LLMs in unit tests and any future model that omits usage data.
    """
    if response is None:
        return 0, 0
    # with_structured_output(include_raw=True) returns {"raw": AIMessage, "parsed": ..., ...}
    if isinstance(response, dict):
        response = response.get("raw")
    if response is None:
        return 0, 0
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return 0, 0
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def llm_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD for the given model and token counts."""
    pricing = LLM_PRICING.get(model, LLM_PRICING[_FALLBACK_MODEL])
    return (
        input_tokens * pricing["input_usd_per_token"]
        + output_tokens * pricing["output_usd_per_token"]
    )


def node_usage(node: str, model: str, input_tokens: int, output_tokens: int) -> NodeTokenUsage:
    """Build a NodeTokenUsage entry for state accumulation."""
    return NodeTokenUsage(
        node=node,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=llm_cost_usd(model, input_tokens, output_tokens),
    )
