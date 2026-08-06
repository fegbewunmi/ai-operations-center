# Architecture Rationale

_Why we made the decisions we made - answers for design reviews and interviews._

This document explains the non-obvious decisions behind the AI Operations Center. The ADRs in `docs/decisions/` capture the formal record of each decision; this document captures the reasoning behind them in plain language.

---

## 1. Why LangGraph instead of a simpler orchestration approach?

The tempting design is a fixed pipeline: call telemetry, call deployment, call knowledge, then synthesize. This works for a demo but breaks for real incidents, because you don't know upfront which specialists you need or in what order.

A failed deployment calls for: deployment agent first (confirm the timing), telemetry second (confirm the error spike correlated with the deploy). Calling knowledge is optional and wasteful.

A latency regression with no recent deployment calls for: telemetry first (establish the pattern), knowledge second (look up the runbook), and deployment only to rule it out.

LangGraph's `StateGraph` models this correctly as a conditional state machine: the planner decides what to call next based on what it has already learned. Each iteration accumulates evidence, and the planner re-evaluates. Three properties of LangGraph made it the right choice:

**Typed state with `operator.add` reducers.** Without this, returning `{"telemetry_findings": [...]}` from a node would overwrite the state, losing evidence from earlier iterations. The `Annotated[list[T], operator.add]` reducer appends instead. The planner at iteration 3 sees all evidence from iterations 1 and 2 - no accumulation logic needed.

**Built-in checkpointing.** The `AsyncPostgresSaver` persists full graph state to Cloud SQL after every node. This is what makes human approval possible: the investigation pauses at an L3 action, a PendingApproval record is created, and when a human POSTs to `/approval`, the graph resumes from the exact checkpoint. The LLM reasoning is preserved across the pause.

**Conditional edges without ceremony.** The routing logic in `route_from_planner` and `route_from_safety_guard` is plain Python that returns a string. The graph wiring is declarative. Adding a new specialist agent is a `builder.add_node` + `builder.add_edge` + one new Literal in the planner's schema - no framework-specific hook to implement.

---

## 2. Why Cloud SQL + pgvector instead of Pinecone or a dedicated vector database?

We're already running Cloud SQL for operational data: incidents, investigations, deployments, services, and service ownership. Adding pgvector to the same Postgres instance means one fewer dependency in production - no Pinecone API key, no additional failure mode, no data residency concern, no added cost.

The corpus is small: eleven documents at seed time, growing toward hundreds as incident memory accumulates. An ivfflat index handles this comfortably. The query latency is well within the budget for an asynchronous investigation.

The more important reason is transactional consistency. When the dispatcher writes an incident_memory record after a completed investigation, it's in the same database as the investigation status update. A single commit covers both. With a dedicated vector store, you'd need distributed coordination: either a two-phase write or eventual consistency, both of which add complexity for a problem that doesn't exist at this scale.

The migration path to a dedicated vector database is clear: change the `_search_documents` function in the knowledge agent and the `_write_incident_memory` function in the dispatcher. Everything else - the schema of what gets written and retrieved - stays the same.

---

## 3. Why the Gemini API directly instead of Vertex AI?

The architecture isolates the LLM provider behind a single abstraction point: `ChatGoogleGenerativeAI` in `langchain-google-genai`. Migrating to Vertex AI is a change in one config variable, one import, and one environment setup step - the graph, state, agents, and prompts are untouched.

The decision to start on the Gemini API was operational: during development, it eliminates service account setup, IAM bindings, VPC configuration, and regional endpoint selection. Getting to a working demo that exercises the full reasoning pipeline is more valuable than premature operational excellence. Portfolio projects often fail because setup complexity blocks progress.

The ADR at `docs/decisions/ADR-001-service-catalog-storage.md` documents the six-step migration plan to Vertex AI. Documenting this as an explicit trade-off - not pretending we started on Vertex AI - is stronger than hiding it, because it shows we understand the production path.

---

## 4. Why does the planner loop?

The planner is the only node that decides what happens next. It runs at the start of every iteration, reads the full accumulated state, and returns one of three decisions: invoke a specialist, synthesize, or escalate.

The loop runs until one of three termination conditions:
1. The planner decides it has enough evidence and chooses to synthesize.
2. The planner decides the situation is unresolvable and escalates.
3. The investigation budget is exhausted (max iterations, max tool calls).

The budget is the safety valve. Without it, a poorly-worded incident or an underinstrumented service could cause infinite loops. With it, the worst case is a spent budget and an escalation - never an infinite loop.

The feedback loop from the safety guard to the planner is a second form of looping: if the safety guard rejects the synthesis, the planner doesn't terminate - it gets the validation issues added to its context and is forced to gather additional evidence before trying again. This is what makes the system self-correcting.

---

## 5. Why does the Safety Guard exist as a separate node?

The original design routed directly from synthesizer to the dispatcher. The problem: the synthesizer is an LLM, and LLMs can produce outputs that are confident and wrong - a hypothesis that cites evidence it invented, a recommended action that exceeds what an automated system should be allowed to do unilaterally.

The safety guard sits between synthesis and dispatch and runs four deterministic checks with no LLM calls:

