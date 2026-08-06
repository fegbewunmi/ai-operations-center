# ADR-001: Service Topology and Ownership Stored in Cloud SQL

**Status:** Accepted
**Date:** 2026-08-06

---

## Context

The Planner agent needs to reason about service dependencies to focus the investigation correctly. For a latency regression originating in Payments, the Planner must know that Gateway → Orders → Payments, and that an Orders latency spike could be caused by Payments degradation rather than an Orders-internal problem. This reasoning requires upstream/downstream traversal and blast-radius analysis.

Service ownership records (team, Slack channel, PagerDuty rotation, runbook URL) are needed by the Knowledge Agent for retrieval context and by the Response Agent for incident dispatch routing.

---

## Options considered

**Option A: Store topology in the Knowledge Agent's RAG corpus (architecture documents)**
- Pros: No additional data store; topology is co-located with architecture documentation
- Cons: The Planner receives topology as prose text, not structured data. Dependency traversal becomes an LLM interpretation task, which is non-deterministic and can hallucinate edges. A graph traversal query ("what services does Orders depend on?") should not require LLM reasoning — it is a lookup.

**Option B: Neo4j or a dedicated graph database**
- Pros: Native graph queries; expressive traversal language
- Cons: Adds a second managed database with its own operational overhead. For a topology of 6–20 services, a relational adjacency table in Cloud SQL is sufficient. The query complexity does not justify a separate graph engine.

**Option C: Cloud SQL relational tables (selected)**
- Pros: Deterministic queries, no LLM interpretation risk, consistent with existing Cloud SQL infrastructure (no new managed service), simple adjacency table supports all required traversal operations at this scale
- Cons: Graph traversal requires recursive CTEs rather than native graph syntax; less elegant for deeply nested topologies (not a concern at Orion Commerce scale)

---

## Decision

Service topology and ownership are stored in Cloud SQL as structured relational data, exposed as a shared tool (Service Catalog) callable by multiple agents.

**Schema (simplified):**
```sql
services (service_id, name, description, health_check_url)
service_dependencies (upstream_service_id, downstream_service_id)
service_ownership (service_id, team_name, slack_channel, pagerduty_rotation, runbook_url, oncall_contact)
```

The Service Catalog is implemented as a tool, not an agent. It executes deterministic SQL queries. No LLM call is made to answer topology questions.

Architecture documents describing the system (prose) remain in the Knowledge Agent's RAG corpus for richer contextual understanding, but they are not the source of truth for topology or ownership.

---

## Consequences

- Topology is queryable deterministically; no hallucination risk on dependency edges
- Adding a new service requires a database record, not a document update
- Service Catalog tool is shared across Planner, Knowledge Agent, and Response Agent — changes to the schema affect multiple callers
- Recursive CTE queries required for multi-hop traversal (manageable at this scale; revisit if topology exceeds 50 services)
