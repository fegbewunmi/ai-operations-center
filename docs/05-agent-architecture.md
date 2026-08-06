# Agent Architecture

*Status: Section locked through agent boundaries. Orchestration pattern (Section 3 of design doc) in progress.*
*Last updated: 2026-08-06.*

---

## Design principles

1. Each agent has one clearly stateable responsibility. If the description requires "and," it is probably two agents.
2. Agents that reason about different domains use different tools and have different failure modes - those should be separate.
3. The coordination cost of every split must be justified by the benefit.
4. Structured operational data (topology, ownership) is a tool, not an agent. Tools do not hold LLMs.

---

## Graph topology

```
IncidentTrigger
      │
      ▼
   Planner ◄──────────────────────────────────────┐
   │     │     │                                  │
   ▼     ▼     ▼                                  │
Telemetry  Deployment  Knowledge          (results return to Planner)
                                                  │
      ▼ (Planner decides: continue or synthesize) │
                                                  │
 Synthesizer                                      │
      │                                           │
      ▼                                           │
Evidence & Safety Guard ──── fail ────────────────┘
      │ pass
      ▼
 Response Agent
   │              │
   ▼              ▼
L1/L2         L3 (pending approval)
dispatch      checkpoint + suspend
                   │
           FastAPI approval endpoint
                   │
              Resume → Response Agent
```

---

## Shared tool: Service Catalog

The Service Catalog is **not an agent**. It is a structured query tool backed by Cloud SQL, callable by multiple agents. It has no LLM reasoning - it executes deterministic queries.

**Why a tool, not an agent:** Topology and ownership data are structured operational records. The Planner needs deterministic graph traversal ("what services does Orders depend on?"), not fuzzy retrieval. Adding an LLM call to answer a SQL query would be wasteful and introduce hallucination risk.

**Capabilities:**
- `lookup_owner(service: str) → ServiceOwnership`
- `get_dependencies(service: str) → list[str]`
- `get_dependents(service: str) → list[str]`
- `get_blast_radius(service: str) → list[str]`
- `get_topology_snapshot(focal_service: str) → ServiceTopology`

**Callers:** Planner (topology for reasoning), Knowledge Agent (ownership for context), Response Agent (ownership for dispatch routing).

---

## Agent 1: Planner (Incident Orchestrator)

**Single responsibility:** Control the investigation loop - decide what information is missing, which specialist to invoke next, and when the evidence is sufficient to hand off to the Synthesizer.

**Inputs:**
- `IncidentTrigger` on first invocation
- Updated `InvestigationState` on each subsequent iteration (growing: timeline, specialist findings)
- `InvestigationBudget` (remaining iterations, tool calls, tokens)

**Outputs per iteration** - exactly one of:
- `{action: "invoke", agent: AgentType, query: AgentQuery}` - call a specialist
- `{action: "synthesize"}` - evidence is sufficient; hand off to Synthesizer
- `{action: "escalate", reason: str}` - budget exhausted or unresolvable; human handoff

**Loop termination conditions (evaluated in order):**
1. `planner_working_confidence ≥ CONFIDENCE_THRESHOLD` (default: 0.90) - stop early, synthesize
2. No new evidence is obtainable from any remaining specialist (all queries would be redundant)
3. `budget.iterations_remaining == 0` or `budget.tool_calls_remaining == 0` - synthesize with what exists, flag incomplete
4. Unrecoverable error from a specialist - escalate

**Internal state** (not part of formal output):
- `planner_working_hypothesis: str` - informal running hypothesis used for loop termination; this is NOT the formal Synthesizer output

**Why separate from Synthesizer:** The Planner asks "what should I do next?" The Synthesizer asks "given everything, what happened and why?" These are different reasoning tasks with different prompts. Combining them creates an agent that both orchestrates and reasons, which makes both functions harder to test, prompt-engineer, and explain.

**Coordination cost:** Every specialist result routes back through the Planner, adding one LLM round-trip per tool call. This is the cost of the iterative pattern. Justified because the Planner can cut short a dead-end branch - a fixed pipeline cannot.

---

## Agent 2: Telemetry Agent

**Single responsibility:** Query and interpret metrics, logs, and traces for a specific service and time window, returning structured findings.

**Inputs:**
- `service_name: str`
- `time_window: TimeWindow`
- `investigation_query: str` - what the Planner is looking for (scopes tool calls, avoids over-fetching)
- `focus: list[Literal["metrics", "logs", "traces"]]` - Planner specifies

