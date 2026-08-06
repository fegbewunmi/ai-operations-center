# ADR-007: Engineering Memory Schema Defined in Phase 1, Implemented in Phase 2

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

Cross-incident learning - where the system retrieves and applies knowledge from past investigations to current ones - is a significant differentiator from stateless investigation systems. However, it introduces architectural complexity: the system must write back its own investigation results to a queryable store, creating a feedback loop.

The question is when to implement this capability.

---

## Options considered

**Option A: Implement engineering memory in Phase 1**
- Pros: The feature is present from the start; early incident records populate the memory store
- Cons: Phase 1 already has significant scope: three incident families, full agent graph, eval harness, synthetic environment. Adding a write path (Synthesizer → Cloud SQL + pgvector) and a retrieval path (Knowledge Agent → past incident search) before the core investigation loop is validated increases complexity before the system's fundamental behavior is confirmed. Bugs in memory retrieval would be harder to isolate.

**Option B: Define schema now, implement write-back in Phase 2 (selected)**
- Pros: The `IncidentMemoryRecord` schema and the Synthesizer's output contract are designed correctly from the start - Phase 2 does not require schema changes that break Phase 1 data. The Knowledge Agent's interface includes the `SimilarIncidents` return type from the start (returning `None` in Phase 1). Phase 2 implementation is additive, not a refactor. Additionally, Phase 2 enables an eval experiment: accuracy with vs. without memory, which is a compelling result to show in interviews.
- Cons: Phase 1 investigations are not stored and cannot be retrieved. Some early incident records are lost (acceptable - they are synthetic and can be regenerated).

---

## Decision

The `IncidentMemoryRecord` schema is defined as part of the Synthesizer's output contract in Phase 1. The Synthesizer always produces this record as part of `SynthesisOutput`, but in Phase 1 it is not written anywhere (`incident_memory_record` is produced but discarded).

In Phase 2, a post-synthesis step writes the record to Cloud SQL and generates an embedding for pgvector. The Knowledge Agent's `SimilarIncidents` return type becomes populated.

**`IncidentMemoryRecord` schema (defined in Phase 1):**
```python
class IncidentMemoryRecord(BaseModel):
    incident_id: str
    incident_type: Literal["failed_deployment", "latency_regression", "resource_leak", "other"]
    affected_service: str
    onset_timestamp: datetime
    resolution_timestamp: datetime | None
    root_cause_category: str
    root_cause_description: str
    confidence_at_resolution: float
    remediation_applied: str | None
    investigation_duration_seconds: int
    tool_invocation_count: int
    embedding_text: str    # normalized text for vector similarity search
    embedding: list[float] | None  # populated when stored (Phase 2)
```

---

## Consequences

- Schema stability: Phase 2 adds a write step without changing the schema
- The eval harness can measure accuracy with vs. without incident memory (Phase 2 experiment)
- `embedding_text` must be carefully designed in Phase 1 - it determines retrieval quality in Phase 2
- The Knowledge Agent interface does not change between phases; `SimilarIncidents` returns `None` in Phase 1 and populated results in Phase 2
- Synthetic incident records from Phase 1 can be retroactively stored in Phase 2 if the schema is stable
