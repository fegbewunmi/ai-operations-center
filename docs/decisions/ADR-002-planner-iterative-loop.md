# ADR-002: Planner Controls Investigation as an Iterative Loop

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

A multi-agent incident investigation system requires an orchestration pattern. The system must decompose an incident, gather evidence from multiple data sources, and synthesize findings into a root-cause hypothesis. The pattern chosen affects graph topology, state management, cost, and debuggability.

---

## Options considered

**Option A: Fixed pipeline (linear DAG)**
- Pros: Simple; easy to reason about; predictable cost
- Cons: Cannot adapt to findings. In a latency regression where deployment is not the cause, a fixed pipeline still calls the Deployment Agent, wastes budget, and cannot decide "I already have enough evidence, skip the remaining steps." Cannot re-investigate if a hypothesis is invalidated. Does not model how a real engineer actually investigates.

**Option B: Supervisor/planner with one-time decomposition**
- Pros: Cleaner separation between planning and execution; widely demonstrated in LangGraph documentation
- Cons: The investigation plan is formed before any data is seen. A plan formed on alert metadata alone will often be wrong — for a resource leak, the first 3 tool calls may reveal that the real cause is a connection pool issue, not a file handle leak, which requires different follow-up queries. A one-time plan cannot pivot.

**Option C: Iterative loop with Planner as controller (selected)**
- Pros: The Planner re-evaluates what to do next after every specialist result. It can pivot, cut off unproductive branches, and stop early when confidence is high. This is how experienced engineers actually investigate: form a hypothesis, test it, revise.
- Cons: One additional LLM round-trip per iteration (Planner sees specialist result, decides next step). Risk of infinite loops if termination conditions are not carefully designed. Requires explicit budget management.

**Option D: Event-driven (pub/sub message bus)**
- Pros: Maximum parallelism; decoupled agents
- Cons: Adds significant infrastructure complexity (Cloud Pub/Sub, consumer management, message ordering). The investigation loop requires sequential reasoning (the Planner must see Telemetry results before deciding whether to call Deployment), so parallelism is limited. Not justified at this scale.

---

## Decision

The Planner controls the investigation as an iterative loop. After each specialist invocation, results return to the Planner, which decides: invoke another specialist, synthesize, or escalate.

The loop is implemented in LangGraph using a conditional edge from the Planner node. The Planner node emits a typed decision (`invoke | synthesize | escalate`) which routes to the next node.

---

## Consequences

- The Planner sees every piece of evidence and can pivot the investigation — a property a fixed pipeline lacks
- Each iteration adds one Planner LLM call; this is the primary driver of token cost per investigation
- Termination logic must be explicit and tested (see ADR-008 for budget design)
- The Planner's prompts are the most critical in the system — poor Planner reasoning degrades every investigation
- LangGraph's built-in checkpointing is used at every Planner iteration, enabling the terminate-checkpoint-resume approval pattern (see ADR-004)
