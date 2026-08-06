# ADR-012: LLM Token and Cost Tracking — Per-Node State, Deferred DB Columns

**Status:** Accepted  
**Date:** August 6, 2026  
**Context:** `InvestigationBudget.tokens_used` was defined but never incremented; LLM costs were invisible across all evaluation runs and production investigations.

---

## Audit Findings

Before designing anything, the codebase was audited for existing token capture. The findings:

`tokens_used: int = 0` exists on `InvestigationBudget` (defined in `app/shared/schemas/incident.py`) and has a corresponding DB column (`budget_tokens_used INT NOT NULL DEFAULT 0`, migration `0001_initial_schema.py`). It has never been incremented. No node reads `response.usage_metadata` after an LLM call. No cost computation exists anywhere. The field has been at its default value — zero — on every investigation ever run.

The data is available: LangChain's `AIMessage` carries a `usage_metadata` field (`input_tokens`, `output_tokens`, `total_tokens`) that `ChatGoogleGenerativeAI` populates on every non-streaming call. Nothing captures it.

There are six LLM call sites across five active nodes:

| Node | Call pattern | Returns |
|---|---|---|
| `planner` | `structured_llm.ainvoke()` via `with_structured_output` | `PlannerDecision` (Pydantic) |
| `incident_analysis` | `structured_llm.ainvoke()` via `with_structured_output` | `AnalysisOutput` (Pydantic) |
| `synthesizer` | `llm.ainvoke()` | `AIMessage` |
| `telemetry` | `llm.ainvoke()` | `AIMessage` |
| `knowledge` | `llm.ainvoke()` | `AIMessage` |
| `deployment` | `llm.ainvoke()` | `AIMessage` |

The two structured-output nodes are structurally different: `with_structured_output` returns a Pydantic object, so `usage_metadata` is not on the returned value. LangChain's `include_raw=True` option on `with_structured_output` returns `{"raw": AIMessage, "parsed": <Model>, "parsing_error": ...}`, preserving the original `AIMessage` and with it `usage_metadata`.

---

## Options Considered

### Option A: Extend `traced_node` to capture tokens

`traced_node` in `app/graph/tracing.py` is an existing decorator that wraps every node function to produce an OpenTelemetry span. The idea: after `result = await fn(state)`, inspect the result dict for a `_token_usage` sentinel key that each node populates, then record it as span attributes.

**Rejected.** `traced_node` wraps the node function, not the LLM call inside it. This means: (1) every node still needs internal logic to extract usage from the response — the extraction is not centralized, only the recording is; (2) it introduces a hidden contract between nodes and the tracing layer via a sentinel key; (3) structured-output nodes return a different response type that requires `include_raw=True` — the tracing decorator cannot know which call pattern each node uses. Extending `traced_node` would make tracing.py know too much about LLM call patterns, mixing observability infrastructure with LLM API behavior.

### Option B: A centralized `llm_invoke` wrapper

A `track_llm_call(llm, messages, node_name) -> (response, NodeTokenUsage)` function that any node can call instead of `llm.ainvoke()` directly.

**Rejected.** The two call patterns — `llm.ainvoke()` for freeform responses and `structured_llm.ainvoke()` for Pydantic output — have different return types and the structured path requires `include_raw=True`. A single wrapper that handles both becomes a branching function parameterized by schema type, effectively reimplementing a subset of LangChain's `with_structured_output` API. The added abstraction exceeds the code it replaces.

### Option C: A small shared helper, called per-node (selected)

Two pure functions in `app/graph/llm_tracking.py`:

```python
def extract_usage(response) -> tuple[int, int]:
    """Returns (input_tokens, output_tokens) from AIMessage or include_raw dict."""

def llm_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns estimated cost in USD from token counts and model name."""
```

Each node calls `extract_usage` once, immediately after its LLM call, and appends a `NodeTokenUsage` entry to its returned state dict. Six call sites, one additional line each. The extraction logic lives in one place; per-node attribution is natural because each node already knows its own name.

