# ADR-008: Investigation Controlled by Multi-Dimensional Budget, Not Iteration Count

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The Planner runs an iterative investigation loop. The loop must terminate. The question is what termination mechanism reflects the actual constraints on investigation quality and cost.

---

## Options considered

**Option A: Hard iteration count (e.g., max 5 loops)**
- Pros: Simple; easy to explain; predictable
- Cons: "Counted to 5" is an operational constraint, not a planning concept. A simple incident that reaches high confidence after 2 iterations still runs to 5. A complex incident that needs 7 iterations is cut short at 5 regardless of evidence quality. The system does not express why it stopped — it just stopped. In an interview, "the system stops after 5 iterations" is an engineering simplification, not a design decision.

**Option B: Multi-dimensional investigation budget (selected)**
- Model the investigation as consuming a finite budget across multiple dimensions: iterations, tool calls, tokens, and wall-clock latency. The Planner consults remaining budget at each decision point. Additionally, the Planner stops early if its working confidence exceeds a threshold — regardless of remaining budget.
- Pros: The system can express "investigation budget exhausted" as a meaningful operational concept. Early stopping on confidence reduces cost on simple incidents. Each budget dimension corresponds to a real operational constraint: iterations (LLM cost), tool calls (external API cost), tokens (context window and cost), latency (SLA).
- Cons: More configuration parameters; requires testing to find appropriate defaults for each incident family.

---

## Decision

Investigations are governed by a multi-dimensional `InvestigationBudget`.

```python
class InvestigationBudget(BaseModel):
    # Limits
    max_iterations: int = 5
    max_tool_calls: int = 20
    max_tokens: int = 50_000
    max_latency_seconds: float = 30.0
    confidence_threshold: float = 0.90

    # Consumed (updated at each Planner iteration)
    iterations_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
```

**Termination logic (evaluated by Planner in order):**
1. `planner_working_confidence ≥ confidence_threshold` → synthesize (early stop)
2. No new evidence obtainable (all remaining specialists would produce redundant queries) → synthesize
3. Any budget dimension exhausted → synthesize with `investigation_incomplete: True` flag, Safety Guard aware
4. Unrecoverable specialist error → escalate

**Budget defaults** are starting points for Version 1, to be tuned using the eval harness. Expected that resource-leak incidents (gradual, hard to detect) will run closer to `max_iterations` than failed-deployment incidents (often resolved in 2–3 iterations).

---

## Consequences

- Budget consumption is tracked per investigation and reported in `SynthesisOutput` — the eval harness uses this for cost-per-investigation measurement
- The Planner prompt must explicitly reason about remaining budget, not just about evidence quality
- Budget parameters are configuration, not constants — different incident severity levels could use different budgets (P1 incidents might get a higher `max_tokens` allowance)
- Early stopping on confidence requires the Planner to maintain a numerical confidence estimate throughout the loop, not just at the end
- The eval harness should measure budget utilization distribution across incident families (expected: failed-deployment uses fewer iterations than resource-leak)
