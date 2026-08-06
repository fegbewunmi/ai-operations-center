# ADR-005: Monitoring and Logs Merged into Single Telemetry Agent

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The original 8-agent sketch included separate Monitoring and Logs agents. These need to be evaluated as separate agents or merged based on actual coordination cost versus separation-of-concerns benefit.

---

## Options considered

**Option A: Separate Monitoring Agent and Logs Agent**
- Pros: Cleaner single responsibility per agent; smaller individual prompt surfaces; can be parallelized
- Cons: Both agents query time-indexed data stores using the same pattern (query scope + time window → structured data). The Planner must coordinate two agents where one would serve, increasing round-trips. The agents would frequently need to be called together on the same service and time window — which turns a logical unit of work into two network calls. The reasoning patterns overlap heavily (both involve anomaly detection and summarization over a time window).

**Option B: Single Telemetry Agent (selected)**
- Pros: One call covers the time-correlated evidence a specialist investigation requires. The Planner specifies `focus` to avoid over-fetching. Tool interfaces are internally distinct (metrics API vs. logging API vs. traces API) but the agent's reasoning over them is coherent — all three are asking "what was this service doing during this time window?"
- Cons: The agent's prompt covers three tool sets; if the prompts for metrics reasoning and log pattern recognition diverge significantly, one agent becomes harder to tune without affecting the other. Splitting becomes necessary if this happens.

---

## Decision

Monitoring and Logs are merged into a single Telemetry Agent. The `focus` input field (`["metrics", "logs", "traces"]`) lets the Planner specify which data sources to query, avoiding over-fetching.

The merge point is monitored in the eval harness: if Telemetry Agent tool selection accuracy degrades (wrong `focus` specified or wrong queries issued), that is a signal to revisit splitting.

---

## Consequences

- Saves one Planner round-trip per iteration for the common case where both metrics and logs are needed
- Agent prompt covers multiple tool sets — this is the primary risk and must be validated in eval
- Future split along the `focus` field is possible without changing caller interfaces — the seam is already present in the schema
- Traces are included in the agent definition (Phase 2 implementation) so the interface does not change when traces are added