1. **Confidence** - does the top hypothesis meet the configured threshold?
2. **Evidence grounding** - does it have at least two substantive supporting items?
3. **Source alignment** - is the hypothesis category consistent with the evidence actually gathered? (A deployment root cause claim without the deployment agent having been invoked is suspicious.)
4. **Authority** - does an L3 action carry a specific directive?

The routing on failure is intentional: the safety guard sends the planner back to gather more evidence, not to END. The issues are written into the planner's context on the next iteration with an explicit instruction not to synthesize at the same confidence level. This creates a loop that terminates either when the synthesis passes validation or when the budget is exhausted.

Making the safety guard pure logic (no LLM) means it's deterministic, testable without network calls, and fast. If validation required another LLM call, it would be slower, more expensive, and harder to reason about.

---

## 6. Why is human approval built into the core graph?

The authority level system (L1/L2/L3) exists because an automated system should not act unilaterally on high-stakes actions - rolling back a database migration, taking a service offline, paging an executive.

The pattern avoids the naive alternatives:

**Naive alternative 1: Always require human approval.** This defeats the purpose of automation for L1 and L2 actions (service restarts, cache flushes, alert acknowledgements) where speed matters.

**Naive alternative 2: Humans approve at the end.** If the human gets a summary and a list of recommended actions, they've lost the investigation context. The graph's state - all evidence, timeline, hypotheses - is already in Cloud SQL. The approval workflow gives the human access to that context, not just the final recommendation.

The LangGraph interrupt/resume mechanism is what makes this tractable. The investigation checkpoints after creating the PendingApproval record. The FastAPI `/approval` endpoint resumes the graph from that checkpoint. The state is preserved exactly as it was - no re-investigation, no context loss.

---

## 7. Why is the Service Catalog a data source rather than an agent?

The service catalog - topology, ownership, on-call routing - is static configuration that the planner reads once per investigation. It doesn't reason; it returns a lookup result.

Making it an agent would mean an LLM call to answer a deterministic question: "what services depend on payments?" The topology is a graph stored in `services` and `service_dependencies`. The answer is a SQL query.

The planner calls `_fetch_topology` at the start of each iteration. Knowing the dependency graph before deciding which specialist to invoke is what lets it reason about blast radius: "payments is failing, orders depends on payments, this is likely a cascade - call the deployment agent for payments first."

The catalog belongs in the infrastructure layer, not the agent layer. Agents reason; catalogs respond.

---

## 8. Why Incident Memory in the data model but not in Phase 1 behavior?

Incident memory creates a chicken-and-egg dependency: you need completed investigations to populate it, and it becomes useful only after enough investigations have run. Blocking Phase 1 on it would mean the system couldn't be demonstrated until it had already investigated many incidents.

The Phase 1 decision was to design the schema and write path now, defer the read path (retrieval in the knowledge agent) to Phase 2. The `incident_memory` table exists in the migration. The `_write_incident_memory` function exists in the dispatcher and fires after every L1/L2 resolution. The knowledge agent's `_search_documents` function queries `documents` - when Phase 2 adds incident_memory retrieval, it adds a second query to the same function.

This is the "design for Phase 2, implement for Phase 1" principle. The schema debt is zero. The implementation debt is one additional query.

---

## 9. Why Planner → Incident Analysis → Synthesizer instead of Planner → Synthesizer?

The original design had the synthesizer doing two jobs: reasoning about what the evidence means, and writing a human-readable summary of that reasoning. These are different cognitive tasks that benefit from different prompts.

Separating them gives each node a single responsibility:

**Incident Analysis:** Given all accumulated evidence, produce ranked hypotheses with supporting and contradicting evidence. This is pure reasoning - the output is structured (`AnalysisOutput`), validated by Pydantic, and contains no prose.

**Synthesizer:** Given the ranked hypotheses from incident_analysis, write a human-readable investigation summary. The synthesizer never re-examines raw evidence. It formats what the analysis node already reasoned.

The safety guard then validates the analysis output's structure and grounding. If it rejects, the planner gets more evidence before the next analysis pass. The synthesizer is only invoked again after analysis clears.

The practical consequence: the synthesizer's job is straightforward and its output quality is predictable. The analysis node is where the hard reasoning happens.

---

## 10. Why `asyncio.create_task` for background execution?

The API returns 202 immediately and runs the investigation in the background. Two options:

**`asyncio.create_task`** - simple, works within the FastAPI event loop, no additional infrastructure.

**Cloud Tasks** - durable, survives process restarts, supports retries and dead-letter queues.

We chose `asyncio.create_task` for the same reason we chose the Gemini API directly: remove infrastructure friction during development. The investigation graph already persists state to Cloud SQL via LangGraph checkpointing, so the state isn't lost even if the process restarts - only the in-progress execution is lost.

The production hardening path is: replace `asyncio.create_task(run_investigation(...))` in the API handler with a Cloud Tasks enqueue, and add a Cloud Tasks handler endpoint that calls `run_investigation`. The investigation logic is unchanged.

This is documented in the roadmap. It's not a hidden debt - it's a deliberate sequencing decision.