**Outputs:**
- `TelemetryFindings`: key metrics, anomalous metrics, log events, error rate change, latency change (p50/p99), resource utilization (cpu_pct, memory_pct, connection_count, fd_count), LLM-generated interpretation summary

**Tools used:**
- Cloud Monitoring API (metrics)
- Cloud Logging API (logs)
- Cloud Trace (traces, Phase 2)
- In synthetic environment: simulated data store backed by Cloud SQL

**Why not split Monitoring and Logs:** Both query time-indexed data stores; their tool interfaces share the same pattern (query scope + time window → structured data). Coordination cost of separate agents exceeds benefit at this scale. The `focus` field provides a clean seam to split on later if prompts diverge significantly.

**Coordination cost:** Planner must be specific (`"check DB connection count for Inventory, 09:00–11:00"`) or the agent over-fetches. Vague queries are a failure mode tracked in the eval harness.

---

## Agent 3: Deployment Agent

**Single responsibility:** Retrieve and analyze deployment history, configuration changes, and rollback availability for services near the incident onset.

**Inputs:**
- `service_name: str`
- `time_window: TimeWindow` - typically incident onset ± lookback window
- `investigation_query: str`

**Outputs:**
- `DeploymentFindings`: list of deployments in window, config changes per deployment, whether a deployment occurred within N minutes of incident onset, rollback availability, LLM-generated interpretation summary

**Tools used:**
- Deployment records table (Cloud SQL in synthetic environment; GitHub/artifact registry in production)
- Config change history

**Why separate from Telemetry:** "What changed" (discrete events, version diffs, config mutations) versus "what happened" (continuous time-series behavior) are different domains, different tools, different reasoning patterns. For a failed-deployment incident, this is usually the first specialist called and often produces the highest-confidence finding quickly. The Planner must be able to invoke it independently of telemetry.

**Coordination cost:** The Planner should know to call this first for failed-deployment incidents - a prompt-level heuristic, not a hardcoded graph edge.

---

## Agent 4: Knowledge Agent

**Single responsibility:** Retrieve relevant institutional knowledge - runbooks, postmortems, architecture docs, known error patterns - ranked by relevance to the current investigation.

**Inputs:**
- `query: str` - derived from Planner's working hypothesis (e.g., "Payments 5xx spike after deployment, NullPointerException in checkout flow")
- `service_name: str | None`
- `document_types: list[str] | None` - narrows retrieval scope

**Outputs:**
- `KnowledgeContext`: ranked document results with excerpts and relevance scores; service ownership via Service Catalog tool; LLM-generated summary
- Phase 2 addition: `SimilarIncidents` - past incidents with similar embedding, their root causes and resolutions

**Tools used:**
- pgvector similarity search over documents
- Service Catalog tool (ownership lookup - structured query, not RAG)
- Phase 2: past incident embedding search (same pgvector store, separate collection)

**Why separate:** RAG retrieval is architecturally distinct from operational data query. The Knowledge Agent talks to pgvector and a document store; the others talk to monitoring and deployment APIs. More importantly, it is the only agent that improves over time (Phase 2: incident memory). Isolation means that upgrade is localized.

**Typical invocation pattern:** Called early (before full telemetry) to get runbook context that shapes what queries to run; called again after a hypothesis forms, to cross-check against known patterns. Two invocations per investigation is typical.

**Bootstrap requirement:** The Orion Commerce document corpus (runbooks, postmortems, architecture docs) must be created as part of the synthetic environment setup. This is non-trivial - 15–20 documents minimum for meaningful retrieval.

---

## Agent 5: Incident Synthesizer

**Single responsibility:** Given a complete evidence set, produce ranked root-cause hypotheses with supporting and contradicting evidence, confidence scores, and recommended next actions.

**Inputs:**
- Complete `InvestigationState`: timeline, telemetry findings, deployment findings, knowledge context, service topology snapshot
- Called exactly once per investigation, by the Planner when investigation is complete
- `synthesis_mode: Literal["final"]` - enforces single-call contract

**Outputs:**
- `SynthesisOutput`:
  - `hypotheses: list[Hypothesis]` - ranked by confidence descending
  - `top_hypothesis: Hypothesis`
  - `investigation_summary: str` - human-readable narrative
  - `timeline: list[TimelineEvent]` - formatted for output
  - `requires_escalation: bool`
  - `escalation_reason: str | None`
  - Phase 2: `incident_memory_record: IncidentMemoryRecord | None`