This is the correct scope. The helper is small enough to be a pure function with no dependencies (testable with no mocks), and per-node attribution requires no additional scaffolding — it's a consequence of where the helper is called.

---

## Decision

### Token extraction

The extraction helper handles both call patterns:

- For `AIMessage` (plain `ainvoke`): `response.usage_metadata["input_tokens"]` / `output_tokens`
- For structured output with `include_raw=True`: `response["raw"].usage_metadata[...]`
- For missing metadata (mocked LLM in tests, or a future model that doesn't return counts): return `(0, 0)` rather than raising — missing metadata is not a failure

Nodes using `with_structured_output` are migrated to `include_raw=True`. The parsed object is extracted from `response["parsed"]` instead of the response directly.

### Cost computation

Gemini 2.5 Flash Vertex AI pricing:

```
Input:  $0.075 per 1,000,000 tokens
Output: $0.30  per 1,000,000 tokens
```

Output tokens are 4× more expensive than input tokens. A total-tokens figure cannot be used to compute cost correctly — the split is required. Pricing constants live in `app/config.py` as a model-keyed dict alongside `gemini_model`, so that changing the model and updating its pricing is a single-location edit.

### State schema

A new Pydantic model:

```python
class NodeTokenUsage(BaseModel):
    node: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
```

A new field on `InvestigationState`:

```python
token_log: Annotated[list[NodeTokenUsage], operator.add] = []
```

`operator.add` is the same reducer used by `timeline` and `error_log` — list fields accumulate across iterations without overwriting. If the planner runs four times, there are four `NodeTokenUsage` entries for `planner` in `token_log`. This is intentional: per-iteration cost is visible, and iteration cost growth is a signal (a node that gets more expensive over iterations may be receiving larger prompts).

`InvestigationBudget` is extended with `input_tokens_used: int = 0` and `output_tokens_used: int = 0`. The existing `tokens_used` field is retired — it was never incremented and adding a third total that must be kept consistent with two source fields would be error-prone. `tokens_used` is removed from the Pydantic schema. The corresponding DB column (`budget_tokens_used`) is left in place but will always read 0 until a future migration adds the replacement columns (see DB Migration section below).

A `cost_usd` property on `InvestigationBudget` computes from the new fields and `settings.gemini_model`:

```python
@property
def cost_usd(self) -> float:
    return llm_cost_usd(settings.gemini_model, self.input_tokens_used, self.output_tokens_used)
```

The planner's existing `budget.model_copy(update={...})` call, which already increments `iterations_used` and `tool_calls_used`, is extended to add the new token totals. Other nodes update the budget similarly.

### Eval harness output

`EvalScore` gains `total_input_tokens`, `total_output_tokens`, and `estimated_cost_usd` fields read from `budget`. `format_score` adds a tokens/cost line to per-fixture output. `format_summary` adds aggregate tokens and total estimated cost to the summary block.

---

## Testing Strategy

Token tracking introduces two classes of correctness:

**Class 1 — math correctness**: given known token counts, is the cost computed right? This is a pure function with no dependencies. It should be a unit test with no network, no mocks, and no LLM spend. Specifically:

- `extract_usage` with a mock `AIMessage` constructed directly (`AIMessage(content="", usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})`) — assert `(100, 50)` is returned
- `extract_usage` with the `include_raw=True` dict form — assert same extraction
- `extract_usage` with `usage_metadata=None` — assert `(0, 0)` not an exception
- `llm_cost_usd("gemini-2.5-flash", 1_000_000, 1_000_000)` — assert `$0.375` to the cent
- `llm_cost_usd("gemini-2.5-flash", 0, 0)` — assert `$0.0`
- Budget accumulation: start with `input_tokens_used=100`, add `NodeTokenUsage(input_tokens=50, ...)`, assert budget reaches `150`

**Class 2 — wiring correctness**: is `extract_usage` actually being called in real graph runs, and are real API responses flowing into the tracking system? This cannot be verified with mocks — a mock LLM that returns a fixed response could be wired up correctly or incorrectly and the test would pass either way.

The eval harness check (added to `eval/scorer.py` and surfaced in `EvalScore`) verifies wiring: after a real eval run, assert `budget.input_tokens_used > 0`, `budget.output_tokens_used > 0`, and `len(token_log) > 0`. If any node's extraction is broken — wrong call pattern, missing `include_raw=True`, exception swallowed — the token counts will be zero and the harness check will fail.

This division means: unit tests run in milliseconds with no API calls and no spend; the eval harness run (which already requires Vertex AI credentials and makes real calls) is the appropriate place to confirm the wiring.

---

## DB Migration — Deferred

**Decision: state-only for now. DB columns are deferred.**

The `investigations` table has a `budget_tokens_used` column that maps to the now-retired `tokens_used` field. Adding `budget_input_tokens` and `budget_output_tokens` columns requires a new Alembic migration. This is deferred because:

1. The `investigations` API does not currently expose token fields in its response contract. Adding DB columns without a corresponding API surface has no immediate consumer.
2. The eval harness is the primary consumer of token/cost data right now. It reads from in-memory state, not the DB.
3. The correct DB design (two columns for input/output, or a JSONB token_breakdown) is worth deciding when there is a concrete query need — a dashboard, a cost-per-service report, or an investigation detail API endpoint that exposes this data.

This follows the same phasing rationale as ADR-007 (engineering memory schema defined in Phase 1, write path deferred to Phase 2): define the schema correctly now in the Pydantic models and state, defer the storage until there is a consumer.

The `budget_tokens_used` DB column is left in place and will continue to read 0. A comment is added to the migration marking it as superseded. The DB columns for `input_tokens_used` and `output_tokens_used` are tracked as deferred in the DESIGN-DOC roadmap.

---

## Consequences

- Per-node token visibility: `token_log` shows which agent is expensive, across iterations
- Investigation cost is computable at any point from `budget.cost_usd`
- The eval harness summary gains a tokens/cost line per run — cost per fixture is now observable
- `tokens_used` is removed from `InvestigationBudget`; any code reading it breaks at import time (easy to catch; there were no readers)
- Structured-output nodes (`planner`, `incident_analysis`) switch to `include_raw=True` — the parsed object is now at `response["parsed"]` instead of being the response directly; this is a localized change in two nodes
- DB column `budget_tokens_used` persists but always reads 0 until a future migration replaces it; this is a known and accepted state
- Adding a new model to the pricing dict in `config.py` is sufficient to support model changes; no other file needs updating for pricing

---

## Files to Change

| File | Change |
|---|---|
| `app/graph/llm_tracking.py` | New: `extract_usage`, `llm_cost_usd`, `NodeTokenUsage` |
| `app/shared/schemas/incident.py` | Remove `tokens_used`; add `input_tokens_used`, `output_tokens_used`; add `cost_usd` property |
| `app/config.py` | Add `LLM_PRICING` dict |
| `app/graph/state.py` | Add `token_log: Annotated[list[NodeTokenUsage], operator.add]` |
| `app/graph/nodes/planner.py` | Call `extract_usage`; add token totals to `budget.model_copy`; switch to `include_raw=True` |
| `app/graph/nodes/incident_analysis.py` | Same as planner |
| `app/graph/nodes/synthesizer.py` | Call `extract_usage`; update budget |
| `app/graph/nodes/telemetry.py` | Call `extract_usage`; update budget |
| `app/graph/nodes/knowledge.py` | Call `extract_usage`; update budget |
| `app/graph/nodes/deployment.py` | Call `extract_usage`; update budget |
| `eval/scorer.py` | Add token/cost fields to `EvalScore`; wiring assertion; update `format_score` and `format_summary` |
| `tests/test_llm_tracking.py` | New: unit tests for `extract_usage` and `llm_cost_usd` |