**Hypothesis schema:**
```
hypothesis_id: str
description: str
root_cause_category: Literal["deployment", "dependency", "resource", "configuration", "infrastructure", "unknown"]
affected_service: str
confidence_pct: float          # 0.0–100.0
supporting_evidence: list[str] # human-readable citations to findings
contradicting_evidence: list[str]
recommended_action: str
authority_level: Literal["L1", "L2", "L3"]
```

**Why not called mid-loop:** The Planner maintains an informal `planner_working_hypothesis` for loop termination. The Synthesizer produces the formal, structured, evidence-grounded output exactly once. Calling it mid-loop would make it an expensive repeated operation and blur the Planner/Synthesizer responsibility boundary.

**Coordination cost:** None after the first call - it is a terminal reasoning node. Cost is front-loaded: the Planner must have assembled a complete state before calling it.

---

## Agent 6: Response Agent

**Single responsibility:** Format the Synthesizer's validated output and dispatch it to external communication and ticketing systems appropriate to the incident's authority level.

**Inputs:**
- `SynthesisOutput` (must have passed Safety Guard validation)
- `ValidationResult` - `passed: True` required to proceed
- `service_ownership: ServiceOwnership` - from Planner state via Service Catalog
- `pending_approvals: list[PendingApproval]` - which Level 3 actions are approved

**Outputs:**
- `DispatchedActions`: list of actions completed (Jira ticket ID, Slack message timestamp, PagerDuty incident ID)
- `PendingApprovals`: Level 3 actions awaiting human approval - triggers checkpoint + suspend
- `HumanReadableSummary`: formatted incident report (owner, escalation path, evidence, recommended remediation)

**Authority-level dispatch:**
- L1: dispatch automatically
- L2: dispatch automatically or with quick-approve (configurable per action type)
- L3: write to `pending_approvals`, checkpoint graph state, suspend. Resume via FastAPI approval endpoint.

**What it does NOT do:** Remediation reasoning. `recommended_action` comes from the Synthesizer's `top_hypothesis`. The Response Agent formats and routes that recommendation - it does not generate it.

**Tools used:**
- Jira API (simulated in Phase 1)
- Slack webhook (simulated in Phase 1)
- PagerDuty API (simulated in Phase 1)
- Cloud SQL (write approval records, checkpoint references)

---

## Agent 7: Evidence and Safety Guard

**Single responsibility:** Validate that the Synthesizer's output is evidence-grounded, confidence-calibrated, and that proposed actions are within the appropriate authority level - before dispatch.

**Position in graph:** Between Synthesizer and Response Agent. Always executes; cannot be bypassed.

**Inputs:**
- `SynthesisOutput`
- `InvestigationState` - to verify that evidence citations refer to real findings in state
- Authority level policy (static configuration)

**Outputs:**
- `ValidationResult`:
  ```
  passed: bool
  issues: list[str]
  risk_level: Literal["L1", "L2", "L3"]
  unsupported_claims: list[str]   # claims not backed by findings in state
  confidence_calibration_ok: bool # confidence reasonable given evidence count?
  action_authority_ok: bool       # recommended action matches stated authority level?
  ```
- If `passed: False`:
  - Routes back to Planner with `issues` list for additional investigation
  - If `budget.iterations_remaining == 0`: escalates rather than loops

**Why routing on failure goes to Planner, not Synthesizer:** A failed validation typically indicates missing evidence (the Synthesizer made a claim it couldn't support), not poor reasoning over complete evidence. The Synthesizer cannot invent evidence. Only the Planner can invoke additional specialists. See [ADR-003](decisions/ADR-003-safety-guard-routing.md).

**What this is not:** An offline evaluator. This runs on every live investigation. The offline eval harness is a separate system. See [ADR-006](decisions/ADR-006-evaluator-split.md).

---

## Investigation budget

Each investigation runs against a configurable budget. The Planner evaluates remaining budget at every iteration.

```python
class InvestigationBudget(BaseModel):
    max_iterations: int = 5
    max_tool_calls: int = 20
    max_tokens: int = 50_000
    max_latency_seconds: float = 30.0
    confidence_threshold: float = 0.90

    iterations_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
```

**Early termination:** If `planner_working_confidence ≥ confidence_threshold`, the Planner stops and hands off to the Synthesizer without exhausting the budget. This reduces cost on straightforward incidents.

See [ADR-008](decisions/ADR-008-investigation-budget.md).
